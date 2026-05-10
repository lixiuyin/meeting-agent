"""Retrieval pipeline steps: retrieve, rerank, and deduplicate.

The main entry point is ``retrieve_documents``. Heavy helper functions and
sub-modules:
- ``_retrieve_utils``   — constants, scoring, dedup, low-info filtering
- ``_retrieve_filters`` — speaker/temporal filtering, content-type bias
- ``_retrieve_routing`` — scope enumeration, summary routing, chunk allocation
- ``_retrieve_broad``   — broad recall per-file retrieval, multi-query merge
- ``_retrieve_post``    — rerank, pre-rerank dedup, near-duplicate suppression
"""

import asyncio

from ...core.config import settings
from ...core.database import (
    get_connection,
    get_write_connection,
    read_anchor,
    touch_anchor,
    write_anchor,
)
from ...core.metrics import (
    ANCHOR_HIT_TOTAL,
    ANCHOR_TTL_REFRESH_TOTAL,
)
from ..rag import (
    determine_adaptive_top_k,
    retrieve,
    retrieve_sibling_chunks,
)
from ..rag._query import _is_simple_query, is_summary_intent
from ..rag._scoping_strategies import get_scoping_strategy
from . import _retrieve_utils
from ._common import logger
from ._context import PipelineContext
from ._retrieve_broad import (
    _count_files_in_meetings,
    _retrieve_broad_recall,
    _retrieve_scoped,
    _retry_raw_question,
)
from ._retrieve_filters import (
    _apply_speaker_filter,
    _apply_temporal_filter,
)
from ._retrieve_post import (
    pre_rerank_dedup,
    rerank_documents,
    suppress_near_duplicates,
)
from ._retrieve_routing import (
    _load_known_speakers,
)

__all__ = [
    "pre_rerank_dedup",
    "rerank_documents",
    "retrieve",
    "retrieve_documents",
    "suppress_near_duplicates",
]


