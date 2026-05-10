"""Broad recall retrieval: per-file fair retrieval when no files are selected.

Extracted from ``_steps_retrieve.py`` to keep that module focused on the
top-level ``retrieve_documents`` entry point and anchor management.
"""

from __future__ import annotations

import asyncio
import contextlib

from ...core.config import settings
from ...core.database import get_connection
from ...core.metrics import (
    BROAD_RECALL_MQ_UNIQUE_FILES_ADDED,
    BROAD_RECALL_MQ_VARIANT_RUNS,
    FAIR_RETRIEVE_CHUNKS_PER_FILE,
)
from ..rag import retrieve
from ..rag._fair_retriever import fair_retrieve_per_file
from ..rag._query import is_summary_intent
from ..rag._query_analysis import QueryAnalysis
from ..rag._scope_types import BroadRecallContext, ScopeSelection
from ._common import logger
from ._context import PipelineContext
from ._retrieve_utils import (
    _dedup_docs,
    _sorted_by_score,
    _vector_score_lower_is_better,
)

_DEFAULT_RETRIEVE = retrieve


def _count_files_in_meetings(meeting_ids: list[int] | None) -> int:
    """Count distinct ready files across the given meetings."""
    if not meeting_ids:
        return 0
    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in meeting_ids)
            row = conn.execute(
                f"SELECT COUNT(DISTINCT id) AS n FROM meeting_files "
                f"WHERE meeting_id IN ({placeholders}) AND status = 'ready'",
                meeting_ids,
            ).fetchone()
            return int(row["n"]) if row and row["n"] else 0
    except Exception:
        logger.debug("Failed to count files in meetings", exc_info=True)
        return 0


