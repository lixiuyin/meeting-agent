import asyncio
import contextlib
import datetime
from typing import Any

from ...core.config import build_settings_snapshot, settings
from ...core.metrics import CONTEXT_STEP_ERROR_TOTAL, CONTEXT_STEP_TIMEOUT_TOTAL, RAG_DOCS_AT_STAGE
from ...core.settings_epoch import get_settings_epoch
from ...core.trace import write_pipeline_log
from ...core.tracing import otel_span
from ._common import logger
from ._context import PipelineContext, PipelineResult
from ._formatting import _extract_sources
from ._retrieve_post import (
    pre_rerank_dedup,
    rerank_documents,
    suppress_near_duplicates,
)
from ._routing import _casual_response, _classify_intent
from ._skill_matching import (
    get_skill_loader as _get_skill_loader,
)
from ._skill_matching import (
    get_skill_matcher as _get_skill_matcher,
)
from ._steps_context import (
    load_entity_context,
    load_history,
    load_memories,
    load_session_context,
    perform_web_search,
)
from ._steps_generate import build_context, generate_answer, save_messages, schedule_fact_extraction
from ._steps_retrieve import retrieve_documents
from ._steps_session import ensure_session, rewrite_query_step


async def _best_effort(name: str, coro: Any, timeout: float, *, ctx: Any = None) -> None:
    """Execute a non-critical context branch with timeout/error isolation.

    When *ctx* is provided and the step is ``"history"``, sets
    ``ctx.history_load_failed = True`` on failure (M-8) so generation can
    inject a warning in the system prompt.
    """
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        CONTEXT_STEP_TIMEOUT_TOTAL.labels(step=name).inc()
        logger.warning("context step '%s' timed out after %.1fs", name, timeout)
        if ctx is not None and name == "history":
            ctx.history_load_failed = True
    except Exception:
        CONTEXT_STEP_ERROR_TOTAL.labels(step=name).inc()
        logger.warning("context step '%s' failed", name, exc_info=True)
        if ctx is not None and name == "history":
            ctx.history_load_failed = True


async def _prewarm_query_embedding(ctx: PipelineContext) -> None:
    """Compute the query embedding once before parallel context loading.

    Without this, retrieve / memories / session / entity each call
    ``embed_query`` independently from a thread-pool worker. They race into the
    cache wrapper at the same time; the leader's API call can take longer than
    the followers' stampede-wait timeout, causing each follower to start its
    own embedding API call. With slow providers this multiplied the work and
    blew past ``CONTEXT_LOAD_TIMEOUT_S``, surfacing as the "context step ...
    timed out" warnings in production. Pre-embedding here populates the LRU
    cache so all 4 downstream branches hit it instantly.
    """
    if ctx.query_embedding is not None:
        return
    query = ctx.rewritten_query or ctx.question
    if not query:
        return
    try:
        from ..embedder import get_embeddings

        embeddings = ctx.embeddings if ctx.embeddings is not None else get_embeddings()
        ctx.query_embedding = await asyncio.to_thread(embeddings.embed_query, query)
    except Exception:
        # Non-fatal: downstream calls will fall back to embedding on demand.
        logger.warning("query embedding pre-warm failed", exc_info=True)


def _context_branch_timeout(ctx: PipelineContext) -> float:
    """Return the timeout for best-effort context-loading branches.

    Uses the configured ``CONTEXT_LOAD_TIMEOUT_S`` directly. Previously a
    hard 4.0s ceiling was applied for scoped queries, but that caused
    excessive timeouts when the embedding service is slow (vector-search
    timeout is 8.0s by default). Let operators tune via config instead.
    """
    return settings.CONTEXT_LOAD_TIMEOUT_S


