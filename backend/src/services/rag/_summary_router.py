"""File-level routing using summary vector similarity + BM25 hybrid.

Given a query and an optional set of meeting_ids, returns the file_ids
whose summaries are most relevant.  When hybrid mode is enabled, vector
similarity and BM25 keyword matches are fused via RRF.

The router is intentionally fail-open: any error returns ``None`` so
callers can fall back to existing logic.
"""

from __future__ import annotations

import logging
from typing import Any

from ...core.config import settings
from ._rrf import _rrf_merge_multi
from ._summary_vectorstore import get_summary_vectorstore
from ._vector import _vector_score_lower_is_better, normalize_score

logger = logging.getLogger(__name__)


def _build_filter(
    meeting_ids: list[int] | None,
    user_id: str | None,
) -> dict[str, Any] | None:
    clauses: list[dict[str, Any]] = []
    if user_id is not None:
        clauses.append({"user_id": user_id})
    if meeting_ids and len(meeting_ids) == 1:
        clauses.append({"meeting_id": int(meeting_ids[0])})
    elif meeting_ids:
        clauses.append({"meeting_id": {"$in": [int(m) for m in meeting_ids]}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _bm25_file_search(
    query: str,
    meeting_ids: list[int] | None,
    top_k: int,
    user_id: str | None,
) -> list[dict] | None:
    """Run file-level BM25 search on summary FTS5 index.

    Returns list of dicts with file_id, meeting_id, score; or None on error.
    """
    try:
        from ...core.database import get_connection
        from ...core.database.bm25 import fts5_search_file_summaries

        with get_connection() as conn:
            return fts5_search_file_summaries(
                conn,
                query,
                meeting_ids=meeting_ids,
                limit=top_k * 2,
                user_id=user_id,
            )
    except Exception:
        logger.debug("File summary BM25 search failed", exc_info=True)
        return None


def _rrf_fuse_file_lists(
    vector_results: list[tuple[int, float]],
    bm25_results: list[dict],
    alpha: float,
    top_k: int,
) -> list[tuple[int, float]]:
    """Fuse vector and BM25 file-level results via RRF.

    *alpha* is the weight for vector results (1-alpha for BM25).
    Returns top_k (file_id, rrf_score) pairs.
    """
    # Convert vector results to list[dict] format for _rrf_merge_multi.
    # The dedup key uses ``chunk_id`` first; we set a synthetic stable
    # ``chunk_id`` per file so vector and BM25 paths merge without falling
    # back to the content-hash branch (which logs a misleading "BM25 index
    # may be stale" warning meant for actual chunks).
    vector_docs = [
        {
            "content": f"file_{fid}",
            "metadata": {"file_id": fid, "chunk_id": f"summary:{fid}"},
            "score": score,
        }
        for fid, score in vector_results
    ]
    bm25_docs = [
        {
            "content": f"file_{r['file_id']}",
            "metadata": {
                "file_id": r["file_id"],
                "chunk_id": f"summary:{r['file_id']}",
            },
            "score": r["score"],
        }
        for r in bm25_results
    ]

    if not vector_docs and not bm25_docs:
        return []

    if not bm25_docs:
        merged = _rrf_merge_multi([(vector_docs, alpha)], top_k=top_k)
    elif not vector_docs:
        merged = _rrf_merge_multi([(bm25_docs, 1.0 - alpha)], top_k=top_k)
    else:
        merged = _rrf_merge_multi(
            [(vector_docs, alpha), (bm25_docs, 1.0 - alpha)],
            top_k=top_k,
        )

    # Extract file_id and score from merged results
    results: list[tuple[int, float]] = []
    seen: set[int] = set()
    for doc in merged:
        fid = doc.get("metadata", {}).get("file_id")
        if isinstance(fid, int) and fid not in seen:
            results.append((fid, doc.get("score", 0.0)))
            seen.add(fid)

    return results[:top_k]


def route_files_by_summary(
    query: str,
    meeting_ids: list[int] | None = None,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
    user_id: str | None = None,
) -> list[int] | None:
    """Return file_ids ranked by summary similarity to *query*.

    When ``RAG_SUMMARY_ROUTER_HYBRID_ENABLED`` is true, combines vector
    similarity with BM25 keyword search via RRF fusion.

    Returns ``None`` (not an empty list) when:
      * the router is disabled in settings,
      * the summary collection is empty,
      * any error occurs.

    A ``None`` result tells the caller to fall back to the legacy
    enumeration path; an empty list means the router was healthy but
    nothing matched (still falls back when
    ``RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK`` is true).
    """
    if not settings.RAG_SUMMARY_ROUTER_ENABLED:
        return None

    query = (query or "").strip()
    if not query:
        return None

    requested_top_k = top_k if top_k is not None else settings.RAG_SUMMARY_ROUTER_TOP_FILES
    if requested_top_k <= 0:
        return None

    threshold = min_score if min_score is not None else settings.RAG_SUMMARY_ROUTER_MIN_SCORE
    lower_is_better = _vector_score_lower_is_better()

    try:
        store = get_summary_vectorstore()
    except Exception:
        logger.warning("Summary vectorstore init failed; router disabled", exc_info=True)
        return None

    try:
        if store._collection.count() == 0:
            return None
    except Exception:
        logger.debug("Summary collection count check failed", exc_info=True)

    where = _build_filter(meeting_ids, user_id)
    fetch_k = max(requested_top_k, requested_top_k * 2)

    # --- Vector search ---
    vector_ranked: list[tuple[int, float]] = []
    try:
        kwargs: dict[str, Any] = {"k": fetch_k}
        if where:
            kwargs["filter"] = where
        results = store.similarity_search_with_score(query, **kwargs)
    except Exception:
        logger.warning("Summary similarity search failed", exc_info=True)
        results = []

    if results:
        seen: set[int] = set()
        for doc, score in results:
            meta = doc.metadata or {}
            file_id = meta.get("file_id")
            if not isinstance(file_id, int) or file_id in seen:
                continue
            score_f = normalize_score(float(score), lower_is_better)
            if threshold > 0 and score_f < threshold:
                continue
            vector_ranked.append((file_id, score_f))
            seen.add(file_id)

    # --- BM25 search (hybrid mode) ---
    hybrid_enabled = getattr(settings, "RAG_SUMMARY_ROUTER_HYBRID_ENABLED", False)
    if hybrid_enabled:
        bm25_results = _bm25_file_search(query, meeting_ids, requested_top_k, user_id)
        alpha = getattr(settings, "RAG_SUMMARY_ROUTER_HYBRID_ALPHA", 0.6)

        if bm25_results is not None:
            fused = _rrf_fuse_file_lists(vector_ranked, bm25_results, alpha, requested_top_k)
            if fused:
                logger.info(
                    "Summary router (hybrid): query='%s' meetings=%s -> %d files "
                    "(vector=%d, bm25=%d)",
                    query[:60],
                    meeting_ids or "ALL",
                    len(fused),
                    len(vector_ranked),
                    len(bm25_results),
                )
                return [fid for fid, _ in fused]

    # --- Vector-only fallback ---
    vector_ranked.sort(key=lambda x: x[1], reverse=True)
    selected = [fid for fid, _ in vector_ranked[:requested_top_k]]

    logger.info(
        "Summary router: query='%s' meetings=%s -> %d/%d files (top_k=%d)",
        query[:60],
        meeting_ids or "ALL",
        len(selected),
        len(results),
        requested_top_k,
    )
    return selected


def route_files_with_scores(
    query: str,
    meeting_ids: list[int] | None = None,
    *,
    top_k: int | None = None,
    user_id: str | None = None,
) -> list[tuple[int, float]] | None:
    """Variant of :func:`route_files_by_summary` returning scores too.

    Useful for trace spans / future hybrid weighting.  Same fail-open
    contract.
    """
    if not settings.RAG_SUMMARY_ROUTER_ENABLED:
        return None

    query = (query or "").strip()
    if not query:
        return None

    requested_top_k = top_k if top_k is not None else settings.RAG_SUMMARY_ROUTER_TOP_FILES
    threshold = settings.RAG_SUMMARY_ROUTER_MIN_SCORE
    lower_is_better = _vector_score_lower_is_better()

    try:
        store = get_summary_vectorstore()
    except Exception:
        return None

    where = _build_filter(meeting_ids, user_id)
    fetch_k = max(requested_top_k, requested_top_k * 2)

    # --- Vector search ---
    vector_ranked: list[tuple[int, float]] = []
    try:
        kwargs: dict[str, Any] = {"k": fetch_k}
        if where:
            kwargs["filter"] = where
        results = store.similarity_search_with_score(query, **kwargs)
    except Exception:
        results = []

    seen: set[int] = set()
    for doc, score in results:
        meta = doc.metadata or {}
        file_id = meta.get("file_id")
        if not isinstance(file_id, int) or file_id in seen:
            continue
        score_f = normalize_score(float(score), lower_is_better)
        if threshold > 0 and score_f < threshold:
            continue
        vector_ranked.append((file_id, score_f))
        seen.add(file_id)

    # --- BM25 search (hybrid mode) ---
    hybrid_enabled = getattr(settings, "RAG_SUMMARY_ROUTER_HYBRID_ENABLED", False)
    if hybrid_enabled:
        bm25_results = _bm25_file_search(query, meeting_ids, requested_top_k, user_id)
        alpha = getattr(settings, "RAG_SUMMARY_ROUTER_HYBRID_ALPHA", 0.6)

        if bm25_results is not None:
            fused = _rrf_fuse_file_lists(vector_ranked, bm25_results, alpha, requested_top_k)
            if fused:
                return fused

    # --- Vector-only fallback ---
    vector_ranked.sort(key=lambda x: x[1], reverse=True)
    return vector_ranked[:requested_top_k]
