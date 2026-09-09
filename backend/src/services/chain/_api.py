import asyncio
import contextlib
import datetime
from typing import Any, Literal

from ...core.config import activate_settings_snapshot, build_retrieval_profile_snapshot, settings
from ...core.metrics import CONTEXT_STEP_ERROR_TOTAL, CONTEXT_STEP_TIMEOUT_TOTAL, RAG_DOCS_AT_STAGE
from ...core.operating_modes import MemoryMode, RetrievalProfile
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
    commit_memory_recall_side_effects,
    load_entity_context,
    load_history,
    load_memories,
    load_session_context,
    perform_web_search,
)
from ._steps_generate import build_context, generate_answer, save_messages, schedule_fact_extraction
from ._steps_retrieve import commit_anchor_for_success, prepare_query_plan, retrieve_documents
from ._steps_session import cleanup_empty_session, ensure_session, rewrite_query_step


def assert_settings_epoch(ctx: PipelineContext) -> None:
    """Abort before committing work assembled from two config versions."""
    # Direct internal/test contexts predate request snapshots. Public ask()
    # and ask_stream() always attach one and therefore enforce the guard.
    if ctx.settings_snapshot is None:
        return
    current = get_settings_epoch()
    if current != ctx.settings_epoch:
        raise RuntimeError(
            "Runtime settings changed while this request was running; please retry "
            f"(started at epoch {ctx.settings_epoch}, current epoch {current})"
        )


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
    if ctx.rag_mode == "bm25":
        ctx.trace.start_span("prewarm_query_embedding", "embedding", skipped=True)
        ctx.trace.finish_span("prewarm_query_embedding")
        return
    if ctx.query_embedding is not None:
        ctx.trace.start_span("prewarm_query_embedding", "embedding", skipped=True)
        ctx.trace.finish_span("prewarm_query_embedding")
        return
    query = ctx.rewritten_query or ctx.question
    if not query:
        ctx.trace.start_span("prewarm_query_embedding", "embedding", skipped=True)
        ctx.trace.finish_span("prewarm_query_embedding")
        return
    ctx.trace.start_span(
        "prewarm_query_embedding",
        "embedding",
        parent_label="pipeline",
        binding=settings.EMBEDDING_BINDING,
        model=settings.EMBEDDING_MODEL,
    )
    try:
        from ..embedder import get_embeddings

        embeddings = ctx.embeddings if ctx.embeddings is not None else get_embeddings()
        ctx.query_embedding = await asyncio.to_thread(embeddings.embed_query, query)
        ctx.trace.finish_span("prewarm_query_embedding")
    except Exception as exc:
        # Non-fatal: downstream calls will fall back to embedding on demand.
        ctx.trace.finish_span("prewarm_query_embedding", "degraded", error=exc)
        logger.warning("query embedding pre-warm failed", exc_info=True)


def _context_branch_timeout(ctx: PipelineContext) -> float:
    """Return the timeout for best-effort context-loading branches.

    Uses the configured ``CONTEXT_LOAD_TIMEOUT_S`` directly. Previously a
    hard 4.0s ceiling was applied for scoped queries, but that caused
    excessive timeouts when the embedding service is slow (vector-search
    timeout is 8.0s by default). Let operators tune via config instead.
    """
    return settings.CONTEXT_LOAD_TIMEOUT_S


def _request_settings_snapshot(
    epoch: int,
    profile: RetrievalProfile,
    memory_mode: MemoryMode = "balanced",
):
    """Build documented operating modes without mutating global settings."""
    return build_retrieval_profile_snapshot(
        epoch=epoch,
        profile=profile,
        memory_mode=memory_mode,
    )


