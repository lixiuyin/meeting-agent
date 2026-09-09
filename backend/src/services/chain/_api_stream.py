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
    ContinuationSnapshotError,
    LLMAPIError,
    LLMAuthenticationError,
    LLMCircuitBreakerError,
    LLMConfigError,
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTransientResponseError,
    map_error,
)
from ...core.metrics import LLM_REQUEST_DURATION, LLM_REQUEST_TOTAL, RAG_DOCS_AT_STAGE
from ...core.trace import write_pipeline_log
from ..llm import get_llm, get_rag_prompt, get_skill_prompt
from ..llm._parsing import StreamingThinkingFilter, strip_thinking_blocks
from ..traffic_control import get_traffic_controller
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


def _should_skip_stream_rerank(ctx: PipelineContext) -> bool:
    """Keep latency-guarded fact lookups off the remote reranker."""
    return _uses_stream_latency_guard(ctx)


def _uses_stream_latency_guard(ctx: PipelineContext) -> bool:
    """Return whether this stream may trade optional recall for bounded latency.

    The guard is intentionally query-shaped rather than profile-shaped. A user
    selecting ``balanced`` should not make a short, self-contained fact lookup
    pay for remote embedding and reranking when the deployment has explicitly
    enabled its interactive latency guard. Analytical, follow-up, visual and
    web-search requests continue through the full path.
    """
    return bool(
        settings.CHAT_STREAM_LATENCY_GUARD_ENABLED
        and not ctx.use_web_search
        and ctx.web_search_mode == "off"
        and not is_visual_query(ctx.question)
        and ctx.fast_path_auto_selected
        and ctx.query_route_decision is not None
        and ctx.query_route_decision.fast_candidate
        and ctx.fast_path_evidence_decision is not None
        and ctx.fast_path_evidence_decision.safe
        and ctx.rag_mode == settings.RAG_FAST_PATH_RETRIEVAL_MODE
    )