async def _retrieve_broad_recall(
    ctx: PipelineContext,
    queries: list[str],
    effective_k: int,
    known_speakers: list[str],
    anchor_meeting_ids: list[int] | None,
    anchor_file_ids: list[int] | None,
    strategy,
    target_files: int,
    scoped_meeting_ids: list[int] | None,
) -> tuple[list[dict], QueryAnalysis | None]:
    """Broad recall mode: per-file fair retrieval when no files selected.

    *queries* is a list of query variants (multi-query lifted to input dimension).
    When only one query is present, behaves identically to the single-query path.

    File scoping strategy is controlled by ``RAG_FILE_SCOPING_MODE``:
      "router_and_funnel" — summary router + funnel parallel, zigzag merge
      "funnel_only"       — skip summary router, funnel does all file selection
      "router_pre_filter" — router narrows meeting scope, funnel within those meetings
      "router_only"       — router selects files directly (no funnel narrow)
    """
    from ...core.database import get_file_metadata_bulk
    from ._retrieve_routing import compute_chunk_budget

    primary_query = queries[0]
    summary_intent = is_summary_intent(ctx.question)

    if len(queries) == 1:
        selection = await strategy.select_scope(
            primary_query,
            scoped_meeting_ids or None,
            anchor_meeting_ids,
            anchor_file_ids,
            target_files,
            ctx.trace,
            ctx.rag_mode,
            known_speakers,
            summary_intent=summary_intent,
        )
    else:
        selection = await _multi_query_select_scope(
            strategy,
            queries,
            ctx,
            anchor_meeting_ids,
            anchor_file_ids,
            target_files,
            known_speakers,
            scoped_meeting_ids=scoped_meeting_ids or None,
            summary_intent=summary_intent,
        )
    scope_file_ids = selection.scope_file_ids
    file_scores = selection.file_scores
    docs_by_file = selection.docs_by_file

    if not scope_file_ids:
        return [], None

    # Compute adaptive chunk allocation
    target_total = max(effective_k * 2, 16)
    if is_summary_intent(ctx.question):
        target_total = max(target_total, settings.SUMMARY_INTENT_TOP_K * 2)
    # When user pinned a meeting, ensure budget covers all files fairly
    meeting_pinned = bool(ctx.meeting_ids and not ctx.file_ids)
    if meeting_pinned:
        file_count = _count_files_in_meetings(ctx.meeting_ids)
        target_total = max(
            target_total,
            file_count * settings.RAG_MIN_CHUNKS_PER_FILE * 3,
        )

    file_metadata: dict[int, dict] | None = None
    if settings.RAG_FAIR_ADAPTIVE_CHUNKS and file_scores:
        try:
            with get_connection() as conn:
                file_metadata = get_file_metadata_bulk(conn, scope_file_ids)
        except Exception:
            logger.debug("File metadata fetch failed", exc_info=True)

    chunks_per_file = compute_chunk_budget(
        scope_file_ids,
        file_scores,
        file_metadata,
        target_total=target_total,
        n_variants=len(queries),
        min_per_file=settings.RAG_MIN_CHUNKS_PER_FILE,
        max_per_file=16,
        uniform_for_single_meeting=meeting_pinned,
    )

    # Defend against zero budget (e.g. from adaptive calculation when every file
    # has zero estimated tokens). Passing zero to fair_retrieve_per_file would
    # return nothing and waste the retrieval call.
    total_budget = (
        chunks_per_file if isinstance(chunks_per_file, int) else sum(chunks_per_file.values())
    )
    if total_budget <= 0:
        logger.warning(
            "Broad-recall budget is zero for %d files; returning empty",
            len(scope_file_ids),
        )
        return [], None

    uniform_val = chunks_per_file if isinstance(chunks_per_file, int) else "adaptive"
    FAIR_RETRIEVE_CHUNKS_PER_FILE.observe(
        chunks_per_file
        if isinstance(chunks_per_file, int)
        else sum(chunks_per_file.values()) // max(len(chunks_per_file), 1)
    )
    ctx.trace.start_span(
        "fair_retrieve",
        "retrieve",
        parent_label="retrieve",
        file_count=len(scope_file_ids),
        chunks_per_file=uniform_val,
    )

    if len(queries) == 1:
        docs = await fair_retrieve_per_file(
            primary_query,
            scope_file_ids,
            chunks_per_file=chunks_per_file,
            trace=ctx.trace,
            rag_mode=ctx.rag_mode,
            known_speakers=known_speakers,
            cached_docs=docs_by_file,
        )
    else:

        async def _run_variant(idx: int, q: str) -> list[dict]:
            ctx.trace.start_span(
                f"fair_retrieve.variant_{idx}",
                "retrieve",
                parent_label="retrieve",
                variant_index=idx,
            )
            try:
                return await fair_retrieve_per_file(
                    q,
                    scope_file_ids,
                    chunks_per_file=chunks_per_file,
                    trace=ctx.trace,
                    rag_mode=ctx.rag_mode,
                    known_speakers=known_speakers,
                    cached_docs=docs_by_file,
                )
            finally:
                ctx.trace.finish_span(f"fair_retrieve.variant_{idx}")

        per_q = await asyncio.gather(*[_run_variant(i, q) for i, q in enumerate(queries)])
        lower_is_better = _vector_score_lower_is_better()
        merged = _dedup_docs(list(per_q), lower_is_better=lower_is_better)
        merged = _sorted_by_score(merged, lower_is_better=lower_is_better)
        docs = merged[: max(effective_k * 2, target_total)]

    ctx.scope_file_ids = scope_file_ids
    ctx.trace.finish_span("fair_retrieve")
    return docs, None


