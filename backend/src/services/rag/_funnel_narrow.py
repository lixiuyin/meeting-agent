"""Funnel narrow: chunk-level file selection to calibrate summary router output.

Runs a single wide fetch, aggregates scores by file, merges with the router's
file selection via rank-aware union, then applies anchor guarantees.

Wide fetch docs are grouped by file_id and returned as a cache so that
fair_retrieve_per_file can skip redundant Chroma calls for files already
well-represented in the wide fetch results.
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import TYPE_CHECKING

from ...core.config import settings
from ...core.metrics import (
    FUNNEL_NARROW_ANCHOR_EVICT,
    FUNNEL_NARROW_EMPTY_SCOPE_TOTAL,
    FUNNEL_NARROW_EVIDENCE_FILTER_RATIO,
    FUNNEL_NARROW_MULTIMODAL_DOCS,
    FUNNEL_NARROW_ROUTER_ONLY_FILES,
    FUNNEL_NARROW_ROUTER_OVERLAP_RATIO,
    FUNNEL_NARROW_SCOPE_SIZE,
    FUNNEL_NARROW_WIDE_K_USED,
)
from ._anchor_inject import apply_anchor_evict
from ._funnel import aggregate_by_file_scored, fetch_file_title_priors, normalize_scores
from ._retriever import retrieve
from ._scope_types import ScopeSelection
from ._vector import _vector_score_lower_is_better

if TYPE_CHECKING:
    from ...core.trace import TraceContext

logger = logging.getLogger(__name__)


def narrow_scope_via_funnel(
    primary_query: str,
    router_scope: list[tuple[int, float]] | None,
    meeting_ids: list[int] | None,
    anchor_meeting_ids: list[int] | None,
    anchor_file_ids: list[int] | None,
    trace: TraceContext | None,
    rag_mode: str | None,
    known_speakers: list[str] | None,
    *,
    target_files: int,
    min_chunk_evidence: float,
    prefetched_wide_docs: list[dict] | None = None,
    summary_intent: bool = False,
    user_id: str | None = None,
) -> ScopeSelection:
    """Select broad recall scope files via chunk-evidence aggregation.

    Returns a :class:`ScopeSelection` whose ``file_scores`` is normalised in
    [0, 1] and whose ``docs_by_file`` groups wide-fetch docs by ``file_id``
    so that downstream fair retrieval can skip redundant Chroma calls.

    Score source priority: funnel score > normalised router score > 1.0
    (anchor-only fallback).

    When ``prefetched_wide_docs`` is provided (M4: parallel router + wide
    fetch), the internal wide_fetch call is skipped entirely.  The caller
    is responsible for honouring the meeting_ids | anchor_meeting_ids scope.
    """
    if prefetched_wide_docs is None:
        docs = _wide_fetch(
            primary_query,
            meeting_ids,
            anchor_meeting_ids,
            trace,
            rag_mode,
            known_speakers,
            user_id=user_id,
        )
    else:
        docs = prefetched_wide_docs
    if not docs:
        return _fallback_from_router(router_scope, target_files)

    # Top-N router files are protected from evidence-floor eviction so that a
    # high-confidence summary match isn't discarded just because its chunk
    # evidence is weak (OR semantics instead of AND).  Without this, when the
    # file router scores file A top-1 but A's chunks are sparse in wide fetch,
    # the evidence floor silently drops A — and the RRF merge can't recover it.
    _protect_count = max(1, target_files // 4)
    router_protected: set[int] | None = None
    if router_scope:
        router_protected = {fid for fid, _ in router_scope[:_protect_count]}

    funnel_candidates, funnel_filtered = _aggregate_funnel_chunks(
        docs,
        target_files=target_files,
        min_chunk_evidence=min_chunk_evidence,
        trace=trace,
        primary_query=primary_query,
        router_protected_fids=router_protected,
        summary_intent=summary_intent,
    )
    if not funnel_filtered and not router_scope:
        FUNNEL_NARROW_EMPTY_SCOPE_TOTAL.inc()
        logger.warning(
            "funnel_narrow returned empty scope: primary_query=%r "
            "target_files=%d min_chunk_evidence=%.2f",
            primary_query,
            target_files,
            min_chunk_evidence,
        )
        return ScopeSelection()

    scope = _merge_router_funnel(
        router_scope,
        funnel_filtered,
        target_files=target_files,
        strategy=settings.RAG_FUNNEL_MERGE_STRATEGY,
    )
    scope, anchor_appended = _inject_anchor(scope, anchor_file_ids, target_files=target_files)

    file_scores = _compute_file_scores(scope, funnel_candidates, router_scope)
    docs_by_file = _group_docs_by_file(docs)

    _emit_merge_trace(
        trace,
        router_scope,
        funnel_filtered,
        scope,
        anchor_appended,
        len(docs),
    )
    _record_router_funnel_overlap(router_scope, funnel_filtered, target_files)
    _record_scope_metrics(scope, funnel_filtered)

    return ScopeSelection(
        scope_file_ids=scope,
        file_scores=file_scores,
        docs_by_file=docs_by_file,
    )


def wide_fetch_for_funnel(
    primary_query: str,
    meeting_ids: list[int] | None,
    anchor_meeting_ids: list[int] | None,
    trace: TraceContext | None,
    rag_mode: str | None,
    known_speakers: list[str] | None,
    *,
    user_id: str | None = None,
) -> list[dict]:
    """Public wrapper for the wide-fetch step (M4: parallel router/funnel).

    Strategies that want to overlap wide-fetch latency with the summary
    router call can invoke this from a worker thread, then pass the result
    to :func:`narrow_scope_via_funnel` via ``prefetched_wide_docs``.
    """
    return _wide_fetch(
        primary_query,
        meeting_ids,
        anchor_meeting_ids,
        trace,
        rag_mode,
        known_speakers,
        user_id=user_id,
    )


def _wide_fetch(
    primary_query: str,
    meeting_ids: list[int] | None,
    anchor_meeting_ids: list[int] | None,
    trace: TraceContext | None,
    rag_mode: str | None,
    known_speakers: list[str] | None,
    *,
    user_id: str | None = None,
) -> list[dict]:
    """Single Chroma call covering both meeting scope and anchor meetings."""
    wide_fetch_meeting_ids = list(set((meeting_ids or []) + (anchor_meeting_ids or []))) or None
    wide_k = _resolve_wide_k(wide_fetch_meeting_ids)
    with contextlib.suppress(Exception):
        FUNNEL_NARROW_WIDE_K_USED.observe(wide_k)
    lower_is_better = _vector_score_lower_is_better()

    span = (
        trace.start_span("funnel_narrow.wide", "retrieve", parent_label="retrieve", wide_k=wide_k)
        if trace
        else None
    )
    docs, _ = retrieve(
        primary_query,
        meeting_ids=wide_fetch_meeting_ids,
        file_ids=None,
        top_k=wide_k,
        fetch_multiplier=1,
        trace=trace,
        rag_mode=rag_mode,
        known_speakers=known_speakers,
        _apply_diversity=False,
        user_id=user_id,
    )
    if span:
        span.finish("success")
        span.metadata["pool_size"] = len(docs)

    if settings.RAG_FUNNEL_MULTIMODAL_ENABLED and settings.RAGANYTHING_ENABLED:
        mm_docs = _wide_fetch_multimodal(
            primary_query,
            wide_fetch_meeting_ids,
            wide_k,
            user_id=user_id,
        )
        if mm_docs:
            docs = _merge_multimodal_docs(docs, mm_docs)
            with contextlib.suppress(Exception):
                FUNNEL_NARROW_MULTIMODAL_DOCS.observe(len(mm_docs))

    if not docs:
        return []
    return normalize_scores(docs, lower_is_better=lower_is_better)


def _wide_fetch_multimodal(
    primary_query: str,
    scope_meeting_ids: list[int] | None,
    wide_k: int,
    *,
    user_id: str | None = None,
) -> list[dict]:
    """Optionally pull multimodal store results for funnel-side fairness.

    Failures degrade silently — the native wide-fetch pool is already a
    valid input for the funnel.
    """
    try:
        from ._raganything import retrieve_with_raganything

        filters: dict[str, list[int] | str] = {}
        if scope_meeting_ids:
            filters["meeting_id"] = scope_meeting_ids
        if user_id is not None:
            filters["user_id"] = user_id
        return retrieve_with_raganything(primary_query, top_k=wide_k, filters=filters) or []
    except Exception:
        logger.debug("Multimodal wide-fetch skipped due to error", exc_info=True)
        return []


def _merge_multimodal_docs(native: list[dict], multimodal: list[dict]) -> list[dict]:
    """Append multimodal docs to native pool, deduping by (meeting_id, file_id, chunk_index)."""
    seen: set[str] = set()
    merged: list[dict] = []
    for doc in list(native) + list(multimodal):
        meta = doc.get("metadata") or {}
        key = f"{meta.get('meeting_id')}:{meta.get('file_id')}:{meta.get('chunk_index')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(doc)
    return merged


def _resolve_wide_k(scope_meeting_ids: list[int] | None) -> int:
    """Compute wide_k = clamp(MIN, base * log_factor, MAX) when bounds set.

    The log factor scales with the number of chunks in the in-scope meetings:
    on small corpora the legacy fixed value is plenty; on large corpora we
    need more samples for the funnel to discover relevant tail files.

    When the scope is a single user-pinned meeting with a small pool, a
    larger constant multiplier is used instead of log scaling so that all
    files in that meeting have enough chunks represented in the wide pool.
    """
    base = settings.TOP_K * settings.RAG_FUNNEL_FETCH_MULTIPLIER
    lo = settings.RAG_FUNNEL_WIDE_K_MIN
    hi = settings.RAG_FUNNEL_WIDE_K_MAX
    if not lo and not hi:
        return base

    pool_size = _estimate_scope_chunk_count(scope_meeting_ids)
    log_factor = 1.0
    if pool_size > 0:
        # Single meeting with small pool: use larger multiplier to ensure
        # all files in the meeting are represented in the wide fetch.
        if scope_meeting_ids is not None and len(scope_meeting_ids) <= 1 and pool_size < 200:
            log_factor = max(2.0, min(4.0, pool_size / max(base, 1) * 2))
        else:
            # log_factor doubles every 10x chunks past the first 100; saturates at 4.
            log_factor = max(1.0, min(4.0, math.log10(max(pool_size, 10))))
    candidate = round(base * log_factor)

    if lo:
        candidate = max(candidate, lo)
    if hi:
        candidate = min(candidate, hi)
    # Hard cap: log scaling may push candidate above the startup-validated ceiling.
    candidate = min(candidate, settings._MAX_WIDE_FETCH_SIZE)
    return max(candidate, 1)


def _estimate_scope_chunk_count(scope_meeting_ids: list[int] | None) -> int:
    """Best-effort count of indexed chunks in the in-scope meetings.

    Uses the BM25 index as a proxy for total chunk count (it is kept in sync
    with the vector store on every indexer write).  Returns 0 if the count
    cannot be determined — the wide_k resolver then falls back to the legacy
    fixed formula.
    """
    try:
        from ...core.database import get_connection

        with get_connection() as conn:
            if scope_meeting_ids:
                placeholders = ",".join("?" for _ in scope_meeting_ids)
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM bm25_index WHERE meeting_id IN ({placeholders})",
                    scope_meeting_ids,
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM bm25_index").fetchone()
            return int(row["n"]) if row and row["n"] is not None else 0
    except Exception:
        return 0


_SUMMARY_INTENT_EVIDENCE_FLOOR = 0.08


def _aggregate_funnel_chunks(
    docs: list[dict],
    *,
    target_files: int,
    min_chunk_evidence: float,
    trace: TraceContext | None,
    primary_query: str = "",
    router_protected_fids: set[int] | None = None,
    summary_intent: bool = False,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Roll chunk scores up to file scores and apply the evidence floor.

    Filtering mode is governed by ``RAG_FUNNEL_EVIDENCE_MODE``:
      * ``absolute``  — keep score >= min_chunk_evidence (legacy)
      * ``ratio``     — keep score >= min_chunk_evidence * top_score
      * ``percentile``— keep top (1 - min_chunk_evidence) fraction of files

    Files in *router_protected_fids* bypass the evidence floor so that a
    high router-confidence file with weak chunk evidence isn't evicted.

    When *summary_intent* is True the evidence floor is lowered to
    ``_SUMMARY_INTENT_EVIDENCE_FLOOR`` so that sparse-but-relevant files
    survive on overview/summary queries.
    """
    prior_weights: dict[int, float] | None = None
    if settings.RAG_FUNNEL_FILE_PRIOR_ENABLED and primary_query:
        candidate_fids = list(
            {
                fid
                for doc in docs
                if isinstance(fid := (doc.get("metadata") or {}).get("file_id"), int)
            }
        )
        if candidate_fids:
            prior_weights = fetch_file_title_priors(primary_query, candidate_fids)

    funnel_candidates = aggregate_by_file_scored(
        docs,
        meeting_ids=None,
        top_n=target_files * 2,
        method=settings.RAG_FUNNEL_AGGREGATION,
        agg_top_k=settings.RAG_FUNNEL_AGG_TOP_K,
        prior_weights=prior_weights,
    )
    effective_evidence = _SUMMARY_INTENT_EVIDENCE_FLOOR if summary_intent else min_chunk_evidence
    # H-RAG-2: Summary intent uses absolute mode so sparse files aren't
    # unfairly penalized by a low top_score in ratio mode.
    effective_mode = settings.RAG_FUNNEL_EVIDENCE_MODE
    if summary_intent and effective_mode == "ratio":
        effective_mode = "absolute"
    funnel_filtered = _apply_evidence_threshold(
        funnel_candidates,
        mode=effective_mode,
        threshold=effective_evidence,
        protected_fids=router_protected_fids,
    )
    if funnel_candidates:
        with contextlib.suppress(Exception):
            FUNNEL_NARROW_EVIDENCE_FILTER_RATIO.observe(
                len(funnel_filtered) / len(funnel_candidates)
            )
    if trace:
        trace.start_span(
            "funnel_narrow.aggregate",
            "retrieve",
            parent_label="retrieve",
            candidates=len(funnel_candidates),
            after_filter=len(funnel_filtered),
            evidence_mode=settings.RAG_FUNNEL_EVIDENCE_MODE,
        ).finish("success")
    return funnel_candidates, funnel_filtered


