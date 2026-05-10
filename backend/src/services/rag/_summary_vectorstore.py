"""Singleton Chroma collection for file-level summary embeddings.

This collection holds one document per ``meeting_files`` row whose
``summary`` column is non-empty.  The retrieval pipeline uses it to
narrow the scope to the most relevant files before doing chunk-level
retrieval, so broad-recall queries don't have to fan out across every
file in the corpus.

Doc id is deterministic: ``file_{file_id}``.  Re-indexing the same
file overwrites its single vector — there's no chunking at this
layer.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from langchain_chroma import Chroma

from ...core.config import settings
from ...core.database._connection import _write_lock as _db_write_lock
from ..embedder import get_embeddings
from ._vectorstore import _ensure_collection_dimension

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "meeting_files_summaries"

_summary_store: Chroma | None = None
_lock = threading.Lock()


def reset_summary_vectorstore() -> None:
    """Reset the summary collection singleton.

    Called when embedding settings change so the collection is rebuilt
    against the new dimension.
    """
    global _summary_store
    with _lock:
        _summary_store = None
    logger.info("Summary vectorstore singleton reset")


def get_summary_vectorstore() -> Chroma:
    """Get or create the singleton summary collection (thread-safe).

    Mirrors :func:`get_vectorstore` so that dimension drift between
    config and stored vectors causes the collection to be dropped and
    recreated rather than failing at query time.
    """
    global _summary_store
    if _summary_store is None:
        with _lock:
            if _summary_store is None:
                embeddings = get_embeddings()
                expected_dim = _resolve_dimension(embeddings, settings.EMBEDDING_DIMENSION)
                _ensure_collection_dimension(
                    str(settings.VECTOR_DB_DIR),
                    _COLLECTION_NAME,
                    embeddings,
                    expected_dim,
                )
                _summary_store = Chroma(
                    collection_name=_COLLECTION_NAME,
                    embedding_function=embeddings,
                    persist_directory=str(settings.VECTOR_DB_DIR),
                )
    return _summary_store


def summary_vectorstore_write_lock() -> threading.RLock:
    """Global write lock for summary collection mutations.

    Shares the same lock as SQLite writes to prevent orphaned vectors/rows.
    """
    return _db_write_lock


def _resolve_dimension(embeddings: Any, fallback: int) -> int:
    for attr in ("embedding_dimension", "dimension", "dimensions"):
        candidate = getattr(embeddings, attr, None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return fallback


def _summary_doc_id(file_id: int) -> str:
    return f"file_{file_id}"


def upsert_file_summary(
    file_id: int,
    summary: str,
    *,
    meeting_id: int,
    file_name: str | None = None,
    file_type: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Upsert one file's summary into the collection.

    Empty / whitespace-only summaries are deleted instead of stored, so
    the collection never holds noise that would dilute routing.
    """
    summary = (summary or "").strip()
    if not summary:
        delete_file_summary(file_id)
        return

    metadata: dict[str, Any] = {"meeting_id": int(meeting_id), "file_id": int(file_id)}
    if file_name:
        metadata["file_name"] = file_name
    if file_type:
        metadata["file_type"] = file_type
    if extra_metadata:
        for key, value in extra_metadata.items():
            if value is None:
                continue
            metadata[key] = value

    store = get_summary_vectorstore()
    doc_id = _summary_doc_id(file_id)
    try:
        embedding = get_embeddings().embed_documents([summary])[0]
    except Exception:
        logger.warning(
            "Embedding failed for file %d summary; skipping summary vector upsert",
            file_id,
            exc_info=True,
        )
        return

    with _db_write_lock:
        store._collection.upsert(
            ids=[doc_id],
            documents=[summary],
            embeddings=[embedding],
            metadatas=[metadata],
        )


def delete_file_summary(file_id: int) -> None:
    """Remove one file's summary vector. No-op if it doesn't exist."""
    store = get_summary_vectorstore()
    doc_id = _summary_doc_id(file_id)
    try:
        with _db_write_lock:
            store._collection.delete(ids=[doc_id])
    except Exception:
        logger.warning("Summary vector delete failed for file %d", file_id, exc_info=True)


def delete_meeting_summaries(meeting_id: int) -> None:
    """Remove all summary vectors belonging to a meeting."""
    store = get_summary_vectorstore()
    try:
        with _db_write_lock:
            store._collection.delete(where={"meeting_id": int(meeting_id)})
    except Exception:
        logger.warning(
            "Summary vector delete-by-meeting failed for meeting %d",
            meeting_id,
            exc_info=True,
        )


def sync_missing_file_summary_vectors() -> int:
    """Backfill any ``meeting_files.summary`` rows missing from the vector store.

    Mirrors ``MemoryService.sync_missing_vectors``: enumerates the live
    collection's doc IDs, diffs against DB rows with a non-empty summary,
    and upserts only the gap.  A single failed embed is logged and skipped
    so one bad row can't block the rest of the backfill.

    Returns the count of newly-indexed file summaries.  Safe to call from
    startup best-effort paths — never raises.
    """
    from ...core.database import get_connection

    try:
        store = get_summary_vectorstore()
        existing_payload = store._collection.get(include=[])
        existing_ids = set(existing_payload.get("ids") or [])

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, meeting_id, file_name, file_type, summary "
                "FROM meeting_files "
                "WHERE summary IS NOT NULL AND TRIM(summary) != ''"
            ).fetchall()
    except Exception:
        logger.warning("File-summary vector sync enumeration failed", exc_info=True)
        return 0

    indexed = 0
    for row in rows:
        if _summary_doc_id(row["id"]) in existing_ids:
            continue
        try:
            upsert_file_summary(
                row["id"],
                row["summary"],
                meeting_id=row["meeting_id"],
                file_name=row["file_name"],
                file_type=row["file_type"],
            )
            indexed += 1
        except Exception:
            logger.warning("File-summary vector sync failed for file %d", row["id"], exc_info=True)
    return indexed
