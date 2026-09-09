"""Reciprocal Rank Fusion helpers for hybrid retrieval."""

import hashlib
import logging
import threading

from ...core.config import settings
from ...core.metrics import RRF_FALLBACK_KEY_TOTAL

logger = logging.getLogger(__name__)
_missing_chunk_warning_emitted = False
_missing_chunk_warning_lock = threading.Lock()


def _normalize_content(text: str) -> str:
    """Normalize text for stable hashing: collapse whitespace, strip, lower."""
    return " ".join(text.lower().split())


def _rrf_dedup_key(doc: dict) -> str:
    """Generate a dedup key for RRF merge.

    Uses chunk_id exclusively when available. Falls back to
    sha256(normalized content) so identical text from vector and BM25
    paths (which may have different metadata schemas) still deduplicates
    correctly. The old meeting_id+chunk_index fallback was removed because
    metadata schema mismatches could cause silent mis-merges.
    """
    meta = doc.get("metadata") or {}
    # Physical IDs include a replacement generation. The logical ID remains
    # stable so old/new generations deduplicate during the atomic handover.
    chunk_id = meta.get("logical_chunk_id") or meta.get("chunk_id")
    if chunk_id:
        return str(chunk_id)
    content = doc.get("content", "")
    # The "BM25 index may be stale" warning is only meaningful for actual
    # chunk-level docs. Synthetic / lookup-style docs (e.g. file-level
    # routing placeholders that carry only ``file_id``) legitimately have
    # no chunk_id; skip the warning for them and use content hash quietly.
    is_chunk_doc = (
        meta.get("chunk_index") is not None
        or "page_number" in meta
        or "timestamp_start" in meta
        or "parent_id" in meta
    )
    if is_chunk_doc:
        # H-5: Missing chunk_id on a real chunk degrades dedup accuracy. Log
        # so operators can detect BM25 write gaps without failing the query.
        global _missing_chunk_warning_emitted
        with _missing_chunk_warning_lock:
            if not _missing_chunk_warning_emitted:
                logger.warning(
                    "BM25 index may be stale: RRF dedup is using content hashes because "
                    "chunk metadata is missing chunk_id; further occurrences are counted "
                    "but not logged"
                )
                _missing_chunk_warning_emitted = True
        RRF_FALLBACK_KEY_TOTAL.inc()
    return hashlib.sha256(_normalize_content(content).encode()).hexdigest()[:32]


def _adaptive_k(fetch_k: int, base_k: int) -> int:
    """Adjust RRF k based on fetch_k — narrower queries benefit from smaller k."""
    if fetch_k <= 10:
        return max(base_k // 3, 10)
    if fetch_k <= 30:
        return max(base_k // 2, 20)
    return base_k


def _normalize_path(results: list[dict], k: int, weight: float) -> dict[str, float]:
    """Compute per-path normalized RRF scores.

    Normalize the unweighted reciprocal-rank contribution against its
    theoretical maximum and apply the path weight afterwards.  Dividing a
    weighted contribution by a weighted maximum would cancel the weight and
    make every non-zero hybrid alpha equivalent.
    """
    if not results or weight <= 0:
        return {}
    theoretical_max = 1.0 / (k + 1)
    scores: dict[str, float] = {}
    for rank, doc in enumerate(results):
        key = _rrf_dedup_key(doc)
        raw = 1.0 / (k + rank + 1)
        scores[key] = scores.get(key, 0.0) + weight * (raw / theoretical_max)
    return scores


def _rrf_merge(
    vector_results: list[dict],
    bm25_results: list[dict],
    top_k: int,
    k: int | None = None,
    fetch_k: int | None = None,
) -> list[dict]:
    """Reciprocal Rank Fusion merge of vector and BM25 results.

    Scores are normalized by the theoretical maximum ``1/(k+1)`` per path
    so the distribution preserves relative ranking quality instead of
    collapsing to a narrow range (RRF-2). Dynamic k adapts to fetch_k
    size (RRF-4).
    """
    base_k = k if k is not None else settings.RRF_K_PARAM
    rrf_k = _adaptive_k(fetch_k, base_k) if fetch_k is not None else base_k
    alpha = settings.HYBRID_ALPHA  # 1.0 = pure vector, 0.0 = pure BM25
    doc_map: dict[str, dict] = {}

    # Per-path normalization (RRF-3)
    vec_scores = _normalize_path(vector_results, rrf_k, alpha)
    bm25_scores = _normalize_path(bm25_results, rrf_k, 1.0 - alpha)

    for doc in vector_results:
        doc_map[_rrf_dedup_key(doc)] = doc

    for doc in bm25_results:
        key = _rrf_dedup_key(doc)
        if key not in doc_map:
            doc_map[key] = doc
        else:
            existing = doc_map[key]
            existing_score = existing.get("score", 0.0)
            new_score = doc.get("score", 0.0)
            if new_score > existing_score:
                doc_map[key] = {**existing, **doc, "score": new_score}

    # Combine per-path scores
    all_keys = set(vec_scores) | set(bm25_scores)
    combined: dict[str, float] = {}
    for key in all_keys:
        combined[key] = vec_scores.get(key, 0.0) + bm25_scores.get(key, 0.0)

    if not combined:
        return []

    merged = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_k]
    max_score = merged[0][1]
    if max_score == 0.0:
        return [{**doc_map[key], "score": 0.0, "score_kind": "relevance"} for key, _ in merged]
    return [
        {**doc_map[key], "score": score / max_score, "score_kind": "relevance"}
        for key, score in merged
    ]


def _rrf_merge_multi(
    result_lists: list[tuple[list[dict], float]],
    top_k: int,
    k: int | None = None,
) -> list[dict]:
    """Reciprocal Rank Fusion merge of multiple result lists with weights."""
    base_k = k if k is not None else settings.RRF_K_PARAM
    rrf_scores: dict[str, float] = {}
    doc_map: dict[str, dict] = {}
    for results, weight in result_lists:
        path_scores = _normalize_path(results, base_k, weight)
        for key, score in path_scores.items():
            rrf_scores[key] = rrf_scores.get(key, 0.0) + score
        for doc in results:
            key = _rrf_dedup_key(doc)
            if key not in doc_map:
                doc_map[key] = doc
    if not rrf_scores:
        return []
    merged = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    max_score = merged[0][1]
    if max_score == 0.0:
        return [{**doc_map[key], "score": 0.0, "score_kind": "relevance"} for key, _ in merged]
    return [
        {**doc_map[key], "score": score / max_score, "score_kind": "relevance"}
        for key, score in merged
    ]