def _apply_evidence_threshold(
    candidates: list[tuple[int, float]],
    *,
    mode: str,
    threshold: float,
    protected_fids: set[int] | None = None,
) -> list[tuple[int, float]]:
    """Filter candidate (fid, score) pairs by the configured evidence mode.

    ``threshold`` is interpreted differently per mode (see caller docstring).
    Always preserves input order so callers can rely on rank semantics.

    Files in *protected_fids* are always retained regardless of score.
    """
    if not candidates:
        return []
    threshold = max(0.0, min(1.0, threshold))
    protect = protected_fids or set()

    if mode == "ratio":
        top = max(score for _, score in candidates)
        if top <= 0:
            return [(fid, score) for fid, score in candidates if fid in protect]
        cutoff = threshold * top
        return [(fid, score) for fid, score in candidates if score >= cutoff or fid in protect]

    if mode == "percentile":
        keep_n = max(1, round(len(candidates) * max(0.0, 1.0 - threshold)))
        kept = list(candidates[:keep_n])
        kept_fids = {fid for fid, _ in kept}
        for fid, score in candidates[keep_n:]:
            if fid in protect and fid not in kept_fids:
                kept.append((fid, score))
                kept_fids.add(fid)
        return kept

    # absolute (default / back-compat)
    return [(fid, score) for fid, score in candidates if score >= threshold or fid in protect]