async def validate_or_promote_fast_path(
    ctx: PipelineContext,
    *,
    requested_top_k: int | None,
) -> None:
    """Keep a fast probe only when its evidence is concentrated and sufficient.

    Automatic fast-path selection is speculative. A weak or cross-source BM25
    result is promoted once to the normal retrieval mode before reranking and
    answer generation. Explicit ``rag_mode`` choices are never overridden.
    """
    if not ctx.fast_path_auto_selected or ctx.query_route_decision is None:
        return

    from ..rag._query import validate_fast_path_evidence

    evidence_decision = validate_fast_path_evidence(
        ctx.query_route_decision,
        ctx.docs,
        meeting_ids=ctx.meeting_ids,
        file_ids=ctx.file_ids,
    )
    ctx.fast_path_evidence_decision = evidence_decision
    span = ctx.trace.start_span(
        "fast_path_evidence",
        "routing",
        route=ctx.query_route_decision.route,
        safe=evidence_decision.safe,
        confidence=evidence_decision.confidence,
        reason=evidence_decision.reason,
        candidate_count=len(ctx.docs),
    )
    span.finish("success")
    if evidence_decision.safe:
        return

    ctx.fast_path_promotion_reason = evidence_decision.reason
    ctx.fast_path_auto_selected = False
    ctx.rag_mode = None
    ctx.top_k = requested_top_k
    ctx.docs = []
    ctx.query_embedding = None
    await retrieve_documents(ctx)