def _normalise_stream_content(event: Any) -> str:
    """Extract only user-visible text from provider/LangChain stream chunks.

    OpenAI-compatible providers may return a string, a list of typed content
    blocks, or a mapping.  Reasoning blocks/metadata are deliberately ignored:
    hidden chain-of-thought must never become the fallback answer.
    """
    content = event if isinstance(event, str) else getattr(event, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") in {"reasoning", "thinking", "reasoning_content"}:
            return ""
        value = content.get("text", content.get("content", ""))
        return value if isinstance(value, str) else ""
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in {"reasoning", "thinking", "reasoning_content"}:
                    continue
                value = block.get("text", block.get("content", ""))
                if isinstance(value, str):
                    parts.append(value)
            else:
                value = getattr(block, "text", "")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


def _stream_user_error_message(exc: Exception) -> dict:
    """Convert internal exceptions to structured stream error payloads.

    Returns a dict with keys: message, code, detail, exception_type.
    """
    if isinstance(exc, ContinuationSnapshotError):
        return {
            "message": str(exc),
            "code": "SNAPSHOT_UNAVAILABLE",
            "detail": None,
            "exception_type": type(exc).__name__,
        }

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
    if isinstance(mapped, LLMEmptyResponseError):
        return {
            "message": "The model returned no usable answer. Please retry.",
            "code": "EMPTY_LLM_RESPONSE",
            "detail": None,
            "exception_type": type(mapped).__name__,
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


def _preserve_incomplete_stream(
    bus: Any,
    ctx: PipelineContext,
    *,
    evidence_sources: list[dict] | None = None,
) -> str:
    """Keep an emitted prefix or return a labelled, source-backed fallback.

    Source excerpts are allowed only when the caller has already established
    the conservative latency-guard query shape. They are explicitly presented
    as unsynthesized evidence rather than as a model-generated answer.
    """
    if not ctx.answer.strip():
        from ._steps_generate import _generation_timeout_message

        if evidence_sources:
            chinese = any("\u3400" <= char <= "\u9fff" for char in ctx.question)
            heading = (
                "回答生成超时。以下为检索到的原文摘录 (未经模型综合):"
                if chinese
                else "Answer generation timed out. Retrieved source excerpts (not synthesized):"
            )
            excerpts = []
            for index, source in enumerate(evidence_sources[:3], start=1):
                content = " ".join(str(source.get("content") or "").split())
                if content:
                    excerpts.append(f"[{index}] {content[:320].rstrip()}")
            ctx.answer = (
                "\n\n".join([heading, *excerpts])
                if excerpts
                else _generation_timeout_message(ctx.question)
            )
        else:
            ctx.answer = _generation_timeout_message(ctx.question)
        bus.emit_token(ctx.answer)
    ctx.degraded = True
    ctx.degradation_reason = "generation_timeout"
    bus.emit_status("degraded", reason=ctx.degradation_reason)
    return ctx.answer


async def _emit_stream(
    bus: Any,
    ctx: PipelineContext,
    stream_iter: Any,
    *,
    first_token_timeout_s: float | None = None,
    stall_timeout_s: float | None = None,
) -> str:
    """Consume an LLM stream with optional first-token and stall deadlines."""
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
            iterator = _gen.__aiter__()
            next_visible_deadline = (
                time.monotonic() + first_token_timeout_s
                if first_token_timeout_s is not None
                else None
            )
            while True:
                try:
                    if next_visible_deadline is None:
                        event = await iterator.__anext__()
                    else:
                        remaining_s = next_visible_deadline - time.monotonic()
                        if remaining_s <= 0:
                            raise TimeoutError("Visible-token deadline exhausted")
                        event = await asyncio.wait_for(
                            iterator.__anext__(),
                            timeout=remaining_s,
                        )
                except StopAsyncIteration:
                    break
                metadata = getattr(event, "response_metadata", {}) or {}
                if (
                    metadata.get("finish_reason") == "length"
                    or metadata.get("stop_reason") == "max_tokens"
                ):
                    ctx.degraded = True
                    ctx.degradation_reason = "output_limit"
                    bus.emit_status("degraded", reason=ctx.degradation_reason)
                token = _normalise_stream_content(event)
                if not token:
                    continue
                accumulated += token
                clean = thinking_filter.feed(token)
                if clean:
                    if not ttft_finished:
                        first_token_seen.set()
                        ctx.trace.finish_span("llm_ttft")
                        ctx.trace.start_span(
                            "llm_streaming",
                            "generate",
                            parent_label="generate_answer",
                        )
                        ttft_finished = True
                    ctx.answer += clean
                    bus.emit_token(clean)
                    next_visible_deadline = (
                        time.monotonic() + stall_timeout_s if stall_timeout_s is not None else None
                    )
        finally:
            remaining = thinking_filter.flush()
            if remaining:
                if not ttft_finished:
                    ctx.trace.finish_span("llm_ttft")
                    ctx.trace.start_span(
                        "llm_streaming",
                        "generate",
                        parent_label="generate_answer",
                    )
                    ttft_finished = True
                ctx.answer += remaining
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
    bus.emit_done(ctx.session_id, message_ids=ctx.saved_message_ids)
    write_pipeline_log(ctx.trace, question, session_id=ctx.session_id)


async def _run_stream_pipeline_inner(
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

    async def _schedule_fact_extraction_if_needed() -> None:
        nonlocal extraction_scheduled
        if extraction_scheduled or not persisted:
            return
        await _api_mod.schedule_fact_extraction(ctx)
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
        route_decision = ctx.query_route_decision
        ctx.trace.start_span(
            "classify_intent",
            "routing",
            query_route=route_decision.route if route_decision else "unknown",
            route_confidence=route_decision.confidence if route_decision else 0.0,
            route_reasons=list(route_decision.reasons) if route_decision else [],
        )
        intent = _api_mod._classify_intent(question)
        ctx.trace.finish_span("classify_intent")
        if intent == "casual" or _is_trivially_short(question):
            await _complete_casual_stream_short_circuit(bus, ctx, question, _api_mod)
            return

        # Parallelize: skill_match + session + LLM init + query rewrite
        async def _do_skill_match():
            from ._query_routes import is_recorded_fact_request

            if is_recorded_fact_request(question, ctx.memory_mode):
                ctx.trace.start_span("skill_match", "skill", skipped=True, reason="recorded_facts")
                ctx.trace.finish_span("skill_match")
                return None
            if (
                settings.RAG_FAST_PATH_ENABLED
                and ctx.query_route_decision
                and ctx.query_route_decision.fast_candidate
            ):
                ctx.trace.start_span("skill_match", "skill", skipped=True, reason="fast_query")
                ctx.trace.finish_span("skill_match")
                return None
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
            span = ctx.trace.start_span("skill_match", "skill")
            try:
                result = await asyncio.wait_for(
                    _api_mod._get_skill_matcher().match(question, summaries),
                    timeout=settings.SKILL_MATCH_TIMEOUT_S,
                )
                ctx.trace.finish_span("skill_match")
                return result
            except TimeoutError as exc:
                logger.warning(
                    "skill_match timed out after %.1fs, skipping",
                    settings.SKILL_MATCH_TIMEOUT_S,
                )
                span.metadata["outcome"] = "timeout_fallback"
                ctx.trace.finish_span("skill_match", "timeout", error=exc)
                return None
            except Exception as exc:
                ctx.trace.finish_span("skill_match", "error", error=exc)
                raise

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
        # Publish the same immutable scope before parallel readers as ask().
        await _api_mod.prepare_query_plan(ctx)

        # Warm the query-embedding cache so the parallel context-loading
        # branches below all share a single embedding API call (otherwise each
        # branch races into ``embed_query`` and slow providers cause repeated
        # ``context step ... timed out`` warnings).
        from ._query_routes import is_recorded_fact_request

        recorded_request = is_recorded_fact_request(question, ctx.memory_mode)
        if not recorded_request:
            await _api_mod._prewarm_query_embedding(ctx)

        # Steps 3-8: parallel retrieval + context loading. skill_task runs
        # alongside and is consumed just before generate_answer below.
        async def _retrieve_branch() -> None:
            requested_top_k = ctx.top_k
            await _api_mod.retrieve_documents(ctx)
            if ctx.snapshot_restored:
                # A saved evidence snapshot is immutable; deduplication and
                # reranking would silently mutate its historical ordering.
                return
            await _api_mod.validate_or_promote_fast_path(
                ctx,
                requested_top_k=requested_top_k,
            )
            _api_mod.pre_rerank_dedup(ctx)
            if _should_skip_stream_rerank(ctx):
                # A remote reranker routinely costs more than the complete
                # interactive budget.  Keep recall from the initial retrieval
                # and make the intentional quality/latency trade-off visible
                # in the trace instead of emitting a long running span.
                ctx.trace.start_span(
                    "rerank",
                    "rerank",
                    skipped=True,
                    reason="chat_latency_guard",
                    candidate_count=len(ctx.docs),
                    top_k=ctx.top_k,
                )
            else:
                await asyncio.to_thread(_api_mod.rerank_documents, ctx)
            await asyncio.to_thread(_api_mod.suppress_near_duplicates, ctx)

        ctx.trace.start_span("pipeline", "pipeline")
        bus.emit_step("pipeline", "start")
        context_timeout = _api_mod._context_branch_timeout(ctx)
        context_tasks = [
            _retrieve_branch(),
            _api_mod._best_effort("memories", _api_mod.load_memories(ctx), context_timeout),
            # H-7: load_history is non-critical context — wrap in _best_effort
            # to prevent its failure from cancelling other gather branches.
            _api_mod._best_effort("history", _api_mod.load_history(ctx), context_timeout, ctx=ctx),
        ]
        if not recorded_request:
            context_tasks.extend(
                [
                    _api_mod._best_effort(
                        "session", _api_mod.load_session_context(ctx), context_timeout
                    ),
                    _api_mod._best_effort(
                        "entity", _api_mod.load_entity_context(ctx), context_timeout
                    ),
                ]
            )
        await asyncio.gather(*context_tasks)
        if not recorded_request:
            await _api_mod._best_effort(
                "web", _api_mod.perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S
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

        _api_mod.assert_settings_epoch(ctx)
        await asyncio.to_thread(_api_mod.build_context, ctx)
        RAG_DOCS_AT_STAGE.labels(stage="truncated").observe(len(ctx.docs))

        # Prepare candidate source metadata.  It is emitted only after a valid
        # answer exists so failed/empty turns cannot masquerade as sourced
        # successes in the UI.
        sources = _extract_sources(
            ctx.docs, max_sources=len(ctx.docs), memory_sources=ctx.memory_sources
        )

        # Build the chain
        ctx.trace.start_span("generate_answer", "generate")
        ctx.trace.start_span("llm_ttft", "generate", parent_label="generate_answer")
        bus.emit_step("generate", "start")
        # Use a local variable instead of mutating ctx.llm to avoid races
        # with parallel tasks (skill_task, session_task) that also access
        # the shared context (C-H5).
        llm = ctx.llm if ctx.llm is not None else await asyncio.to_thread(get_llm)
        from ..llm._requested_output import bind_requested_output

        llm = bind_requested_output(llm, ctx.question)
        _latency_guarded = _uses_stream_latency_guard(ctx)
        if _latency_guarded:
            llm = llm.bind(max_tokens=settings.RAG_FAST_PATH_MAX_OUTPUT_TOKENS)
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
        _tc_cm = get_traffic_controller()

        # Metrics instrumentation
        _llm_provider = settings.LLM_BINDING
        _llm_status = "success"
        _llm_start = time.monotonic()
        _latency_guard = _latency_guarded
        _generation_timeout = (
            settings.RAG_FAST_PATH_TOTAL_TIMEOUT_S
            if _latency_guard
            else settings.LLM_GENERATION_TIMEOUT_S
        )
        _llm_deadline = _llm_start + _generation_timeout

        async def _await_with_generation_deadline(factory) -> Any:
            remaining = _llm_deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError(
                    "Generation deadline exhausted",
                    timeout=_generation_timeout,
                    provider=settings.LLM_BINDING,
                )
            try:
                return await asyncio.wait_for(factory(), timeout=remaining)
            except TimeoutError as exc:
                raise LLMTimeoutError(
                    "Generation deadline exhausted",
                    timeout=_generation_timeout,
                    provider=settings.LLM_BINDING,
                ) from exc

        from ..llm._prompt_safety import escape_prompt_data

        # Build stream inputs (possibly with multimodal image injection)
        _stream_inputs = {
            "context": ctx.combined_context,
            "question": escape_prompt_data(ctx.question),
            "history": ctx.history_messages,
            "memory_context": "",
        }

        async def _do_stream() -> str:
            emit_kwargs = (
                {
                    "first_token_timeout_s": settings.RAG_FAST_PATH_FIRST_TOKEN_TIMEOUT_S,
                    "stall_timeout_s": settings.RAG_FAST_PATH_STREAM_STALL_TIMEOUT_S,
                }
                if _latency_guard
                else {}
            )
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
                    return await _emit_stream(
                        bus,
                        ctx,
                        llm.astream(messages),
                        **emit_kwargs,
                    )
                except Exception:
                    if ctx.answer.strip():
                        raise
                    logger.warning(
                        "Multimodal streaming failed, falling back to text-only streaming",
                        exc_info=True,
                    )
                    return await _emit_stream(
                        bus,
                        ctx,
                        chain.astream(_stream_inputs),
                        **emit_kwargs,
                    )
            return await _emit_stream(
                bus,
                ctx,
                chain.astream(_stream_inputs),
                **emit_kwargs,
            )

        # Stream tokens
        accumulated = ""

        try:
            if _tc_cm is not None:
                async with _tc_cm:
                    try:
                        accumulated = await _await_with_generation_deadline(_do_stream)
                        if not _strip_internal_tokens(strip_thinking_blocks(accumulated)).strip():
                            raise LLMEmptyResponseError(
                                "Provider stream completed without user-visible content",
                                provider=settings.LLM_BINDING,
                            )
                        _tc_cm.record_success()
                    except Exception:
                        # The traffic-controller context manager owns failure
                        # accounting; recording here would double-count it.
                        raise
            else:
                accumulated = await _await_with_generation_deadline(_do_stream)
                if not _strip_internal_tokens(strip_thinking_blocks(accumulated)).strip():
                    raise LLMEmptyResponseError(
                        "Provider stream completed without user-visible content",
                        provider=settings.LLM_BINDING,
                    )
        except Exception as exc:
            _llm_status = "error"
            if isinstance(exc, (TimeoutError, LLMTimeoutError)):
                accumulated = _preserve_incomplete_stream(
                    bus,
                    ctx,
                    evidence_sources=sources if _latency_guard else None,
                )
                _llm_status = "degraded"
                # A timeout before the first visible token leaves the TTFT span
                # open. Close it explicitly and create a matching streaming span
                # so degraded runs remain complete in telemetry.
                ctx.trace.finish_span("llm_ttft", "degraded", error=exc)
                if not any(
                    span.label == "llm_streaming" and span.end_time is None
                    for span in ctx.trace.spans
                ):
                    ctx.trace.start_span(
                        "llm_streaming",
                        "generate",
                        parent_label="generate_answer",
                        degraded=True,
                    )
                logger.warning(
                    "Streaming exceeded generation deadline of %.1fs; preserving partial output",
                    _generation_timeout,
                )
            else:
                if ctx.answer.strip():
                    raise LLMTransientResponseError(
                        "Provider stream failed after partial visible output",
                        provider=settings.LLM_BINDING,
                    ) from exc
                logger.warning("Streaming failed, falling back to non-streaming", exc_info=True)
                if is_fallback_circuit_open():
                    raise RuntimeError(
                        "Fallback temporarily disabled due repeated failures"
                    ) from exc
                full_chain = (
                    prompt | RunnableLambda(apply_anthropic_cache_control) | llm | StrOutputParser()
                )
                fallback_inputs = {
                    "context": ctx.combined_context,
                    "question": escape_prompt_data(ctx.question),
                    "history": ctx.history_messages,
                    "memory_context": "",
                }
                last_err: Exception | None = None
                fallback_attempts = 1 if isinstance(exc, LLMEmptyResponseError) else 2

                async def _invoke_fallback_once() -> str:
                    if _stream_images:
                        try:
                            return await _await_with_generation_deadline(
                                lambda: asyncio.to_thread(
                                    _invoke_chain_with_retry_multimodal,
                                    full_chain,
                                    fallback_inputs,
                                    _stream_images,
                                )
                            )
                        except Exception:
                            logger.warning(
                                "Multimodal fallback failed, retrying text-only",
                                exc_info=True,
                            )
                    return await _await_with_generation_deadline(
                        lambda: asyncio.to_thread(
                            _invoke_chain_with_retry,
                            full_chain,
                            fallback_inputs,
                        )
                    )

                for _ in range(fallback_attempts):
                    try:
                        if _tc_cm is not None:
                            async with _tc_cm:
                                accumulated = await _invoke_fallback_once()
                                fallback_visible = _strip_internal_tokens(
                                    strip_thinking_blocks(accumulated)
                                )
                                if not fallback_visible.strip():
                                    raise LLMEmptyResponseError(
                                        "Fallback completed without user-visible content",
                                        provider=settings.LLM_BINDING,
                                    )
                                _tc_cm.record_success()
                        else:
                            accumulated = await _invoke_fallback_once()
                            fallback_visible = _strip_internal_tokens(
                                strip_thinking_blocks(accumulated)
                            )
                            if not fallback_visible.strip():
                                raise LLMEmptyResponseError(
                                    "Fallback completed without user-visible content",
                                    provider=settings.LLM_BINDING,
                                )
                        record_fallback_success()
                        _llm_status = "fallback_success"
                        break
                    except Exception as fallback_exc:
                        last_err = fallback_exc
                else:
                    record_fallback_failure()
                    if isinstance(last_err, LLMError):
                        raise last_err
                    raise RuntimeError("Fallback non-streaming generation failed") from last_err
                bus.emit_token(fallback_visible)
        finally:
            try:
                LLM_REQUEST_DURATION.labels(provider=_llm_provider).observe(
                    time.monotonic() - _llm_start
                )
                LLM_REQUEST_TOTAL.labels(
                    provider=_llm_provider, status="degraded" if ctx.degraded else _llm_status
                ).inc()
            except Exception:
                logger.debug("Metrics recording failed", exc_info=True)

        bus.emit_step("generate", "done")

        # Save final answer and messages
        ctx.answer = _strip_internal_tokens(strip_thinking_blocks(accumulated))
        if not ctx.answer.strip():
            raise LLMEmptyResponseError(
                "Generation completed without user-visible content",
                provider=settings.LLM_BINDING,
            )
        ctx.trace.finish_span("llm_streaming", "degraded" if ctx.degraded else "success")
        ctx.trace.finish_span("generate_answer", "degraded" if ctx.degraded else "success")
        for span in reversed(ctx.trace.spans):
            if span.label == "generate_answer":
                span.tokens_out = max(1, len(ctx.answer) // 4)
                span.metadata["visible_chars"] = len(ctx.answer)
                break
        _api_mod.assert_settings_epoch(ctx)
        await asyncio.to_thread(_persist_response)
        await asyncio.gather(
            asyncio.to_thread(_api_mod.commit_memory_recall_side_effects, ctx),
            asyncio.to_thread(_api_mod.commit_anchor_for_success, ctx),
        )
        await _schedule_fact_extraction_if_needed()
        from ...services.memory._service._crud import flush_pending_touches

        await asyncio.to_thread(flush_pending_touches)
        ctx.trace.finish_span("pipeline", "degraded" if ctx.degraded else "success")

        # Structural evidence belongs to a successfully generated answer.
        if sources:
            bus.emit_sources(sources)
        if ctx.web_results:
            bus.emit_web_results(ctx.web_results)
        bus.emit_trace(ctx.trace.to_dict())
        bus.emit_done(ctx.session_id, message_ids=ctx.saved_message_ids)
        write_pipeline_log(ctx.trace, question, session_id=ctx.session_id)
    except asyncio.CancelledError:
        if skill_task is not None and not skill_task.done():
            skill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await skill_task
        try:
            if ctx.answer and ctx.session_id:
                ctx.degraded = True
                ctx.degradation_reason = "cancelled"
                from ...core.chat_run_context import cancelled_partial_commit

                def _persist_cancelled_response() -> None:
                    with cancelled_partial_commit():
                        _persist_response()

                await asyncio.to_thread(_persist_cancelled_response)
                await _schedule_fact_extraction_if_needed()
                from ...services.memory._service._crud import flush_pending_touches

                await asyncio.to_thread(flush_pending_touches)
        except Exception:
            logger.error("Failed to persist cancelled stream result", exc_info=True)
        await asyncio.to_thread(_api_mod.cleanup_empty_session, ctx)
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
        # Trace first; the error itself is the single terminal event.  Sending
        # a later ``done`` would either erase the error or create an ambiguous
        # two-terminal protocol.
        bus.emit_trace(ctx.trace.to_dict())
        bus.emit_error(
            err_info["message"],
            code=err_info.get("code"),
            detail=err_info.get("detail"),
            exception_type=err_info.get("exception_type"),
        )
        bus.close()
        write_pipeline_log(ctx.trace, question, session_id=ctx.session_id)
        await asyncio.to_thread(_api_mod.cleanup_empty_session, ctx)
    finally:
        _hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Global heartbeat task exited with error", exc_info=True)


async def _run_stream_pipeline(
    bus: Any,
    ctx: PipelineContext,
    question: str,
) -> None:
    """Run streaming work with a stable request-scoped settings view."""
    from ...core.config import activate_settings_snapshot

    with activate_settings_snapshot(ctx.settings_snapshot):
        await _run_stream_pipeline_inner(bus, ctx, question)