def _inject_anchor(
    scope: list[int],
    anchor_file_ids: list[int] | None,
    *,
    target_files: int,
) -> tuple[list[int], int]:
    """Apply anchor evict policy and bump the metric counter."""
    if not (settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL and anchor_file_ids):
        return scope, 0
    effective_ratio = settings.RAG_ANCHOR_QUOTA_RATIO
    if effective_ratio <= 0 and target_files > 0:
        effective_ratio = 1 / target_files
    new_scope, anchor_appended = apply_anchor_evict(
        scope,
        anchor_file_ids,
        cap=target_files,
        quota_ratio=effective_ratio,
    )
    if anchor_appended:
        FUNNEL_NARROW_ANCHOR_EVICT.inc(anchor_appended)
    return new_scope, anchor_appended


def _compute_file_scores(
    scope: list[int],
    funnel_candidates: list[tuple[int, float]],
    router_scope: list[tuple[int, float]] | None,
) -> dict[int, float]:
    """Construct file_scores priority funnel > router > anchor-only fallback.

    Anchor-only files (no router/funnel evidence) get the median score of the
    in-scope pool, scaled by ``RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO``.  This
    prevents anchor files from monopolising downstream chunk-budget allocation
    or reranker tiebreaks while still giving them more weight than empty pool.
    Set the ratio to 1.0 to restore the legacy "anchor wins everything"
    behaviour, or to 0.0 to give anchor-only files no score floor at all.
    """
    funnel_norm = _normalize_to_unit(funnel_candidates)
    router_norm = _normalize_to_unit(router_scope or [])
    all_scores = list(funnel_norm.values()) + list(router_norm.values())
    if all_scores:
        sorted_scores = sorted(all_scores)
        n = len(sorted_scores)
        if n % 2 == 1:
            median = sorted_scores[n // 2]
        else:
            median = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2
        fallback = settings.RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO * median
    else:
        fallback = 0.5
    return {fid: funnel_norm.get(fid, router_norm.get(fid, fallback)) for fid in scope}


def _record_router_funnel_overlap(
    router_scope: list[tuple[int, float]] | None,
    funnel_filtered: list[tuple[int, float]],
    target_files: int,
) -> None:
    """Record router/funnel agreement ratio as a Prometheus histogram sample.

    Long-term low overlap is a signal that the file-level summary embeddings
    and the chunk-level evidence are diverging — a leading indicator of bad
    summaries or stale routing.
    """
    if not router_scope or not funnel_filtered or target_files <= 0:
        return
    router_set = {fid for fid, _ in router_scope}
    funnel_set = {fid for fid, _ in funnel_filtered}
    overlap = len(router_set & funnel_set)
    with contextlib.suppress(Exception):
        FUNNEL_NARROW_ROUTER_OVERLAP_RATIO.observe(overlap / target_files)


def _record_scope_metrics(
    scope: list[int],
    funnel_filtered: list[tuple[int, float]],
) -> None:
    """Record final scope size and router-only file count."""
    with contextlib.suppress(Exception):
        FUNNEL_NARROW_SCOPE_SIZE.observe(len(scope))
    if scope:
        funnel_fids = {fid for fid, _ in funnel_filtered}
        router_only = sum(1 for fid in scope if fid not in funnel_fids)
        with contextlib.suppress(Exception):
            FUNNEL_NARROW_ROUTER_ONLY_FILES.observe(router_only)


def _group_docs_by_file(docs: list[dict]) -> dict[int, list[dict]]:
    """Group wide-fetch docs by ``file_id`` for fair-retriever cache reuse."""
    grouped: dict[int, list[dict]] = {}
    for doc in docs:
        meta = doc.get("metadata") or {}
        fid = meta.get("file_id")
        if isinstance(fid, int):
            grouped.setdefault(fid, []).append(doc)
    return grouped


def _emit_merge_trace(
    trace: TraceContext | None,
    router_scope: list[tuple[int, float]] | None,
    funnel_filtered: list[tuple[int, float]],
    scope: list[int],
    anchor_appended: int,
    wide_pool: int,
) -> None:
    """Emit the funnel_narrow.merge span and structured log line."""
    if trace:
        trace.start_span(
            "funnel_narrow.merge",
            "retrieve",
            parent_label="retrieve",
            router_count=len(router_scope) if router_scope else 0,
            funnel_count=len(funnel_filtered),
            final_count=len(scope),
            anchor_appended=anchor_appended,
        ).finish("success")
    logger.info(
        "funnel narrow: router=%d funnel=%d intersect=%d final=%d anchor_evicted=%d wide_pool=%d",
        len(router_scope) if router_scope else 0,
        len(funnel_filtered),
        len({f for f, _ in (router_scope or [])} & {f for f, _ in funnel_filtered}),
        len(scope),
        anchor_appended,
        wide_pool,
    )


def _merge_router_funnel(
    router_scope: list[tuple[int, float]] | None,
    funnel_scored: list[tuple[int, float]],
    *,
    target_files: int,
    strategy: str,
) -> list[int]:
    """Merge router and funnel file selections.

    Dispatches on *strategy*:
      * ``"rrf"`` (default) — Reciprocal Rank Fusion; high-scoring funnel
        files keep their rank even when there is no overlap with router.
      * ``"zigzag"`` — legacy v3 strict interleave, preserved for rollback.
    """
    if strategy == "zigzag":
        return _rank_aware_union_zigzag(router_scope, funnel_scored, target_files)
    return _rank_aware_union_rrf(
        router_scope, funnel_scored, target_files, rrf_k=settings.RAG_FUNNEL_RRF_K
    )


def _rank_aware_union_rrf(
    router_scope: list[tuple[int, float]] | None,
    funnel_scored: list[tuple[int, float]],
    target_files: int,
    *,
    rrf_k: int = 60,
) -> list[int]:
    """Merge router and funnel via Reciprocal Rank Fusion.

    Each list contributes ``1 / (k + rank)`` per file; files appearing in
    both lists naturally receive a double contribution (the implicit
    "intersection first" property of zigzag is preserved without a special
    case).  Returned in descending RRF score; ties broken by router order
    so the legacy "router takes the lead" behaviour is preserved.

    Contract: ``router_scope`` must be ordered by relevance (best first).
    Callers are responsible for sorting before passing.  ``funnel_scored``
    follows the same contract.
    """
    if not router_scope and not funnel_scored:
        return []

    rrf: dict[int, float] = {}
    router_rank: dict[int, int] = {}
    for rank, (fid, _) in enumerate(router_scope or []):
        rrf[fid] = rrf.get(fid, 0.0) + 1.0 / (rrf_k + rank + 1)
        router_rank.setdefault(fid, rank)
    for rank, (fid, _) in enumerate(funnel_scored):
        rrf[fid] = rrf.get(fid, 0.0) + 1.0 / (rrf_k + rank + 1)

    # Sort by descending RRF score; tiebreak: router rank (lower wins),
    # then file_id for determinism in tests.
    def _key(fid: int) -> tuple[float, int, int]:
        return (-rrf[fid], router_rank.get(fid, 1_000_000), fid)

    return sorted(rrf.keys(), key=_key)[:target_files]


def _rank_aware_union_zigzag(
    router_scope: list[tuple[int, float]] | None,
    funnel_scored: list[tuple[int, float]],
    target_files: int,
) -> list[int]:
    """Merge router and funnel file selections via zigzag interleave.

    1. Intersection files (present in both) first, preserving router order.
    2. Then zigzag: alternate router-only / funnel-only.
    3. Cap at *target_files*.
    """
    router_top = [fid for fid, _ in (router_scope or [])]
    funnel_top = [fid for fid, _ in funnel_scored]
    funnel_set = set(funnel_top)
    router_set = set(router_top)

    if not router_top and not funnel_top:
        return []

    # Intersection (router order)
    intersection = [f for f in router_top if f in funnel_set]
    router_only = [f for f in router_top if f not in funnel_set]
    funnel_only = [f for f in funnel_top if f not in router_set]

    scope = intersection[:target_files]
    remaining = target_files - len(scope)

    # Zigzag interleave: alternate router-only / funnel-only
    i = j = 0
    while remaining > 0 and (i < len(router_only) or j < len(funnel_only)):
        if i < len(router_only):
            scope.append(router_only[i])
            i += 1
            remaining -= 1
            if remaining == 0:
                break
        if j < len(funnel_only):
            scope.append(funnel_only[j])
            j += 1
            remaining -= 1

    return scope


def _fallback_from_router(
    router_scope: list[tuple[int, float]] | None,
    target_files: int,
) -> ScopeSelection:
    """When wide fetch returns nothing, fall back to router output.

    Router scores are normalised to [0, 1] for parity with the funnel path
    so that downstream adaptive chunk allocation sees a consistent scale.
    """
    if router_scope:
        head = router_scope[:target_files]
        fids = [fid for fid, _ in head]
        return ScopeSelection(scope_file_ids=fids, file_scores=_normalize_to_unit(head))
    return ScopeSelection()


def _normalize_to_unit(scored: list[tuple[int, float]]) -> dict[int, float]:
    """Min-max normalise ``scored`` to a ``{file_id: score}`` map in [0, 1].

    * Empty input → empty map.
    * All values identical (or a single entry) → every entry maps to 1.0.
    * All values non-positive → every entry maps to 0.0.
    """
    if not scored:
        return {}
    values = [v for _, v in scored]
    hi, lo = max(values), min(values)
    if hi <= 0:
        return {fid: 0.0 for fid, _ in scored}
    if hi == lo:
        return {fid: 1.0 for fid, _ in scored}
    span = hi - lo
    return {fid: (v - lo) / span for fid, v in scored}
