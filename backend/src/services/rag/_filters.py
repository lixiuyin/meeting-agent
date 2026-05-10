"""Filter building and provider resolution for retrieval."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from ...core.config import settings

logger = logging.getLogger(__name__)
_RETRIEVAL_PROVIDERS = {"native", "hybrid", "multimodal", "hybrid_multimodal"}


def _resolve_provider(rag_mode: str | None) -> str:
    configured = (settings.RAG_RETRIEVER_PROVIDER or "native").strip().lower()
    if configured not in _RETRIEVAL_PROVIDERS:
        logger.warning(
            "Unknown RAG_RETRIEVER_PROVIDER=%s, falling back to native",
            configured,
        )
        configured = "native"
    if rag_mode is None:
        return configured
    mode = rag_mode.strip().lower()
    if mode == "hybrid":
        return "hybrid"
    if mode == "multimodal":
        return "multimodal"
    if mode == "auto":
        return "hybrid_multimodal" if settings.RAGANYTHING_ENABLED else "native"
    if mode in _RETRIEVAL_PROVIDERS:
        return mode
    return configured


def _build_filters(
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    speakers: list[str] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Build Chroma-compatible where filter from retrieval parameters.

    When ``RAG_SPEAKER_FILTER_PUSHDOWN`` is enabled and *speakers* are
    provided, adds a ``$or`` clause matching any speaker against the
    ``speakers_in_chunk`` metadata field.  Post-retrieval filtering in
    ``_apply_speaker_filter`` still runs as a safety net for old chunks
    that lack the metadata field.

    When *user_id* is provided (and not ``"default"``), it is included as a
    filter clause so that unscoped queries only return vectors belonging to
    that user.
    """
    clauses: list[dict[str, Any]] = []
    has_scope_ids = bool(meeting_ids or file_ids)
    if settings.PARENT_CHILD_ENABLED and not has_scope_ids:
        clauses.append({"chunk_type": "child"})
    if meeting_ids:
        clauses.append({"meeting_id": {"$in": meeting_ids}})
    if file_ids:
        clauses.append({"file_id": {"$in": file_ids}})
    if file_types:
        clauses.append({"file_type": {"$in": file_types}})
    if date_from:
        clauses.append({"meeting_date": {"$gte": int(date_from.strftime("%Y%m%d"))}})
    if date_to:
        clauses.append({"meeting_date": {"$lte": int(date_to.strftime("%Y%m%d"))}})
    if speakers and settings.RAG_SPEAKER_FILTER_PUSHDOWN:
        speaker_clause: dict[str, Any] = {
            "$or": [{"speakers_in_chunk": {"$contains": s}} for s in speakers]
        }
        clauses.append(speaker_clause)
    if user_id and user_id != "default":
        clauses.append({"user_id": user_id})
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) > 1:
        return {"$and": clauses}
    return {}


def _extract_in_filter(filters: dict[str, Any], field: str) -> list[int] | None:
    """Extract `$in` values for a field from a Chroma filter dict."""
    if not filters:
        return None
    if field in filters and isinstance(filters[field], dict):
        values = filters[field].get("$in")
        if isinstance(values, list):
            return values
    and_clauses = filters.get("$and")
    if isinstance(and_clauses, list):
        for clause in and_clauses:
            if isinstance(clause, dict) and field in clause and isinstance(clause[field], dict):
                values = clause[field].get("$in")
                if isinstance(values, list):
                    return values
    return None


def _warn_scoped_zero_results(
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    k: int,
) -> None:
    logger.warning(
        "scoped retrieval returned 0 chunks meeting_ids=%s file_ids=%s k=%d",
        meeting_ids or [],
        file_ids or [],
        k,
    )


def _warn_unscoped_zero_results(
    *,
    k: int,
    vectorstore_doc_count: int | None = None,
    embedding_ok: bool = True,
    bm25_ok: bool = True,
) -> None:
    """Log a warning when unscoped retrieval returns zero results.

    Includes diagnostic hints to help operators diagnose why RAG failed
    for a cold-state query (no memories, no meeting/file selection).
    """
    logger.warning(
        "unscoped retrieval returned 0 chunks k=%d total_docs=%s embedding_ok=%s bm25_ok=%s",
        k,
        vectorstore_doc_count,
        embedding_ok,
        bm25_ok,
    )
