"""BM25 full-text retrieval using SQLite FTS5."""

import json
import logging

from ...core.trace import TraceContext

logger = logging.getLogger(__name__)


def _bm25_retrieve(
    query: str,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    k: int,
    *,
    trace: TraceContext | None = None,
    speaker_names: list[str] | None = None,
) -> list[dict]:
    """FTS5 full-text retrieval using SQLite built-in BM25 ranking."""
    from ._bm25_maintenance import is_bm25_rebuilding

    if is_bm25_rebuilding():
        logger.info("BM25 rebuild in progress — skipping FTS5 retrieval")
        return []

    from ...core.database import fts5_search, get_connection

    if trace:
        trace.start_span("bm25_search", "retrieve", parent_label="retrieve")
    try:
        with get_connection() as conn:
            results = fts5_search(
                conn,
                query,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
                limit=k,
                speaker_names=speaker_names,
            )
    except Exception as e:
        if trace:
            trace.finish_span("bm25_search", "error")
        logger.warning("FTS5 search failed: %s", e)
        return []

    out: list[dict] = []
    parent_id_to_best_score: dict[str, float] = {}
    _BM25_SCORE_FLOOR = 0.1  # RECALL-3: filter near-zero BM25 hits

    for r in results:
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except json.JSONDecodeError:
            meta = {"meeting_id": r["meeting_id"]}
        # FTS5 rank is negative BM25; negate for consistent "higher is better"
        score = float(-r["rank"]) if r["rank"] else 0.0
        if score < _BM25_SCORE_FLOOR:
            continue

        parent_id = meta.get("parent_id")
        # Parent-Child mode: collect best score per parent, resolve later
        if parent_id and meta.get("chunk_type") == "child":
            if (
                parent_id not in parent_id_to_best_score
                or score > parent_id_to_best_score[parent_id]
            ):
                parent_id_to_best_score[parent_id] = score
            continue

        # Flat mode or direct parent hit: keep as-is, inject chunk_id if missing
        if "chunk_id" not in meta:
            meta = {**meta, "chunk_id": r["chunk_id"]}
        out.append(
            {
                "content": r["content"],
                "metadata": meta,
                "score": score,
            }
        )

    # Resolve parent chunks if any child hits were found
    if parent_id_to_best_score:
        from ._vector import resolve_parent_chunks_by_ids

        parents = resolve_parent_chunks_by_ids(
            list(parent_id_to_best_score.keys()),
            parent_id_to_best_score,
        )
        out.extend(parents)

    if trace:
        trace.finish_span("bm25_search")
    return out