async def _run_pipeline(
    ctx: PipelineContext,
    skill_definition: dict[str, Any] | None = None,
    *,
    skill_task: "asyncio.Task[Any] | None" = None,
) -> None:
    """Execute the core RAG pipeline steps.

    Steps:
        1. ensure_session + rewrite_query_step (parallel)
        2-7. retrieve, rerank, memories, session context, web search, history (parallel)
        8-11. build_context, generate_answer, save_messages, schedule_fact_extraction

    If ``skill_task`` is provided, skill matching runs concurrently with
    retrieval; the result is consumed before ``generate_answer`` to choose the
    correct prompt template. This overlaps the skill_match latency (up to
    ``SKILL_MATCH_TIMEOUT_S``) with the vector search, which is usually the
    dominant cost.
    """
    ctx.trace.start_span("pipeline", "pipeline")
    try:
        with otel_span("chain.run_pipeline"):
            # Step 1-2: ensure session and rewrite query in parallel.
            # rewrite_query_step is self-contained — it loads its own lightweight
            # history when the resolver gate passes; for simple queries it's a no-op.
            await asyncio.gather(
                asyncio.to_thread(ensure_session, ctx),
                rewrite_query_step(ctx),
            )
            assert ctx.session_id is not None  # ensure_session always sets session_id

            # Warm the query-embedding cache so the parallel context-loading
            # branches below all share a single embedding API call.
            await _prewarm_query_embedding(ctx)

            # Steps 4-9: retrieval, memory, session context, web search, history in parallel
            async def _retrieve_branch() -> None:
                with otel_span("chain.retrieve_branch"):
                    await retrieve_documents(ctx)
                    RAG_DOCS_AT_STAGE.labels(stage="retrieved").observe(len(ctx.docs))
                    pre_rerank_dedup(ctx)
                    RAG_DOCS_AT_STAGE.labels(stage="deduped").observe(len(ctx.docs))
                    await asyncio.to_thread(rerank_documents, ctx)
                    RAG_DOCS_AT_STAGE.labels(stage="reranked").observe(len(ctx.docs))
                    await asyncio.to_thread(suppress_near_duplicates, ctx)

            context_timeout = _context_branch_timeout(ctx)
            parallel = [
                _retrieve_branch(),
                _best_effort("memories", load_memories(ctx), context_timeout),
                _best_effort("session", load_session_context(ctx), context_timeout),
                _best_effort("entity", load_entity_context(ctx), context_timeout),
                _best_effort("web", perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S),
                # H-7: load_history is non-critical context — wrap in _best_effort
                # to prevent its failure from cancelling other gather branches and
                # potentially leaking DB connections.
                _best_effort("history", load_history(ctx), context_timeout, ctx=ctx),
            ]
            await asyncio.gather(*parallel)

            # Resolve skill match result (awaiting at most the remaining time
            # after retrieve finishes — usually 0ms since skill_task is short).
            if skill_task is not None and skill_definition is None:
                try:
                    match = await skill_task
                except Exception:
                    logger.warning("skill_task failed, continuing without skill", exc_info=True)
                    match = None
                if match and getattr(match, "matched", False):
                    try:
                        loader = _get_skill_loader()
                        full = loader.get_full(match.skill.name)
                        skill_definition = full.model_dump() if full else None
                        ctx.skill_name = match.skill.name
                        ctx.skill_confidence = float(match.score)
                    except Exception:
                        logger.warning("skill definition resolution failed", exc_info=True)
                        skill_definition = None

            # Steps 10-13: context assembly, generation, persistence
            await asyncio.to_thread(build_context, ctx)
            RAG_DOCS_AT_STAGE.labels(stage="truncated").observe(len(ctx.docs))
            await generate_answer(ctx, skill_definition)
            await asyncio.to_thread(save_messages, ctx)
            schedule_fact_extraction(ctx)
        from ...services.memory._service._crud import flush_pending_touches

        await asyncio.to_thread(flush_pending_touches)
        ctx.trace.finish_span("pipeline")
    except Exception as exc:
        ctx.trace.finish_span("pipeline", "error", error=exc)
        if skill_task is not None and not skill_task.done():
            skill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await skill_task
        raise


