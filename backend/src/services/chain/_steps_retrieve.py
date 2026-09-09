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
import datetime
import json
import re

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
    build_query_plan,
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
    apply_meeting_evidence_policy,
)
from ._retrieve_post import (
    pre_rerank_dedup,
    rerank_documents,
    suppress_near_duplicates,
)
from ._retrieve_routing import (
    _load_known_speakers,
)


def _scoped_latest_chunks(ctx: PipelineContext, limit: int) -> list[dict]:
    """Return a bounded local fallback for a one-file scoped BM25 miss.

    This is deliberately a recency fallback rather than a semantic search: it
    keeps the request local and bounded when an index is momentarily stale, and
    it is only enabled for a scope that contains one file. Multi-file misses
    remain empty so the fast-path SLO cannot be defeated by an embedding call.
    """
    if not ctx.meeting_ids and not ctx.file_ids:
        return []
    from ..rag._contextual import restore_display_content

    where: list[str] = []
    params: list[object] = []
    if ctx.meeting_ids:
        where.append("meeting_id IN (" + ",".join("?" for _ in ctx.meeting_ids) + ")")
        params.extend(ctx.meeting_ids)
    if ctx.file_ids:
        where.append(
            "CAST(json_extract(metadata, '$.file_id') AS INTEGER) IN ("
            + ",".join("?" for _ in ctx.file_ids)
            + ")"
        )
        params.extend(ctx.file_ids)
    if ctx.user_id:
        where.append("meeting_id IN (SELECT id FROM meetings WHERE user_id = ?)")
        params.append(ctx.user_id)
    if not where:
        return []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT chunk_id, content, metadata FROM bm25_index WHERE "
                + " AND ".join(where)
                + " ORDER BY id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
    except Exception:
        logger.debug("Scoped BM25 recency fallback failed", exc_info=True)
        return []

    out: list[dict] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.setdefault("chunk_id", row["chunk_id"])
        out.append(
            {
                "content": restore_display_content(row["content"], metadata),
                "metadata": metadata,
                "score": 0.1,
                "score_kind": "relevance",
            }
        )
    return out


__all__ = [
    "pre_rerank_dedup",
    "prepare_query_plan",
    "rerank_documents",
    "retrieve",
    "retrieve_documents",
    "suppress_near_duplicates",
]