async def _run_pipeline_inner(
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
    ctx.trace.start_span(
        "pipeline",
        "pipeline",
        retrieval_profile=ctx.retrieval_profile,
        memory_mode=ctx.memory_mode,
    )
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

            # Publish one immutable interpretation of scope, time and speaker
            # constraints before any parallel context branch reads it.
            await prepare_query_plan(ctx)

            # Warm the query-embedding cache so the parallel context-loading
            # branches below all share a single embedding API call.
            from ._query_routes import is_recorded_fact_request

            recorded_request = is_recorded_fact_request(ctx.question, ctx.memory_mode)
            if not ctx.snapshot_restored and not recorded_request:
                await _prewarm_query_embedding(ctx)

            # Steps 4-9: retrieval, memory, session context, web search, history in parallel
            async def _retrieve_branch() -> None:
                with otel_span("chain.retrieve_branch"):
                    requested_top_k = ctx.top_k
                    await retrieve_documents(ctx)
                    if not ctx.snapshot_restored:
                        await validate_or_promote_fast_path(
                            ctx,
                            requested_top_k=requested_top_k,
                        )
                    RAG_DOCS_AT_STAGE.labels(stage="retrieved").observe(len(ctx.docs))
                    # A saved snapshot already contains the authoritative document
                    # order, scores, and exact rendered context.  Running today's
                    # deduper/reranker here would silently mutate that historical
                    # evidence view and make continuation non-reproducible.
                    if ctx.snapshot_restored:
                        return
                    pre_rerank_dedup(ctx)
                    RAG_DOCS_AT_STAGE.labels(stage="deduped").observe(len(ctx.docs))
                    await asyncio.to_thread(rerank_documents, ctx)
                    RAG_DOCS_AT_STAGE.labels(stage="reranked").observe(len(ctx.docs))
                    await asyncio.to_thread(suppress_near_duplicates, ctx)

            context_timeout = _context_branch_timeout(ctx)
            parallel = [_retrieve_branch()]
            if not ctx.snapshot_restored:
                parallel.append(_best_effort("memories", load_memories(ctx), context_timeout))
                # Session summaries and the current KG do not yet expose
                # system-time versions. Excluding them is safer than leaking
                # knowledge recorded after an explicit known_at boundary.
                if ctx.known_at is None and not recorded_request:
                    parallel.extend(
                        [
                            _best_effort("session", load_session_context(ctx), context_timeout),
                            _best_effort("entity", load_entity_context(ctx), context_timeout),
                        ]
                    )
            # Conversation history is part of the interaction, not evidence.
            parallel.append(_best_effort("history", load_history(ctx), context_timeout, ctx=ctx))
            await asyncio.gather(*parallel)
            # Web fallback depends on the completed local retrieval confidence;
            # running it in the same gather made the skip decision race ctx.docs.
            if not ctx.snapshot_restored and ctx.known_at is None and not recorded_request:
                await _best_effort("web", perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S)

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
            assert_settings_epoch(ctx)
            await asyncio.to_thread(build_context, ctx)
            RAG_DOCS_AT_STAGE.labels(stage="truncated").observe(len(ctx.docs))
            await generate_answer(ctx, skill_definition)
            assert_settings_epoch(ctx)
            await asyncio.to_thread(save_messages, ctx)
            if not ctx.snapshot_restored:
                await asyncio.gather(
                    asyncio.to_thread(commit_memory_recall_side_effects, ctx),
                    asyncio.to_thread(commit_anchor_for_success, ctx),
                )
                await schedule_fact_extraction(ctx)
        from ...services.memory._service._crud import flush_pending_touches

        await asyncio.to_thread(flush_pending_touches)
        ctx.trace.finish_span("pipeline")
    except Exception as exc:
        ctx.trace.finish_span("pipeline", "error", error=exc)
        if skill_task is not None and not skill_task.done():
            skill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await skill_task
        await asyncio.to_thread(cleanup_empty_session, ctx)
        raise


async def _run_pipeline(
    ctx: PipelineContext,
    skill_definition: dict[str, Any] | None = None,
    *,
    skill_task: "asyncio.Task[Any] | None" = None,
) -> None:
    """Run one request with settings pinned to its admission snapshot."""
    with activate_settings_snapshot(ctx.settings_snapshot):
        await _run_pipeline_inner(ctx, skill_definition, skill_task=skill_task)


async def ask(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_mode: Literal["off", "fallback", "always"] | None = None,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    valid_at: datetime.datetime | None = None,
    known_at: datetime.datetime | None = None,
    rag_mode: str | None = None,
    retrieval_profile: RetrievalProfile = "balanced",
    memory_mode: MemoryMode = "balanced",
    continuation_mode: Literal["latest", "saved_scope", "saved_snapshot"] = "latest",
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
    from ..rag._query import classify_query_route

    # The frontend's "auto" value means no explicit provider override. Normalize
    # it at the boundary so automatic fast-path selection is not accidentally
    # disabled merely because the client serialized the default UI option.
    effective_rag_mode = None if rag_mode == "auto" else rag_mode
    query_route_decision = classify_query_route(
        question,
        meeting_count=len(set(meeting_ids or [])),
        file_count=len(set(file_ids or [])),
    )
    fast_path_auto_selected = bool(
        effective_rag_mode is None
        and settings.RAG_FAST_PATH_ENABLED
        and retrieval_profile == "fast"
        and not use_web_search
        and query_route_decision.fast_candidate
    )
    if fast_path_auto_selected:
        effective_rag_mode = settings.RAG_FAST_PATH_RETRIEVAL_MODE

    ctx = PipelineContext(
        question=question,
        session_id=session_id,
        user_id=user_id,
        meeting_ids=meeting_ids,
        file_ids=file_ids,
        top_k=top_k,
        use_web_search=use_web_search,
        web_search_mode=web_search_mode or ("always" if use_web_search else "off"),
        web_search_results=web_search_results,
        file_types=file_types,
        date_from=date_from,
        date_to=date_to,
        valid_at=valid_at,
        known_at=known_at,
        rag_mode=effective_rag_mode,
        retrieval_profile=retrieval_profile,
        memory_mode=memory_mode,
        continuation_mode=continuation_mode,
        query_route_decision=query_route_decision,
        fast_path_auto_selected=fast_path_auto_selected,
        settings_epoch=epoch,
        settings_snapshot=_request_settings_snapshot(epoch, retrieval_profile, memory_mode),
    )

    # Query routing: skip retrieval for casual inputs
    ctx.trace.start_span(
        "classify_intent",
        "routing",
        query_route=query_route_decision.route,
        route_confidence=query_route_decision.confidence,
        route_reasons=list(query_route_decision.reasons),
    )
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
            degraded=ctx.degraded,
            degradation_reason=ctx.degradation_reason,
        )

    # Kick off LLM init and skill matching concurrently with the RAG pipeline.
    # Skill match is only consumed right before generate_answer, so it overlaps
    # with the (usually dominant) vector_search latency.
    from ..llm import get_llm

    async def _do_skill_match():
        from ._query_routes import is_recorded_fact_request

        if is_recorded_fact_request(question, ctx.memory_mode):
            ctx.trace.start_span("skill_match", "skill", skipped=True, reason="recorded_facts")
            ctx.trace.finish_span("skill_match")
            return None
        if settings.RAG_FAST_PATH_ENABLED and query_route_decision.fast_candidate:
            ctx.trace.start_span("skill_match", "skill", skipped=True, reason="fast_query")
            ctx.trace.finish_span("skill_match")
            return None
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
        span = ctx.trace.start_span("skill_match", "skill")
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
                    span.metadata["rewrite_match"] = "timeout"
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
        sources=_extract_sources(
            ctx.docs, max_sources=len(ctx.docs), memory_sources=ctx.memory_sources
        ),
        session_id=ctx.session_id,
        web_results=ctx.web_results if ctx.web_results else None,
        past_sessions=ctx.past_session_refs if ctx.past_session_refs else None,
        extraction_failed=ctx.failed_extraction_count > 0,
        skill_used=ctx.skill_name,
        skill_confidence=ctx.skill_confidence,
        trace=ctx.trace.to_dict(),
        context_truncated=ctx.dropped_chunks if ctx.dropped_chunks > 0 else None,
        background_errors=ctx.background_errors,
        degraded=ctx.degraded,
        degradation_reason=ctx.degradation_reason,
    )