async def ask(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    rag_mode: str | None = None,
) -> PipelineResult:
    """Ask a question about meeting content using the RAG pipeline.

    Pipeline steps:
        1. ensure_session + rewrite_query_step (parallel)
        2. retrieve_documents — semantic search in vector store
        3. rerank_documents — rerank results (optional)
        4. load_memories — fetch long-term user facts
        5. perform_web_search — optionally augment with web results
        6. load_history — fetch chat history for the session
        7. build_context — merge all context sources
        8. generate_answer — invoke LLM
        9. save_messages — persist Q&A to history
        10. schedule_fact_extraction — background fact mining

    Returns:
        PipelineResult with answer, sources, session_id, and optional web_results
    """
    epoch = get_settings_epoch()
    ctx = PipelineContext(
        question=question,
        session_id=session_id,
        user_id=user_id,
        meeting_ids=meeting_ids,
        file_ids=file_ids,
        top_k=top_k,
        use_web_search=use_web_search,
        web_search_results=web_search_results,
        file_types=file_types,
        date_from=date_from,
        date_to=date_to,
        rag_mode=rag_mode,
        settings_epoch=epoch,
        settings_snapshot=build_settings_snapshot(epoch=epoch),
    )

    # Query routing: skip retrieval for casual inputs
    ctx.trace.start_span("classify_intent", "routing")
    intent = _classify_intent(question)
    ctx.trace.finish_span("classify_intent")
    if intent == "casual":
        ctx.trace.start_span("pipeline", "pipeline", skipped=True)
        ctx.trace.start_span("skill_match", "skill", skipped=True)
        ctx.trace.finish_span("skill_match")
        await asyncio.to_thread(ensure_session, ctx)
        assert ctx.session_id is not None  # ensure_session always sets session_id
        ctx.answer = _casual_response(question)
        await asyncio.to_thread(save_messages, ctx)
        ctx.trace.finish_span("pipeline")
        return PipelineResult(
            answer=ctx.answer,
            sources=[],
            session_id=ctx.session_id,
            trace=ctx.trace.to_dict(),
            background_errors=ctx.background_errors,
        )

    # Kick off LLM init and skill matching concurrently with the RAG pipeline.
    # Skill match is only consumed right before generate_answer, so it overlaps
    # with the (usually dominant) vector_search latency.
    from ..llm import get_llm

    async def _do_skill_match():
        # Emit a skipped-marked span when skill matching is disabled or there
        # are no skills loaded, so traces can distinguish "we intentionally
        # bypassed skill match" from "we ran it and got no match".
        if not settings.SKILL_MATCHING_ENABLED:
            ctx.trace.start_span("skill_match", "skill", skipped=True)
            ctx.trace.finish_span("skill_match")
            return None
        loader = _get_skill_loader()
        summaries = loader.load_summaries()
        if not summaries:
            ctx.trace.start_span("skill_match", "skill", skipped=True)
            ctx.trace.finish_span("skill_match")
            return None
        # Short-circuit trivially short inputs (e.g. "yo bro") to avoid wasting
        # embedding API calls on queries that never match skills. Single words
        # are not short-circuited — they are too likely to be search terms.
        if len(question.strip().split()) == 2 and not question.strip().startswith("/"):
            ctx.trace.start_span("skill_match", "skill", skipped=True)
            ctx.trace.finish_span("skill_match")
            return None
        ctx.trace.start_span("skill_match", "skill")
        try:
            try:
                matcher = _get_skill_matcher()
                # HIGH-9: Match against both original and rewritten queries,
                # taking the highest-confidence result. The rewritten query
                # may lose domain terminology but gains retrieval precision;
                # the original preserves user intent phrasing.
                result = await asyncio.wait_for(
                    matcher.match(question, summaries),
                    timeout=settings.SKILL_MATCH_TIMEOUT_S,
                )
                # If rewritten query exists and differs, try dual-match.
                rewritten = getattr(ctx, "rewritten_query", None)
                if rewritten and rewritten != question:
                    try:
                        result_rw = await asyncio.wait_for(
                            matcher.match(rewritten, summaries),
                            timeout=settings.SKILL_MATCH_TIMEOUT_S / 2,
                        )
                        if result_rw and (not result or result_rw.score > result.score):
                            result = result_rw
                    except TimeoutError:
                        pass  # best-effort second match
                return result
            except TimeoutError:
                logger.warning(
                    "skill_match timed out after %.1fs, skipping",
                    settings.SKILL_MATCH_TIMEOUT_S,
                )
                return None
        finally:
            ctx.trace.finish_span("skill_match")

    # Resolve singletons once at pipeline entry (B2 ctx injection):
    # storing them in ctx guarantees a consistent instance for every step
    # even if settings change (reset_llm / reset_embeddings) mid-request.
    from ..embedder import get_embeddings

    ctx.llm = await asyncio.to_thread(get_llm)
    ctx.embeddings = await asyncio.to_thread(get_embeddings)
    skill_task = asyncio.create_task(_do_skill_match())

    await _run_pipeline(ctx, None, skill_task=skill_task)
    assert ctx.session_id is not None

    write_pipeline_log(
        ctx.trace,
        question,
        session_id=ctx.session_id,
        skill_used=ctx.skill_name,
        skill_confidence=ctx.skill_confidence,
    )
    return PipelineResult(
        answer=ctx.answer,
        sources=_extract_sources(ctx.docs, max_sources=len(ctx.docs)),
        session_id=ctx.session_id,
        web_results=ctx.web_results if ctx.web_results else None,
        past_sessions=ctx.past_session_refs if ctx.past_session_refs else None,
        extraction_failed=ctx.failed_extraction_count > 0,
        skill_used=ctx.skill_name,
        skill_confidence=ctx.skill_confidence,
        trace=ctx.trace.to_dict(),
        context_truncated=ctx.dropped_chunks if ctx.dropped_chunks > 0 else None,
        background_errors=ctx.background_errors,
    )


async def ask_stream(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    rag_mode: str | None = None,
):
    """Streaming variant of ask() — yields typed SSE events via StreamBus.

    Event types:
        - {"type": "step", "step": "...", "status": "start|done"}
        - {"type": "token", "content": "..."}
        - {"type": "sources", "items": [...]}
        - {"type": "trace", "trace": {...}}
        - {"type": "web_results", "items": [...]}
        - {"type": "done", "session_id": "..."}
    """
    from ..stream_bus import StreamBus

    bus = StreamBus()

    epoch = get_settings_epoch()
    ctx = PipelineContext(
        question=question,
        session_id=session_id,
        user_id=user_id,
        meeting_ids=meeting_ids,
        file_ids=file_ids,
        top_k=top_k,
        use_web_search=use_web_search,
        web_search_results=web_search_results,
        file_types=file_types,
        date_from=date_from,
        date_to=date_to,
        rag_mode=rag_mode,
        settings_epoch=epoch,
        settings_snapshot=build_settings_snapshot(epoch=epoch),
    )

    from ._api_stream import _run_stream_pipeline

    task = asyncio.create_task(_run_stream_pipeline(bus, ctx, question))

    try:
        async for event in bus:
            yield event
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Stream pipeline task exited with error", exc_info=True)
        bus.close()
