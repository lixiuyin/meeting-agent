"""Vector retrieval helpers: parent-child resolution and score direction."""

import functools
import logging
from typing import Any

from ...core.config import settings

logger = logging.getLogger(__name__)


def source_metadata(metadata: dict | None, document_id: object = None) -> dict:
    """Carry the actual vector record identity, including legacy index entries."""
    result = dict(metadata or {})
    if isinstance(document_id, str) and document_id:
        result["chunk_id"] = document_id
    return result


def resolve_parent_chunks_by_ids(
    parent_ids: list[str],
    child_scores: dict[str, float],
) -> list[dict]:
    """Given parent IDs and corresponding child scores, batch-fetch parent chunks from vectorstore.

    For a parent with multiple child hits, keep the one with the best score
    (lowest distance / highest similarity).
    """
    if not parent_ids:
        return []
    from ._vectorstore import get_vectorstore

    vectorstore = get_vectorstore()
    try:
        parent_data = vectorstore.get(
            ids=parent_ids,
            include=["documents", "metadatas"],
        )
    except Exception:
        logger.warning("Failed to fetch parent chunks", exc_info=True)
        return []

    from ._contextual import restore_display_content

    out = []
    for idx, content in enumerate(parent_data["documents"]):
        pid = parent_data["ids"][idx]
        meta = source_metadata(parent_data["metadatas"][idx], pid)
        if pid in child_scores:
            out.append(
                {
                    "content": restore_display_content(content, meta),
                    "metadata": meta,
                    "score": float(child_scores[pid]),
                }
            )
    return out


def _resolve_parent_chunks(
    vectorstore: Any,
    child_results: list[tuple[Any, float]],
    threshold: float | None,
    lower_is_better: bool = True,
) -> list[dict]:
    """Map child chunk hits back to parent chunks.

    When parent_id lookup fails (e.g. after chunk_size change without rebuild),
    falls back to matching by parent_start_offset stored in child metadata (C-RAG-1).
    """
    seen_parents: dict[str, float] = {}
    offset_fallback: dict[tuple[int, int], float] = {}  # (start, end) → best score
    for doc, score in child_results:
        if threshold is not None:
            if lower_is_better:
                if score > threshold:
                    continue
            elif score < threshold:
                continue
        parent_id = doc.metadata.get("parent_id")
        p_start = doc.metadata.get("parent_start_offset")
        p_end = doc.metadata.get("parent_end_offset")
        if parent_id:
            if parent_id not in seen_parents or (
                (lower_is_better and score < seen_parents[parent_id])
                or (not lower_is_better and score > seen_parents[parent_id])
            ):
                seen_parents[parent_id] = score
        elif isinstance(p_start, int) and isinstance(p_end, int) and p_start >= 0:
            key = (p_start, p_end)
            if key not in offset_fallback or (
                (lower_is_better and score < offset_fallback[key])
                or (not lower_is_better and score > offset_fallback[key])
            ):
                offset_fallback[key] = score

    results = resolve_parent_chunks_by_ids(list(seen_parents.keys()), seen_parents)

    # If ID-based resolution found all parents, return immediately
    if len(results) == len(seen_parents) and not offset_fallback:
        return results

    # Fallback: resolve orphaned children by offset-based content lookup
    if offset_fallback and len(results) < len(seen_parents) + len(offset_fallback):
        _resolve_parents_by_offset(vectorstore, offset_fallback, results, lower_is_better)

    return results


def _resolve_parents_by_offset(
    vectorstore: Any,
    offset_map: dict[tuple[int, int], float],
    results: list[dict],
    lower_is_better: bool,
) -> None:
    """Attempt to find parent chunks by matching parent_start_offset in metadata."""
    try:
        # Query all parent-type chunks and filter by offset
        all_parents = vectorstore.get(
            include=["documents", "metadatas"],
            where={"chunk_type": "parent"},
        )
    except Exception:
        logger.debug("Offset-based parent fallback query failed", exc_info=True)
        return

    offset_set = set(offset_map.keys())
    from ._contextual import restore_display_content

    for idx, meta in enumerate(all_parents.get("metadatas", [])):
        ids = all_parents.get("ids", [])
        meta = source_metadata(meta, ids[idx] if idx < len(ids) else None)
        p_start = meta.get("parent_start_offset")
        p_end = meta.get("parent_end_offset")
        if isinstance(p_start, int) and isinstance(p_end, int) and (p_start, p_end) in offset_set:
            score = offset_map[(p_start, p_end)]
            results.append(
                {
                    "content": restore_display_content(all_parents["documents"][idx], meta),
                    "metadata": meta,
                    "score": float(score),
                }
            )
            offset_set.discard((p_start, p_end))
            if not offset_set:
                break


@functools.lru_cache(maxsize=4)
def _score_lower_is_better_cached(metric: str) -> bool:
    return metric in {"l2", "cosine"}


def _vector_score_lower_is_better() -> bool:
    """Return score direction for configured vector distance metric.

    Result is cached per metric string to avoid repeated lower()+set
    lookups on every retrieval call.
    """
    return _score_lower_is_better_cached(str(settings.DISTANCE_METRIC).lower())


def normalize_score(raw: float, lower_is_better: bool) -> float:
    """Normalize a raw vector distance score to a uniform "higher is better" scale.

    For cosine/L2 (lower is better), convert to similarity via ``1/(1+s)``.
    For IP (higher is better), pass through unchanged.
    """
    if lower_is_better:
        denominator = 1.0 + raw
        if denominator <= 0:
            return 0.0
        return 1.0 / denominator
    return raw


def normalize_document_scores(
    docs: list[dict],
    *,
    legacy_lower_is_better: bool = False,
) -> list[dict]:
    """Return documents under the public higher-is-better score contract.

    Retrieval adapters mark raw vector distances with ``score_kind=distance``.
    BM25, RRF, multimodal, funnel, and reranker outputs use
    ``score_kind=relevance``.  The legacy fallback is only for old callers that
    have not yet attached provenance; public retrieval results should always be
    returned with explicit relevance provenance.
    """
    normalized: list[dict] = []
    for doc in docs:
        raw = float(doc.get("score", 0.0))
        score_kind = doc.get("score_kind")
        lower_is_better = score_kind == "distance" or (
            score_kind not in {"distance", "relevance"} and legacy_lower_is_better
        )
        normalized.append(
            {
                **doc,
                "score": normalize_score(raw, lower_is_better),
                "score_kind": "relevance",
            }
        )
    return normalized