async def _retrieve_scoped(
    ctx: PipelineContext,
    queries: list[str],
    effective_k: int,
    fetch_multiplier: int,
    known_speakers: list[str],
    *,
    retrieve_fn=None,
) -> tuple[list[dict], QueryAnalysis | None]:
    """Standard scoped retrieval with file or meeting scope.

    Uses plain ``retrieve`` directly — scoped path always has file_ids,
    so the hierarchical wrapper would degenerate to plain retrieve anyway.
    Anchor is not passed: the user explicitly selected files, so session-level
    scope hints are unnecessary and could crowd out the user's intent.
    Supports multi-query via *queries* list.
    """
    if retrieve_fn is None:
        retrieve_fn = retrieve
        if retrieve_fn is _DEFAULT_RETRIEVE:
            from . import _steps_retrieve

            steps_retrieve = getattr(_steps_retrieve, "retrieve", _DEFAULT_RETRIEVE)
            if steps_retrieve is not _DEFAULT_RETRIEVE:
                retrieve_fn = steps_retrieve

    if len(queries) == 1:
        docs, qa = await asyncio.to_thread(
            retrieve_fn,
            queries[0],
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
        )
        return docs, qa

    per_query_k = max(effective_k // len(queries), 3) * fetch_multiplier
    raw = await asyncio.gather(
        *[
            asyncio.to_thread(
                retrieve_fn,
                q,
                ctx.meeting_ids,
                ctx.file_ids,
                per_query_k,
                1,
                ctx.file_types,
                ctx.date_from,
                ctx.date_to,
                ctx.trace,
                ctx.rag_mode,
                known_speakers,
            )
            for q in queries
        ]
    )
    all_docs = [r[0] for r in raw]
    qa = raw[0][1] if raw else None
    lower_is_better = _vector_score_lower_is_better()
    merged = _dedup_docs(all_docs, lower_is_better=lower_is_better)
    merged = _sorted_by_score(merged, lower_is_better=lower_is_better)
    return merged[: effective_k * fetch_multiplier], qa


async def _multi_query_select_scope(
    strategy,
    queries: list[str],
    ctx: PipelineContext,
    anchor_meeting_ids: list[int] | None,
    anchor_file_ids: list[int] | None,
    target_files: int,
    known_speakers: list[str],
    scoped_meeting_ids: list[int] | None = None,
    summary_intent: bool = False,
) -> ScopeSelection:
    """Run the file scoping strategy per variant and merge via file-level RRF.

    H2: lifts multi-query into the file-selection layer so each variant
    contributes a different lens on what files are relevant — instead of
    only widening the chunk pool inside an already-selected scope.

    The merge strategy mirrors funnel/router merging: file-level RRF using
    ``RAG_FUNNEL_RRF_K`` (or zigzag if ``RAG_BROAD_RECALL_MQ_MERGE``
    forces it).  Anchor injection happens after the merge so anchor
    semantics stay consistent with the single-query path.
    """
    from ..rag._anchor_inject import apply_anchor_evict

    # Use scoped_meeting_ids from meeting router when available, otherwise
    # fall back to user-selected ctx.meeting_ids.
    effective_meeting_ids = scoped_meeting_ids or ctx.meeting_ids

    # Request-scoped context: all variants share one wide-fetch doc pool.
    broad_ctx = BroadRecallContext()

    selections = await asyncio.gather(
        *[
            strategy.select_scope(
                q,
                effective_meeting_ids,
                anchor_meeting_ids,
                None,  # defer anchor injection until after merge
                target_files,
                ctx.trace,
                ctx.rag_mode,
                known_speakers,
                summary_intent=summary_intent,
                broad_recall_ctx=broad_ctx,
            )
            for q in queries
        ]
    )
    with contextlib.suppress(Exception):
        BROAD_RECALL_MQ_VARIANT_RUNS.inc(len(queries))

    # Build a single ranked list per variant (preserve scope_file_ids order
    # which already encodes the merged router+funnel rank for that variant).
    variant_lists: list[list[tuple[int, float]]] = []
    merged_scores: dict[int, float] = {}
    merged_docs_by_file: dict[int, list[dict]] = {}
    for sel in selections:
        if not sel.scope_file_ids:
            continue
        variant_lists.append([(fid, sel.file_scores.get(fid, 0.0)) for fid in sel.scope_file_ids])
        for fid, score in sel.file_scores.items():
            merged_scores[fid] = max(merged_scores.get(fid, 0.0), score)
        for fid, docs in (sel.docs_by_file or {}).items():
            existing = merged_docs_by_file.setdefault(fid, [])
            seen_keys: set[str] = {
                f"{(d.get('metadata') or {}).get('file_id')}:"
                f"{(d.get('metadata') or {}).get('chunk_index')}:"
                f"{(d.get('content') or '')[:80]}"
                for d in existing
            }
            for d in docs:
                key = (
                    f"{(d.get('metadata') or {}).get('file_id')}:"
                    f"{(d.get('metadata') or {}).get('chunk_index')}:"
                    f"{(d.get('content') or '')[:80]}"
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    existing.append(d)

    if not variant_lists:
        return ScopeSelection()

    if settings.RAG_BROAD_RECALL_MQ_MERGE == "zigzag":
        merged_scope: list[int] = []
        seen: set[int] = set()
        max_len = max(len(v) for v in variant_lists)
        for rank in range(max_len):
            for variant in variant_lists:
                if rank >= len(variant):
                    continue
                fid = variant[rank][0]
                if fid in seen:
                    continue
                seen.add(fid)
                merged_scope.append(fid)
                if len(merged_scope) >= target_files:
                    break
            if len(merged_scope) >= target_files:
                break
    else:
        # File-level RRF across variants (default)
        rrf_k = settings.RAG_FUNNEL_RRF_K
        rrf: dict[int, float] = {}
        for variant in variant_lists:
            for rank, (fid, _) in enumerate(variant):
                rrf[fid] = rrf.get(fid, 0.0) + 1.0 / (rrf_k + rank + 1)
        merged_scope = [fid for fid, _ in sorted(rrf.items(), key=lambda x: (-x[1], x[0]))][
            :target_files
        ]

    # Apply anchor injection on the merged scope (same as single-query path)
    if settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL and anchor_file_ids:
        merged_scope, _ = apply_anchor_evict(
            merged_scope,
            anchor_file_ids,
            cap=target_files,
            quota_ratio=settings.RAG_ANCHOR_QUOTA_RATIO,
        )

    # Anchor-only files inherit the median-derived fallback score per H1.
    if merged_scope:
        present_scores = [merged_scores[fid] for fid in merged_scope if fid in merged_scores]
        if present_scores:
            sorted_scores = sorted(present_scores)
            median = sorted_scores[len(sorted_scores) // 2]
            fallback = settings.RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO * median
        else:
            fallback = 0.5
        for fid in merged_scope:
            merged_scores.setdefault(fid, fallback)

    # Record how many files were added purely from variant diversity (H2 contribution).
    if variant_lists:
        primary_fids = {fid for fid, _ in variant_lists[0]}
        unique_added = sum(1 for fid in merged_scope if fid not in primary_fids)
        with contextlib.suppress(Exception):
            BROAD_RECALL_MQ_UNIQUE_FILES_ADDED.observe(unique_added)

    final_scores = {fid: merged_scores.get(fid, 0.0) for fid in merged_scope}
    final_docs = {fid: merged_docs_by_file.get(fid, []) for fid in merged_scope}
    return ScopeSelection(
        scope_file_ids=list(merged_scope),
        file_scores=final_scores,
        docs_by_file=final_docs,
    )


async def _retry_raw_question(
    ctx: PipelineContext,
    effective_k: int,
    fetch_multiplier: int,
    known_speakers: list[str],
) -> tuple[list[dict], QueryAnalysis | None]:
    """Retry retrieval with the raw (non-rewritten) question."""
    ctx.trace.start_span("retrieve.retry_raw", "retrieve", parent_label="retrieve")
    try:
        docs, qa = await _retrieve_scoped(
            ctx,
            [ctx.question],
            effective_k,
            fetch_multiplier,
            known_speakers,
        )
        if docs:
            logger.info(
                "Retrieve retry with raw question succeeded: %d docs (rewritten='%s' returned 0)",
                len(docs),
                ctx.rewritten_query[:60],
            )
            return docs, qa
        return [], None
    except Exception:
        logger.warning("Retrieve retry with raw question failed", exc_info=True)
        return [], None
    finally:
        ctx.trace.finish_span("retrieve.retry_raw")