async def ask_stream(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_mode: Literal["off", "fallback", "always"] | None = None,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    valid_at: datetime.datetime | None = None,
    known_at: datetime.datetime | None = None,
    rag_mode: str | None = None,
    retrieval_profile: RetrievalProfile = "balanced",
    memory_mode: MemoryMode = "balanced",
    continuation_mode: Literal["latest", "saved_scope", "saved_snapshot"] = "latest",
):
    """Streaming variant of ask() — yields typed SSE events via StreamBus.

    Event types:
        - {"type": "step", "step": "...", "status": "start|done"}
        - {"type": "token", "content": "..."}
        - {"type": "sources", "items": [...]}
        - {"type": "trace", "trace": {...}}
        - {"type": "status", "status": "degraded", "reason": "..."}
        - {"type": "web_results", "items": [...]}
        - {"type": "done", "session_id": "..."}
    """
    from ..stream_bus import StreamBus

    bus = StreamBus()

    epoch = get_settings_epoch()
    from ..rag._query import classify_query_route

    effective_rag_mode = None if rag_mode == "auto" else rag_mode
    query_route_decision = classify_query_route(
        question,
        meeting_count=len(set(meeting_ids or [])),
        file_count=len(set(file_ids or [])),
    )
    fast_path_auto_selected = bool(
        effective_rag_mode is None
        and settings.RAG_FAST_PATH_ENABLED
        and (retrieval_profile == "fast" or settings.CHAT_STREAM_LATENCY_GUARD_ENABLED)
        and not use_web_search
        and query_route_decision.fast_candidate
    )
    if fast_path_auto_selected:
        effective_rag_mode = settings.RAG_FAST_PATH_RETRIEVAL_MODE

    ctx = PipelineContext(
        question=question,
        session_id=session_id,
        user_id=user_id,
        meeting_ids=meeting_ids,
        file_ids=file_ids,
        top_k=top_k,
        use_web_search=use_web_search,
        web_search_mode=web_search_mode or ("always" if use_web_search else "off"),
        web_search_results=web_search_results,
        file_types=file_types,
        date_from=date_from,
        date_to=date_to,
        valid_at=valid_at,
        known_at=known_at,
        rag_mode=effective_rag_mode,
        retrieval_profile=retrieval_profile,
        memory_mode=memory_mode,
        continuation_mode=continuation_mode,
        query_route_decision=query_route_decision,
        fast_path_auto_selected=fast_path_auto_selected,
        settings_epoch=epoch,
        settings_snapshot=_request_settings_snapshot(epoch, retrieval_profile, memory_mode),
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