async def retrieve_documents(ctx: PipelineContext) -> None:
    """Retrieve relevant meeting chunks from the vector store."""
    ctx.trace.start_span("retrieve", "retrieve")
    query = ctx.rewritten_query or ctx.question

    # Cache the query embedding so summary scoring can reuse it without
    # re-embedding.  The _QueryCachedEmbeddings wrapper deduplicates, so
    # the Chroma call below will hit the cache.
    if ctx.query_embedding is None:
        try:
            from ..embedder import get_embeddings

            embeddings = ctx.embeddings if ctx.embeddings is not None else get_embeddings()
            ctx.query_embedding = await asyncio.to_thread(embeddings.embed_query, query)
        except Exception:
            pass  # non-fatal — summary scores fall back to 0.5

    fetch_multiplier = settings.RAG_RERANK_FETCH_MULTIPLIER if settings.RERANKER_BINDING else 1
    # QR-3: Summary intent benefits from a larger multiplier to capture
    # broad context across documents.
    if is_summary_intent(ctx.question):
        fetch_multiplier = int(fetch_multiplier * 1.5)

    is_broad_recall = not ctx.file_ids
    effective_k = determine_adaptive_top_k(
        ctx.question,
        ctx.top_k,
        is_broad_recall=is_broad_recall,
    )
    if ctx.file_ids:
        effective_k = max(effective_k, 12)
    elif ctx.meeting_ids:
        effective_k = max(effective_k, settings.TOP_K_MEETING_SCOPED_FLOOR)

    known_speakers = await asyncio.to_thread(_load_known_speakers, ctx.meeting_ids)
    anchor_meeting_ids, anchor_file_ids = _read_anchor(ctx)

    # Multi-query lifted to input dimension: variants generated once at top level.
    # In broad recall, H2 makes per-variant funnel runs do real work (each
    # variant produces its own router+funnel scope; results merged via
    # file-level RRF), so the legacy "disable in broad recall" flag is now
    # only a kill-switch for environments wanting to stay on single-query.
    multi_query_eligible = settings.MULTI_QUERY_ENABLED and not _is_simple_query(query)
    if is_broad_recall and not settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED:
        multi_query_eligible = False
    queries: list[str] = [query]
    if multi_query_eligible:
        variants = await _retrieve_utils._generate_query_variants(
            query,
            n=settings.MULTI_QUERY_COUNT,
            llm=ctx.llm,
        )
        queries = list(dict.fromkeys([query, *variants]))

    try:
        qa = None
        if is_broad_recall:
            # Broad recall: determine meeting routing and scoping strategy
            target_files = settings.RAG_BROAD_RECALL_SCOPE_CAP
            meeting_pinned = bool(ctx.meeting_ids and not ctx.file_ids)
            if meeting_pinned:
                file_count = _count_files_in_meetings(ctx.meeting_ids)
                target_files = max(target_files, file_count)

            scoped_meeting_ids = list(ctx.meeting_ids) if ctx.meeting_ids else []

            # Phase 0 — Meeting-level routing via meeting summary vectors.
            if not scoped_meeting_ids and settings.RAG_MEETING_SUMMARY_ROUTER_ENABLED:
                try:
                    from ..rag._meeting_summary_vectorstore import route_meetings_by_summary

                    router_timeout = settings.RAG_MEETING_SUMMARY_ROUTER_TIMEOUT_S

                    async def _run_router():
                        return await asyncio.to_thread(
                            route_meetings_by_summary,
                            query,
                            top_k=settings.RAG_MEETING_SUMMARY_ROUTER_TOP_MEETINGS,
                            min_score=settings.RAG_MEETING_SUMMARY_ROUTER_MIN_SCORE,
                        )

                    if router_timeout > 0:
                        meeting_route = await asyncio.wait_for(
                            _run_router(), timeout=router_timeout
                        )
                    else:
                        meeting_route = await _run_router()

                    if (
                        meeting_route is not None
                        and len(meeting_route) >= settings.RAG_MEETING_SUMMARY_ROUTER_MIN_HITS
                    ):
                        scoped_meeting_ids = [mid for mid, _ in meeting_route]
                        ctx.trace.start_span(
                            "meeting_router",
                            "retrieve",
                            parent_label="retrieve",
                            hits=len(meeting_route),
                        ).finish("success")
                        try:
                            from ...core.metrics import MEETING_SUMMARY_ROUTER_HITS

                            MEETING_SUMMARY_ROUTER_HITS.labels(result="narrowed").inc()
                        except Exception:
                            pass  # metrics are optional
                    else:
                        try:
                            from ...core.metrics import MEETING_SUMMARY_ROUTER_HITS

                            MEETING_SUMMARY_ROUTER_HITS.labels(result="fail_open").inc()
                        except Exception:
                            pass  # metrics are optional
                except TimeoutError:
                    logger.debug(
                        "Meeting summary router timed out (%.1fs); fail-open to all meetings",
                        router_timeout,
                    )
                except Exception:
                    logger.debug("Meeting summary router failed", exc_info=True)

            strategy = get_scoping_strategy()
            ctx.docs, qa = await _retrieve_broad_recall(
                ctx,
                queries,
                effective_k,
                known_speakers,
                anchor_meeting_ids,
                anchor_file_ids,
                strategy,
                target_files,
                scoped_meeting_ids=scoped_meeting_ids or None,
            )
        else:
            ctx.docs, qa = await _retrieve_scoped(
                ctx,
                queries,
                effective_k,
                fetch_multiplier,
                known_speakers,
            )

        ctx.query_analysis = qa

        if qa and qa.speaker_names and ctx.docs:
            before = len(ctx.docs)
            span = ctx.trace.start_span(
                "filter.speaker",
                "retrieve",
                parent_label="retrieve",
                before=before,
                speaker_names=list(qa.speaker_names),
            )
            ctx.docs = _apply_speaker_filter(ctx.docs, qa, ctx.meeting_ids)
            span.metadata["after"] = len(ctx.docs)
            span.finish("success")
        elif qa and qa.temporal_hint and ctx.docs:
            before = len(ctx.docs)
            span = ctx.trace.start_span(
                "filter.temporal",
                "retrieve",
                parent_label="retrieve",
                before=before,
            )
            ctx.docs = _apply_temporal_filter(ctx.docs, qa.temporal_hint)
            span.metadata["after"] = len(ctx.docs)
            span.finish("success")

        if settings.RAG_SIBLING_CORETRIEVE_ENABLED and ctx.docs:
            before = len(ctx.docs)
            span = ctx.trace.start_span(
                "filter.sibling_coretrieve",
                "retrieve",
                parent_label="retrieve",
                before=before,
            )
            siblings = retrieve_sibling_chunks(
                ctx.docs,
                max_per_anchor=settings.RAG_SIBLING_CORETRIEVE_PER_ANCHOR,
                max_total=settings.RAG_SIBLING_CORETRIEVE_MAX_TOTAL,
            )
            if siblings:
                ctx.docs.extend(siblings)
            span.metadata["siblings_added"] = len(siblings) if siblings else 0
            span.metadata["after"] = len(ctx.docs)
            span.finish("success")

        # D2: Retry with raw question when rewritten query returns zero docs (unscoped).
        _retry_used = False
        if (
            not ctx.docs
            and not ctx.meeting_ids
            and not ctx.file_ids
            and ctx.rewritten_query
            and ctx.rewritten_query != ctx.question
        ):
            ctx.docs, qa = await _retry_raw_question(
                ctx,
                effective_k,
                fetch_multiplier,
                known_speakers,
            )
            _retry_used = True
            if qa:
                ctx.query_analysis = qa

        # --- Write conversational anchor from retrieved docs ---
        # C-3: Skip anchor write when docs came from retry to avoid locking in wrong scope.
        if not _retry_used:
            _write_anchor_from_docs(ctx, effective_k)

        ctx.trace.finish_span("retrieve")
        for span in ctx.trace.spans:
            if span.label == "retrieve" and span.end_time is not None:
                span.docs_retrieved = len(ctx.docs)
                break
    except Exception as _trace_exc:
        ctx.trace.finish_span("retrieve", "error", error=_trace_exc)
        raise