async def prepare_query_plan(ctx: PipelineContext) -> None:
    """Freeze request meaning before retrieval and context branches fan out."""
    if ctx.query_plan is not None:
        return
    query = ctx.rewritten_query or ctx.question

    def resolve_projects():
        from ...core.database import get_connection
        from ...core.project_resolution import resolve_project_ids

        with get_connection() as conn:
            explicit = resolve_project_ids(conn, ctx.user_id, ctx.question)
            if explicit:
                return explicit
            # An explicit follow-up may resolve its omitted project from context.
            # Standalone query expansion must not introduce a new file constraint.
            followup = re.search(
                r"继续|接着|刚才|上面|上述|那个项目|这个项目|"
                r"\b(?:continue|the previous|the above|that project|this project|"
                r"those tasks|these tasks)\b",
                ctx.question,
                re.IGNORECASE,
            )
            return resolve_project_ids(conn, ctx.user_id, query) if followup else ()

    project_ids = await asyncio.to_thread(resolve_projects) or ctx.restored_project_ids

    def registered_file_projects():
        # Extracted memory labels scope memory lookup. Only user-owned project
        # directory entries define a material binding, including an empty one.
        # A pending fact must not silently turn an existing meeting selection
        # into an empty file scope.
        with get_connection() as conn:
            registered = {
                row[0]
                for row in conn.execute(
                    "SELECT project_id FROM projects WHERE user_id=?", (ctx.user_id,)
                )
            }
            return tuple(pid for pid in project_ids if pid in registered)

    file_project_ids = await asyncio.to_thread(registered_file_projects) if project_ids else ()
    if file_project_ids and ctx.continuation_mode != "saved_snapshot":
        if ctx.memory_scope_override is None:
            ctx.memory_scope_override = tuple(ctx.file_ids or [])
        from ...core.database.projects import project_file_ids

        def scoped_files():
            with get_connection() as conn:
                ids = project_file_ids(conn, ctx.user_id, file_project_ids)
                if ctx.meeting_ids:
                    allowed = {
                        row[0]
                        for row in conn.execute(
                            "SELECT id FROM meeting_files WHERE meeting_id IN ("
                            + ",".join("?" for _ in ctx.meeting_ids)
                            + ")",
                            ctx.meeting_ids,
                        )
                    }
                    ids = [fid for fid in ids if fid in allowed]
                return [fid for fid in ids if not ctx.file_ids or fid in ctx.file_ids]

        # An empty list means all files in existing retrievers. Use an impossible
        # internal ID to keep an explicitly empty project scope fail-closed.
        from ...core.file_scope import FileScope

        scoped_ids = await asyncio.to_thread(scoped_files)
        ctx.resolved_file_scope = (
            FileScope("restricted", tuple(scoped_ids)) if scoped_ids else FileScope("empty")
        )
        ctx.file_ids = ctx.resolved_file_scope.retrieval_ids()
    from ..rag._query_plan import infer_historical_date_to

    if (
        ctx.date_from is None
        and ctx.date_to is None
        and not ctx.valid_at
        and not infer_historical_date_to(ctx.question)
    ):
        from ...core.meeting_time import resolve_meeting_time

        def resolve_time():
            with get_connection() as conn:
                return resolve_meeting_time(
                    conn,
                    ctx.user_id,
                    ctx.question,
                    file_ids=ctx.file_ids,
                    meeting_ids=ctx.meeting_ids or None,
                )

        start, end, resolution = await asyncio.to_thread(resolve_time)
        if resolution:
            ctx.date_from, ctx.date_to = start, end
            if end is not None and end < datetime.datetime.now(datetime.UTC).date():
                ctx.valid_at = datetime.datetime.combine(end, datetime.time.max, datetime.UTC)
            span = ctx.trace.start_span(
                "resolve_meeting_time",
                "routing",
                resolution=resolution,
                date_from=str(start) if start else None,
                date_to=str(end) if end else None,
            )
            span.finish("success" if resolution != "unresolved_meeting_anchor" else "degraded")
            ctx.query_scope_notice = resolution
            if resolution == "unresolved_meeting_anchor":
                ctx.file_ids = [-1]
                ctx.memory_scope_override = (-1,)

    ctx.known_speakers = await asyncio.to_thread(
        _load_known_speakers,
        ctx.meeting_ids,
        user_id=ctx.user_id,
    )
    ctx.query_plan = build_query_plan(
        original_query=ctx.question,
        resolved_query=query,
        known_speakers=ctx.known_speakers,
        meeting_ids=ctx.meeting_ids,
        file_ids=ctx.file_ids,
        file_types=ctx.file_types,
        date_from=ctx.date_from,
        date_to=ctx.date_to,
        valid_at=ctx.valid_at,
        known_at=ctx.known_at,
        project_ids=project_ids,
    )
    ctx.date_to = ctx.query_plan.date_to
    ctx.query_analysis = ctx.query_plan.analysis


