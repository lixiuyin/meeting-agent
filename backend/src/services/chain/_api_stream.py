"""Streaming pipeline internals for the RAG chain."""

import asyncio
import contextlib
import time
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from ...core.config import settings
from ...core.exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMCircuitBreakerError,
    LLMConfigError,
    LLMContextWindowError,
    LLMRateLimitError,
    LLMTimeoutError,
    map_error,
)
from ...core.metrics import LLM_REQUEST_DURATION, LLM_REQUEST_TOTAL, RAG_DOCS_AT_STAGE
from ...core.trace import write_pipeline_log
from ..llm import get_llm, get_rag_prompt, get_skill_prompt
from ..llm._parsing import StreamingThinkingFilter, strip_thinking_blocks
from ..traffic_control import traffic_controller as _tc
from ._anthropic_cache import apply_anthropic_cache_control
from ._common import logger
from ._context import PipelineContext
from ._fallback import is_fallback_circuit_open, record_fallback_failure, record_fallback_success
from ._formatting import (
    _extract_sources,
    extract_image_urls_from_docs,
    is_visual_query,
    load_image_as_base64_url,
)
from ._generate_helpers import (
    _invoke_chain_with_retry,
    _invoke_chain_with_retry_multimodal,
    _strip_internal_tokens,
)
from ._routing import _is_trivially_short

# Named constants for streaming timeouts and intervals
_PRE_TOKEN_HEARTBEAT_TIMEOUT_S = 8.0
_ACLOSE_TIMEOUT_S = 3.0


def _stream_user_error_message(exc: Exception) -> dict:
    """Convert internal exceptions to structured stream error payloads.

    Returns a dict with keys: message, code, detail, exception_type.
    """
    mapped = map_error(exc)

    if isinstance(mapped, LLMCircuitBreakerError):
        return {
            "message": (
                "LLM is temporarily unavailable due to repeated failures. Please retry in a moment."
            ),
            "code": "LLM_CIRCUIT_OPEN",
            "detail": None,
            "exception_type": None,
        }
    if isinstance(mapped, (LLMAuthenticationError, LLMConfigError)):
        return {
            "message": "LLM configuration is invalid. Please check API key and model settings.",
            "code": "LLM_AUTH_ERROR",
            "detail": None,
            "exception_type": None,
        }
    if isinstance(mapped, LLMRateLimitError):
        return {
            "message": "LLM rate limit reached. Please retry in a moment.",
            "code": "LLM_RATELIMIT",
            "detail": None,
            "exception_type": None,
        }
    if isinstance(mapped, LLMTimeoutError):
        return {
            "message": "LLM request timed out. Please retry.",
            "code": "RETRIABLE_TIMEOUT",
            "detail": None,
            "exception_type": None,
        }
    if isinstance(mapped, LLMContextWindowError):
        return {
            "message": "Request is too large for the current model context window.",
            "code": "LLM_CONTEXT_WINDOW",
            "detail": None,
            "exception_type": None,
        }
    if isinstance(mapped, LLMAPIError) and mapped.status_code is not None:
        return {
            "message": "LLM provider is temporarily unavailable. Please retry later.",
            "code": f"LLM_HTTP_{mapped.status_code}",
            "detail": None,
            "exception_type": None,
        }
    return {
        "message": "Internal error",
        "code": "INTERNAL",
        "detail": None,
        "exception_type": None,
    }