# ---------------------------------------------------------------------------
# Anchor management
# ---------------------------------------------------------------------------


def _read_anchor(
    ctx: PipelineContext,
) -> tuple[list[int] | None, list[int] | None]:
    """Read conversational anchor from session context.

    Honours ``RAG_ANCHOR_TTL_MINUTES`` for the freshness window.  When
    ``RAG_ANCHOR_TTL_MODE`` is ``sliding``, also refreshes the anchor
    timestamp on a successful read so long sessions of related questions
    keep their anchor alive without a write on every turn.
    """
    anchor_meeting_ids: list[int] | None = None
    anchor_file_ids: list[int] | None = None
    if settings.RAG_ANCHOR_ENABLED and ctx.session_id and not ctx.file_ids:
        ttl_seconds = max(0, settings.RAG_ANCHOR_TTL_MINUTES) * 60
        try:
            with get_connection() as conn:
                anchor_data = read_anchor(conn, ctx.session_id, ttl_seconds=ttl_seconds)
                if anchor_data:
                    anchor_meeting_ids = anchor_data.get("meeting_ids") or []
                    anchor_file_ids = anchor_data.get("file_ids") or []
                    if ctx.meeting_ids and anchor_file_ids:
                        anchor_meeting_ids = None
                    ANCHOR_HIT_TOTAL.labels(result="fresh").inc()
                else:
                    ANCHOR_HIT_TOTAL.labels(result="missing").inc()
            if anchor_data and settings.RAG_ANCHOR_TTL_MODE == "sliding":
                _slide_anchor_ttl(ctx.session_id)
            span = (
                ctx.trace.start_span(
                    "anchor.read",
                    "anchor",
                    present=True,
                    meeting_count=len(anchor_meeting_ids) if anchor_meeting_ids else 0,
                    file_count=len(anchor_file_ids) if anchor_file_ids else 0,
                    ttl_mode=settings.RAG_ANCHOR_TTL_MODE,
                )
                if ctx.trace
                else None
            )
            if span:
                span.finish("success")
        except Exception:
            logger.warning("Anchor read failed, proceeding without anchor", exc_info=True)
    elif not settings.RAG_ANCHOR_ENABLED or ctx.file_ids:
        ANCHOR_HIT_TOTAL.labels(result="disabled").inc()
    return anchor_meeting_ids, anchor_file_ids


def _slide_anchor_ttl(session_id: str) -> None:
    """Best-effort sliding-TTL refresh for the anchor."""
    try:
        with get_write_connection() as conn:
            touch_anchor(conn, session_id)
        ANCHOR_TTL_REFRESH_TOTAL.labels(result="refreshed").inc()
    except Exception:
        ANCHOR_TTL_REFRESH_TOTAL.labels(result="skipped").inc()
        logger.debug("Anchor sliding TTL refresh failed", exc_info=True)


def _write_anchor_from_docs(ctx: PipelineContext, effective_k: int) -> None:
    """Write conversational anchor from retrieved docs metadata."""
    if not (settings.RAG_ANCHOR_ENABLED and ctx.session_id and ctx.docs):
        return
    try:
        mids: list[int] = []
        fids: list[int] = []
        for d in ctx.docs[:effective_k]:
            meta = d.get("metadata") or {}
            mid = meta.get("meeting_id")
            fid = meta.get("file_id")
            if isinstance(mid, int) and mid not in mids:
                mids.append(mid)
            if isinstance(fid, int) and fid not in fids:
                fids.append(fid)
        with get_write_connection() as conn:
            write_anchor(
                conn,
                ctx.session_id,
                meeting_ids=mids if mids else None,
                file_ids=fids if fids else None,
                max_ids=settings.RAG_ANCHOR_MAX_IDS,
            )
        span_aw = (
            ctx.trace.start_span(
                "anchor.write",
                "anchor",
                meeting_ids_written=len(mids),
                file_ids_written=len(fids),
            )
            if ctx.trace
            else None
        )
        if span_aw:
            span_aw.finish("success")
    except Exception:
        logger.warning("Anchor write failed", exc_info=True)