async def retrieve_documents(ctx: PipelineContext) -> None:
    """Retrieve relevant meeting chunks from the vector store."""
    ctx.trace.start_span("retrieve", "retrieve")
    if ctx.query_plan is None:
        await prepare_query_plan(ctx)
    plan = ctx.query_plan
    if plan is None:  # Defensive guard for custom pipeline integrations.
        raise RuntimeError("Query plan preparation did not publish a plan")
    if ctx.continuation_mode == "saved_snapshot" and ctx.snapshot_restored:
        # Evidence was reconstructed from the last completed server-written
        # answer. Keep it isolated from today's index state.
        ctx.retrieval_candidate_count = len(ctx.docs)
        ctx.trace.finish_span("retrieve", "skipped")
        return
    from ._query_routes import is_recorded_fact_request

    if is_recorded_fact_request(ctx.question, ctx.memory_mode):
        ctx.docs = []
        ctx.retrieval_candidate_count = 0
        ctx.trace.finish_span("retrieve", "skipped")
        return
    query = ctx.rewritten_query or ctx.question

    # Cache the query embedding so summary scoring can reuse it without
    # re-embedding.  The _QueryCachedEmbeddings wrapper deduplicates, so
    # the Chroma call below will hit the cache.
    if ctx.query_embedding is None and ctx.rag_mode != "bm25":
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
    requested_top_k = ctx.top_k
    effective_k = determine_adaptive_top_k(
        ctx.question,
        ctx.top_k,
        is_broad_recall=is_broad_recall,
    )
    if ctx.file_ids:
        effective_k = max(effective_k, 12)
    elif ctx.meeting_ids:
        effective_k = max(effective_k, settings.TOP_K_MEETING_SCOPED_FLOOR)
    if (
        requested_top_k is None
        and ctx.query_plan is not None
        and ctx.query_plan.intent == "exhaustive"
    ):
        effective_k = max(effective_k, settings.SUMMARY_INTENT_TOP_K)
    # Persist the resolved budget so reranking and the final selector apply
    # the same contract as candidate retrieval. Previously they fell back to
    # settings.TOP_K and silently discarded the adaptive/exhaustive reserve.
    ctx.top_k = effective_k

    known_speakers = ctx.known_speakers
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

    # Variants affect only document retrieval. The immutable request plan was
    # already published before parallel memory/session/entity branches began.
    queries = list(dict.fromkeys([*plan.semantic_queries, *queries[1:]]))

    try:
        qa = None
        if ctx.rag_mode == "bm25":
            # Fast standalone lookups use the local lexical index directly.
            # This intentionally bypasses summary-router/funnel embedding
            # calls; strict meeting/file filters are still applied by retrieve.
            docs, qa = await asyncio.to_thread(
                retrieve,
                query,
                ctx.meeting_ids,
                ctx.file_ids,
                effective_k,
                fetch_multiplier,
                ctx.file_types,
                ctx.date_from,
                ctx.date_to,
                ctx.trace,
                ctx.rag_mode,
                known_speakers,
                user_id=ctx.user_id,
                analysis_query=ctx.question,
                lexical_query=(
                    ctx.query_plan.lexical_queries[0] if ctx.query_plan else ctx.question
                ),
            )
            ctx.docs = docs
            if not ctx.docs and (ctx.file_ids or (ctx.meeting_ids and len(ctx.meeting_ids) == 1)):
                # A sparse/stale FTS index must not turn a one-file scoped
                # query into a false "no evidence" answer. Use a bounded local
                # recency fallback; semantic retrieval remains reserved for
                # the full path so a BM25 miss cannot blow the latency SLO.
                single_file_scope = bool(ctx.file_ids and len(ctx.file_ids) == 1)
                if not single_file_scope and ctx.meeting_ids:
                    single_file_scope = (
                        await asyncio.to_thread(_count_files_in_meetings, ctx.meeting_ids) <= 1
                    )
                if single_file_scope:
                    ctx.docs = await asyncio.to_thread(
                        _scoped_latest_chunks, ctx, max(1, min(effective_k, 4))
                    )
        elif is_broad_recall:
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
                            user_id=ctx.user_id,
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
                        # A summary match is a relevance prior, not an access
                        # boundary.  Keep global recall available for evidence
                        # outside the routed meetings.
                        ctx.meeting_priors = dict(meeting_route)
                        ctx.trace.start_span(
                            "meeting_router",
                            "retrieve",
                            parent_label="retrieve",
                            hits=len(meeting_route),
                        ).finish("success")
                        try:
                            from ...core.metrics import MEETING_SUMMARY_ROUTER_HITS

                            MEETING_SUMMARY_ROUTER_HITS.labels(result="soft_prior").inc()
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

        retrieval_was_empty = not ctx.docs

        # Rewrites and variants may improve recall, but must never erase
        # speaker/time constraints from the user's original question.
        qa = ctx.query_plan.analysis if ctx.query_plan is not None else qa
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
        if qa and qa.temporal_hint and ctx.docs:
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

        ctx.docs = apply_meeting_evidence_policy(
            ctx.docs,
            query=ctx.question,
            user_id=ctx.user_id,
            known_at=ctx.known_at,
        )

        # Speaker/time-scoped questions retain their strict evidence boundaries.
        if not qa or (not qa.speaker_names and not qa.temporal_hint):
            from ..rag._meeting_structure import expand_meeting_evidence

            ctx.docs = await asyncio.to_thread(
                expand_meeting_evidence, ctx.docs, user_id=ctx.user_id, query=ctx.question
            )

        # D2: Retry with raw question when rewritten query returns zero docs (unscoped).
        _retry_used = False
        if (
            not ctx.docs
            and retrieval_was_empty
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
                if qa.speaker_names and ctx.docs:
                    ctx.docs = _apply_speaker_filter(ctx.docs, qa, ctx.meeting_ids)
                if qa.temporal_hint and ctx.docs:
                    ctx.docs = _apply_temporal_filter(ctx.docs, qa.temporal_hint)
            ctx.docs = apply_meeting_evidence_policy(
                ctx.docs,
                query=ctx.question,
                user_id=ctx.user_id,
                known_at=ctx.known_at,
            )

        ctx.retrieval_candidate_count = len(ctx.docs)
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


def commit_anchor_for_success(ctx: PipelineContext) -> None:
    """Persist an anchor from the final reranked context after answer success."""
    if not ctx.degraded:
        _write_anchor_from_docs(ctx, ctx.top_k or settings.TOP_K)