async def _emit_stream(bus: Any, ctx: PipelineContext, stream_iter: Any) -> str:
    """Consume an LLM async iterator, emit tokens to the bus, and return accumulated text."""
    accumulated = ""
    first_token_seen = asyncio.Event()
    ttft_finished = False
    thinking_filter = StreamingThinkingFilter()

    async def _pre_token_heartbeat() -> None:
        try:
            while not first_token_seen.is_set():
                try:
                    await asyncio.wait_for(
                        first_token_seen.wait(),
                        timeout=_PRE_TOKEN_HEARTBEAT_TIMEOUT_S,
                    )
                except TimeoutError:
                    bus.emit_heartbeat()
        except asyncio.CancelledError:
            pass

    _gen = stream_iter

    async with AsyncExitStack() as cleanup_stack:
        # Register cleanup callback *before* creating the heartbeat task so
        # the task is always cancelled even if creation itself raises (C-H3).
        hb_task: asyncio.Task | None = None

        async def _cancel_heartbeat() -> None:
            if hb_task is not None:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning("LLM heartbeat task exited with error", exc_info=True)

        cleanup_stack.push_async_callback(_cancel_heartbeat)
        hb_task = asyncio.create_task(_pre_token_heartbeat())

        try:
            async for event in _gen:
                token = getattr(event, "content", str(event))
                first_token_seen.set()
                if not ttft_finished:
                    ctx.trace.finish_span("llm_ttft")
                    ctx.trace.start_span(
                        "llm_streaming",
                        "generate",
                        parent_label="generate_answer",
                    )
                    ttft_finished = True
                accumulated += token
                clean = thinking_filter.feed(token)
                if clean is not None:
                    bus.emit_token(clean)
        finally:
            remaining = thinking_filter.flush()
            if remaining:
                bus.emit_token(remaining)
            first_token_seen.set()
            if hasattr(_gen, "aclose"):
                try:
                    await asyncio.wait_for(_gen.aclose(), timeout=_ACLOSE_TIMEOUT_S)
                except TimeoutError:
                    # H-4: aclose timed out — the generator may still be running
                    # (e.g. slow LLM provider).  Calling athrow(GeneratorExit) on
                    # an already-running generator raises RuntimeError which we'd
                    # just suppress anyway.  Instead, log and move on — the
                    # underlying httpx/LangChain resources will be released when
                    # the provider eventually times out or the task is cancelled.
                    logger.warning(
                        "aclose timed out after %ds; generator left for "
                        "provider timeout/cancellation",
                        _ACLOSE_TIMEOUT_S,
                    )
                except Exception:
                    logger.warning("aclose raised unexpected error", exc_info=True)

    return accumulated


async def _complete_casual_stream_short_circuit(
    bus: Any,
    ctx: PipelineContext,
    question: str,
    _api_mod: Any,
) -> None:
    """Emit casual response for streaming: session, token, persist, trace, done.

    Used when intent is casual or the question is trivially short small-talk.
    """
    ctx.trace.start_span("pipeline", "pipeline", skipped=True)
    ctx.trace.start_span("skill_match", "skill", skipped=True)
    ctx.trace.finish_span("skill_match")
    await asyncio.to_thread(_api_mod.ensure_session, ctx)
    assert ctx.session_id is not None
    response = _api_mod._casual_response(question)
    bus.emit_token(response)
    ctx.answer = response
    await asyncio.to_thread(_api_mod.save_messages, ctx)
    ctx.trace.finish_span("pipeline")
    bus.emit_trace(ctx.trace.to_dict())
    bus.emit_done(ctx.session_id)
    write_pipeline_log(ctx.trace, question, session_id=ctx.session_id)


async def _run_stream_pipeline(
    bus: Any,
    ctx: PipelineContext,
    question: str,
) -> None:
    """Run the full streaming pipeline, emitting events to the bus as they arrive."""
    from . import _api as _api_mod

    persisted = False
    extraction_scheduled = False
    _hb_stop = asyncio.Event()
    skill_task: asyncio.Task[Any] | None = None  # hoisted for cancel-on-exception

    def _persist_response() -> None:
        nonlocal persisted
        if persisted or not ctx.session_id or not ctx.answer:
            return
        _api_mod.save_messages(ctx)
        persisted = True

    def _schedule_fact_extraction_if_needed() -> None:
        nonlocal extraction_scheduled
        if extraction_scheduled or not persisted:
            return
        _api_mod.schedule_fact_extraction(ctx)
        extraction_scheduled = True

    async def _global_heartbeat() -> None:
        try:
            while not _hb_stop.is_set():
                try:
                    await asyncio.wait_for(_hb_stop.wait(), timeout=5.0)
                except TimeoutError:
                    bus.emit_heartbeat()
        except asyncio.CancelledError:
            pass

    hb_task = asyncio.create_task(_global_heartbeat())
    try:
        bus.emit_step("accepted", "start")
        # Query routing for streaming
        ctx.trace.start_span("classify_intent", "routing")
        intent = _api_mod._classify_intent(question)
        ctx.trace.finish_span("classify_intent")
        if intent == "casual" or _is_trivially_short(question):
            await _complete_casual_stream_short_circuit(bus, ctx, question, _api_mod)
            return

        # Parallelize: skill_match + session + LLM init + query rewrite
        async def _do_skill_match():
            if not settings.SKILL_MATCHING_ENABLED:
                ctx.trace.start_span("skill_match", "skill", skipped=True)
                ctx.trace.finish_span("skill_match")
                return None
            loader = _api_mod._get_skill_loader()
            summaries = loader.load_summaries() if hasattr(loader, "load_summaries") else []
            if not summaries:
                ctx.trace.start_span("skill_match", "skill", skipped=True)
                ctx.trace.finish_span("skill_match")
                return None
            ctx.trace.start_span("skill_match", "skill")
            try:
                try:
                    return await asyncio.wait_for(
                        _api_mod._get_skill_matcher().match(question, summaries),
                        timeout=settings.SKILL_MATCH_TIMEOUT_S,
                    )
                except TimeoutError:
                    logger.warning(
                        "skill_match timed out after %.1fs, skipping",
                        settings.SKILL_MATCH_TIMEOUT_S,
                    )
                    return None
            finally:
                ctx.trace.finish_span("skill_match")

        async def _do_session():
            bus.emit_step("session", "start")
            await asyncio.to_thread(_api_mod.ensure_session, ctx)
            assert ctx.session_id is not None
            bus.emit_step("session", "done")

        # Resolve singletons once at pipeline entry (B2 ctx injection):
        # storing them in ctx guarantees a consistent instance for every step
        # even if settings change (reset_llm / reset_embeddings) mid-request.
        from ..embedder import get_embeddings

        ctx.llm = await asyncio.to_thread(get_llm)
        ctx.embeddings = await asyncio.to_thread(get_embeddings)
        skill_task = asyncio.create_task(_do_skill_match())  # assigns outer var
        session_task = asyncio.create_task(_do_session())

        # Preload history once so both rewrite_query_step and load_history
        # share the same DB read instead of fetching independently (PERF-1).
        await session_task  # ensures ctx.session_id is set
        if ctx.session_id:
            try:
                from ..memory import get_session_history

                history = await asyncio.to_thread(get_session_history, ctx.session_id)
                ctx.raw_history_messages = list(history.messages)
            except Exception:
                logger.debug("History preload failed; steps will load independently", exc_info=True)

        # Query rewrite (reuses preloaded history from ctx.raw_history_messages).
        await _api_mod.rewrite_query_step(ctx)

        # Warm the query-embedding cache so the parallel context-loading
        # branches below all share a single embedding API call (otherwise each
        # branch races into ``embed_query`` and slow providers cause repeated
        # ``context step ... timed out`` warnings).
        await _api_mod._prewarm_query_embedding(ctx)

        # Steps 3-8: parallel retrieval + context loading. skill_task runs
        # alongside and is consumed just before generate_answer below.
        async def _retrieve_branch() -> None:
            await _api_mod.retrieve_documents(ctx)
            _api_mod.pre_rerank_dedup(ctx)
            await asyncio.to_thread(_api_mod.rerank_documents, ctx)
            await asyncio.to_thread(_api_mod.suppress_near_duplicates, ctx)

        ctx.trace.start_span("pipeline", "pipeline")
        bus.emit_step("pipeline", "start")
        context_timeout = _api_mod._context_branch_timeout(ctx)
        await asyncio.gather(
            _retrieve_branch(),
            _api_mod._best_effort("memories", _api_mod.load_memories(ctx), context_timeout),
            _api_mod._best_effort("session", _api_mod.load_session_context(ctx), context_timeout),
            _api_mod._best_effort("entity", _api_mod.load_entity_context(ctx), context_timeout),
            _api_mod._best_effort(
                "web", _api_mod.perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S
            ),
            # H-7: load_history is non-critical context — wrap in _best_effort
            # to prevent its failure from cancelling other gather branches.
            _api_mod._best_effort("history", _api_mod.load_history(ctx), context_timeout, ctx=ctx),
        )

        bus.emit_step("pipeline", "done")

        # Resolve skill match — usually already complete by now.
        skill_definition = None
        try:
            match = await skill_task
        except Exception:
            logger.warning("skill_task failed in stream, continuing without skill", exc_info=True)
            match = None
        if match and getattr(match, "matched", False):
            try:
                loader = _api_mod._get_skill_loader()
                full = loader.get_full(match.skill.name)
                skill_definition = full.model_dump() if full else None
                ctx.skill_name = match.skill.name
                ctx.skill_confidence = float(match.score)
            except Exception:
                logger.warning("skill definition resolution failed in stream", exc_info=True)
                skill_definition = None

        await asyncio.to_thread(_api_mod.build_context, ctx)
        RAG_DOCS_AT_STAGE.labels(stage="truncated").observe(len(ctx.docs))

        # Emit sources before streaming tokens
        sources = _extract_sources(ctx.docs, max_sources=len(ctx.docs))
        if sources:
            bus.emit_sources(sources)

        if ctx.web_results:
            bus.emit_web_results(ctx.web_results)

        # Build the chain
        ctx.trace.start_span("generate_answer", "generate")
        ctx.trace.start_span("llm_ttft", "generate", parent_label="generate_answer")
        bus.emit_step("generate", "start")
        # Use a local variable instead of mutating ctx.llm to avoid races
        # with parallel tasks (skill_task, session_task) that also access
        # the shared context (C-H5).
        llm = ctx.llm if ctx.llm is not None else await asyncio.to_thread(get_llm)
        assert llm is not None  # guaranteed by the guard above
        prompt = get_skill_prompt(skill_definition) if skill_definition else get_rag_prompt()
        chain = prompt | RunnableLambda(apply_anthropic_cache_control) | llm

        # Multimodal: inject images when docs contain images AND the query
        # plausibly asks the model to look at visual content. Attaching large
        # base64 images to text-only questions costs significant tokens.
        _stream_images: list[dict] = []
        _attach_stream_images = True
        if settings.MULTIMODAL_ATTACH_GATE_ENABLED:
            _attach_stream_images = is_visual_query(ctx.question) or is_visual_query(
                ctx.rewritten_query
            )
        if ctx.docs and _attach_stream_images:
            image_infos = extract_image_urls_from_docs(ctx.docs)
            for info in image_infos:
                storage_path = info.get("storage_path")
                if not isinstance(storage_path, str):
                    continue
                data_url = load_image_as_base64_url(storage_path)
                if data_url:
                    _stream_images.append({"url": data_url, "source_index": info["source_index"]})
            if _stream_images:
                logger.info(
                    "Streaming multimodal: %d images attached",
                    len(_stream_images),
                )

        # Wrap streaming LLM call with traffic controller
        _tc_cm = _tc if _tc is not None else None

        # Metrics instrumentation
        _llm_provider = settings.LLM_BINDING
        _llm_status = "success"
        _llm_start = time.monotonic()

        # Build stream inputs (possibly with multimodal image injection)
        _stream_inputs = {
            "context": ctx.combined_context,
            "question": ctx.question,
            "history": ctx.history_messages,
            "memory_context": "",
        }

        async def _do_stream() -> str:
            if _stream_images:
                try:
                    prompt_value = prompt.invoke(_stream_inputs)
                    messages = prompt_value.to_messages()
                    image_blocks = [
                        {"type": "image_url", "image_url": {"url": img["url"]}}
                        for img in _stream_images
                    ]
                    for idx in range(len(messages) - 1, -1, -1):
                        if isinstance(messages[idx], HumanMessage):
                            orig = messages[idx].content
                            if isinstance(orig, str):
                                messages[idx] = HumanMessage(
                                    content=[{"type": "text", "text": orig}, *image_blocks],
                                    additional_kwargs=messages[idx].additional_kwargs,
                                )
                            else:
                                messages[idx] = HumanMessage(
                                    content=[*orig, *image_blocks],
                                    additional_kwargs=messages[idx].additional_kwargs,
                                )
                            break
                    return await _emit_stream(bus, ctx, llm.astream(messages))
                except Exception:
                    logger.warning(
                        "Multimodal streaming failed, falling back to text-only streaming",
                        exc_info=True,
                    )
                    return await _emit_stream(bus, ctx, chain.astream(_stream_inputs))
            return await _emit_stream(bus, ctx, chain.astream(_stream_inputs))

        # Stream tokens
        accumulated = ""

        try:
            if _tc_cm is not None:
                async with _tc_cm:
                    try:
                        accumulated = await _do_stream()
                        _tc_cm.record_success()
                    except Exception:
                        _tc_cm.record_failure()
                        raise
            else:
                accumulated = await _do_stream()
        except Exception as exc:
            _llm_status = "error"
            logger.warning("Streaming failed, falling back to non-streaming", exc_info=True)
            if is_fallback_circuit_open():
                raise RuntimeError("Fallback temporarily disabled due repeated failures") from exc
            full_chain = (
                prompt | RunnableLambda(apply_anthropic_cache_control) | llm | StrOutputParser()
            )
            fallback_inputs = {
                "context": ctx.combined_context,
                "question": ctx.question,
                "history": ctx.history_messages,
                "memory_context": "",
            }
            last_err: Exception | None = None
            for _ in range(2):
                try:
                    if _stream_images:
                        try:
                            accumulated = await asyncio.to_thread(
                                _invoke_chain_with_retry_multimodal,
                                full_chain,
                                fallback_inputs,
                                _stream_images,
                            )
                        except Exception:
                            logger.warning(
                                "Multimodal fallback failed, retrying text-only",
                                exc_info=True,
                            )
                            accumulated = await asyncio.to_thread(
                                _invoke_chain_with_retry,
                                full_chain,
                                fallback_inputs,
                            )
                    else:
                        accumulated = await asyncio.to_thread(
                            _invoke_chain_with_retry,
                            full_chain,
                            fallback_inputs,
                        )
                    try:
                        record_fallback_success()
                    except Exception:
                        logger.debug("Fallback success metric recording failed", exc_info=True)
                    break
                except Exception as exc:
                    last_err = exc
            else:
                record_fallback_failure()
                raise RuntimeError("Fallback non-streaming generation failed") from last_err
            bus.emit_token(_strip_internal_tokens(strip_thinking_blocks(accumulated)))
        finally:
            try:
                LLM_REQUEST_DURATION.labels(provider=_llm_provider).observe(
                    time.monotonic() - _llm_start
                )
                LLM_REQUEST_TOTAL.labels(provider=_llm_provider, status=_llm_status).inc()
            except Exception:
                logger.debug("Metrics recording failed", exc_info=True)

        bus.emit_step("generate", "done")

        # Save final answer and messages
        ctx.answer = _strip_internal_tokens(strip_thinking_blocks(accumulated))
        ctx.trace.finish_span("llm_streaming")
        ctx.trace.finish_span("generate_answer")
        await asyncio.to_thread(_persist_response)
        _schedule_fact_extraction_if_needed()
        from ...services.memory._service._crud import flush_pending_touches

        await asyncio.to_thread(flush_pending_touches)
        ctx.trace.finish_span("pipeline")

        # Emit trace and done
        bus.emit_trace(ctx.trace.to_dict())
        bus.emit_done(ctx.session_id)
        write_pipeline_log(ctx.trace, question, session_id=ctx.session_id)
    except asyncio.CancelledError:
        if skill_task is not None and not skill_task.done():
            skill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await skill_task
        try:
            if ctx.answer and ctx.session_id:
                await asyncio.to_thread(_persist_response)
                _schedule_fact_extraction_if_needed()
                from ...services.memory._service._crud import flush_pending_touches

                await asyncio.to_thread(flush_pending_touches)
        except Exception:
            logger.error("Failed to persist cancelled stream result", exc_info=True)
        raise
    except Exception as exc:
        if skill_task is not None and not skill_task.done():
            skill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await skill_task
        logger.error("Stream pipeline failed", exc_info=True)
        ctx.trace.finish_span("llm_streaming", "error", error=exc)
        ctx.trace.finish_span("llm_ttft", "error", error=exc)
        ctx.trace.finish_span("generate_answer", "error", error=exc)
        ctx.trace.finish_span("pipeline", "error", error=exc)
        err_info = _stream_user_error_message(exc)
        bus.emit_error(
            err_info["message"],
            code=err_info.get("code"),
            detail=err_info.get("detail"),
            exception_type=err_info.get("exception_type"),
        )
        bus.emit_trace(ctx.trace.to_dict())
        if not ctx.session_id:
            bus.emit_error(
                "Pipeline failed before session was established",
                code="NO_SESSION",
            )
            bus.emit_done("", error=True)
        else:
            bus.emit_done(ctx.session_id)
    finally:
        _hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Global heartbeat task exited with error", exc_info=True)
