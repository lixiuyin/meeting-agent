"""Storage helpers for vector/BM25 indexing and cleanup."""

import hashlib
import json
import logging
import time
from typing import Any

from langchain_core.documents import Document

from ...core.trace import TraceContext
from ..embedder import get_embeddings
from ._vectorstore import get_vectorstore, vectorstore_write_lock

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """Stable content hash for chunk_id (IDX-3).

    Using content hash instead of position index prevents chunk_id drift
    when text is edited (e.g. speaker rename) — unchanged chunks keep
    the same ID and upsert naturally deduplicates.
    """
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def _chunk_id_prefix(meeting_id: int, file_id: int | None) -> str:
    """Build chunk ID prefix that includes file_id to prevent collision."""
    if file_id is None:
        return f"meeting_{meeting_id}"
    return f"meeting_{meeting_id}_file_{file_id}"


def _dedup_existing_chunks(
    docs: list[Document], ids: list[str], vectorstore: Any
) -> tuple[list[Document], list[str]]:
    """Skip chunks whose content hasn't changed since last index."""
    if not docs:
        return docs, ids

    try:
        existing = vectorstore.get(ids=ids, include=["documents"])
    except Exception:
        return docs, ids

    existing_map: dict[str, str] = {}
    for eid, doc_text in zip(existing["ids"], existing["documents"], strict=False):
        existing_map[eid] = _content_hash(doc_text)

    new_docs: list[Document] = []
    new_ids: list[str] = []
    for doc, chunk_id in zip(docs, ids, strict=False):
        new_hash = _content_hash(doc.page_content)
        if existing_map.get(chunk_id) == new_hash:
            continue
        new_docs.append(doc)
        new_ids.append(chunk_id)

    return new_docs, new_ids


def _upsert_with_trace(
    vectorstore: Any,
    docs: list[Document],
    ids: list[str],
    trace: TraceContext | None,
    meeting_id: int,
    embeddings: list[list[float]] | None = None,
) -> None:
    """Embed documents and upsert into vectorstore with optional trace spans.

    Args:
        vectorstore: Vector store instance
        docs: Documents to upsert
        ids: Document IDs
        trace: Trace context
        meeting_id: Meeting ID for logging
        embeddings: Optional pre-computed embeddings (if provided, skips embedding step)
    """
    if embeddings is None:
        # Compute embeddings
        if trace:
            trace.start_span("embed", "index")
        try:
            embedding_fn = get_embeddings()
            embeddings = embedding_fn.embed_documents([d.page_content for d in docs])
        except Exception:
            if trace:
                trace.finish_span("embed", "error")
            raise
        else:
            if trace:
                trace.finish_span("embed")

    if trace:
        trace.start_span("vectorstore_upsert", "index")
    try:
        with vectorstore_write_lock():
            vectorstore._collection.upsert(
                ids=ids,
                documents=[d.page_content for d in docs],
                embeddings=embeddings,
                metadatas=[d.metadata for d in docs],
            )
        if len(docs) < len(ids):
            logger.info(
                "Meeting %d: indexed %d chunks (%d unchanged skipped)",
                meeting_id,
                len(docs),
                len(ids) - len(docs),
            )
        else:
            logger.info("Meeting %d: indexed %d chunks", meeting_id, len(docs))
    finally:
        if trace:
            trace.finish_span("vectorstore_upsert")


def _add_to_bm25(meeting_id: int, text: str, metadata: dict, separators: list[str]) -> None:
    """Add document chunks to FTS5-backed full-text index via bm25_index table.

    When parent-child chunking is enabled, BM25 uses the same two-level
    split (parent then child) so that chunk granularity matches the
    vector index and RRF dedup keys align.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from ...core.config import settings
    from ...core.database import get_write_connection

    prefix = _chunk_id_prefix(meeting_id, metadata.get("file_id"))

    # Build chunk rows outside the write lock to minimise lock hold time.
    rows: list[tuple[str, int, str, str, str | None]] = []
    if settings.PARENT_CHILD_ENABLED:
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=separators,
        )
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHILD_CHUNK_SIZE,
            chunk_overlap=settings.CHILD_CHUNK_OVERLAP,
            separators=separators,
        )
        parent_chunks = parent_splitter.split_text(text)
        for i, parent_text in enumerate(parent_chunks):
            parent_id = f"{prefix}_parent_{i}"
            for j, child_text in enumerate(child_splitter.split_text(parent_text)):
                chunk_id = f"{prefix}_child_{i}_{j}"
                rows.append(
                    (
                        chunk_id,
                        meeting_id,
                        child_text,
                        "[]",
                        json.dumps(
                            {
                                "meeting_id": meeting_id,
                                "chunk_index": i * 1000 + j,
                                "chunk_id": chunk_id,
                                "chunk_type": "child",
                                "parent_id": parent_id,
                                **metadata,
                            }
                        ),
                    )
                )
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=separators,
        )
        for chunk_index, chunk_text in enumerate(splitter.split_text(text)):
            chunk_id = f"{prefix}_chunk_{chunk_index}"
            rows.append(
                (
                    chunk_id,
                    meeting_id,
                    chunk_text,
                    "[]",
                    json.dumps(
                        {
                            "meeting_id": meeting_id,
                            "chunk_index": chunk_index,
                            "chunk_id": chunk_id,
                            **metadata,
                        }
                    ),
                )
            )

    indexed = 0
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with get_write_connection() as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO bm25_index
                       (chunk_id, meeting_id, content, tokenized, metadata)
                       VALUES (?, ?, ?, ?, ?)""",
                    rows,
                )
                indexed = len(rows)
            logger.info("Meeting %d: added %d chunks to FTS5 index", meeting_id, indexed)
            return
        except Exception as e:
            last_exc = e
            if attempt == 0:
                backoff = min(0.05 * 2**attempt, 1.0)
                logger.debug(
                    "BM25 write attempt %d failed, retrying in %.2fs: %s",
                    attempt + 1,
                    backoff,
                    e,
                )
                time.sleep(backoff)
    logger.warning("Failed to persist BM25 chunks to database after 2 attempts: %s", last_exc)


def _add_docs_to_bm25(meeting_id: int, docs: list[Document], ids: list[str]) -> None:
    """Add pre-chunked Documents to the FTS5 BM25 index with retry.

    Used by index_meeting_pages / index_meeting_segments so the BM25
    fallback has data even when hybrid_search is not explicitly enabled.

    Retries once on transient DB errors to reduce dual-write inconsistency
    between Chroma (already succeeded) and BM25.
    """
    from ...core.database import add_bm25_chunk, get_write_connection

    if not docs:
        return
    indexed = 0
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with get_write_connection() as conn:
                for doc, chunk_id in zip(docs, ids, strict=False):
                    add_bm25_chunk(
                        conn,
                        chunk_id=chunk_id,
                        meeting_id=meeting_id,
                        content=doc.page_content,
                        metadata=json.dumps({**doc.metadata, "chunk_id": chunk_id}),
                    )
                    indexed += 1
            logger.info("Meeting %d: added %d chunks to FTS5 index", meeting_id, indexed)
            return
        except Exception as e:
            last_exc = e
            if attempt == 0:
                backoff = min(0.05 * 2**attempt, 1.0)
                logger.debug(
                    "BM25 write attempt %d failed, retrying in %.2fs: %s",
                    attempt + 1,
                    backoff,
                    e,
                )
                time.sleep(backoff)
    logger.warning("Failed to persist BM25 chunks after 2 attempts: %s", last_exc)


def _remove_from_raganything(meeting_id: int, file_id: int | None = None) -> None:
    """Remove meeting chunks from RAGAnything/LightRAG by doc_id.

    M-15: On deletion failure, records a pending deletion so the startup
    reconciler can retry it instead of silently leaving orphan data.
    """
    doc_id = f"meeting_{meeting_id}_file_{file_id if file_id is not None else 'unknown'}"
    try:
        from ._raganything import _get_raganything, _run_async

        ra = _get_raganything()

        async def _delete() -> None:
            try:
                await ra.lightrag.adelete_by_doc_id(doc_id)
                logger.info("Meeting %d: removed from RAGAnything (doc_id=%s)", meeting_id, doc_id)
            except Exception as exc:
                logger.debug("RAGAnything delete_by_doc_id failed for %s: %s", doc_id, exc)
                raise

        _run_async(_delete())
    except Exception as e:
        logger.warning("Failed to delete RAGAnything chunks: %s", e, exc_info=True)
        # M-15: Record for periodic cleanup retry
        try:
            from ...core.database import get_write_connection

            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions"
                    " (collection, embedding_id) VALUES ('raganything', ?)",
                    (doc_id,),
                )
        except Exception:
            logger.debug(
                "Failed to record raganything pending deletion for %s",
                doc_id,
                exc_info=True,
            )


def _remove_from_bm25(meeting_id: int, file_id: int | None = None) -> None:
    """Remove meeting chunks from FTS5 index (triggers auto-sync from bm25_index)."""
    from ...core.database import (
        delete_bm25_chunks_by_file,
        delete_bm25_chunks_by_meeting,
        get_write_connection,
    )

    try:
        with get_write_connection() as conn:
            if file_id is None:
                delete_bm25_chunks_by_meeting(conn, meeting_id)
            else:
                delete_bm25_chunks_by_file(conn, meeting_id, file_id)
    except Exception as e:
        logger.warning("Failed to delete FTS5 chunks from database: %s", e, exc_info=True)

    if file_id is None:
        logger.info("Meeting %d: removed from FTS5 index", meeting_id)
    else:
        logger.info("Meeting %d file %d: removed from FTS5 index", meeting_id, file_id)


def delete_meeting_chunks(meeting_id: int, file_id: int | None = None) -> None:
    """Delete vectors and BM25 index entries for a given meeting.

    When ``file_id`` is provided, acquires the file reindex lock to
    prevent concurrent reindex + delete from producing BM25 orphans.
    """
    from ...core.config import settings

    # Acquire file-level lock when deleting a specific file to serialize
    # against concurrent reindex operations (IDX-1).
    if file_id is not None:
        from ._indexer import _acquire_file_reindex_lock

        file_lock = _acquire_file_reindex_lock(meeting_id, file_id)
    else:
        file_lock = None

    if file_lock is not None:
        file_lock.acquire()
    try:
        _delete_meeting_chunks_inner(meeting_id, file_id, settings)
    finally:
        if file_lock is not None:
            file_lock.release()


def _delete_meeting_chunks_inner(meeting_id: int, file_id: int | None, settings: Any) -> None:
    """Inner implementation of delete_meeting_chunks (called under lock when applicable).

    Each deletion step records a pending deletion on failure so the startup
    reconciler can retry it, preventing partial-delete orphans.
    """
    from ...core.database import get_write_connection

    scope_label = f"meeting_{meeting_id}" + (f"_file_{file_id}" if file_id else "")
    vectorstore = get_vectorstore()
    if file_id is not None:
        where: dict = {"$and": [{"meeting_id": meeting_id}, {"file_id": file_id}]}
    else:
        where = {"meeting_id": meeting_id}

    # Step 1: Delete from vectorstore
    try:
        with vectorstore_write_lock():
            vectorstore.delete(where=where)
    except Exception:
        logger.warning("Failed to delete vectors for %s", scope_label, exc_info=True)
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES ('chroma', ?)",
                    (scope_label,),
                )
        except Exception:
            logger.debug("Failed to record pending vector deletion", exc_info=True)

    # Step 2: Delete from BM25
    try:
        _remove_from_bm25(meeting_id, file_id=file_id)
    except Exception:
        logger.warning("Failed to delete BM25 for %s", scope_label, exc_info=True)
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES ('bm25', ?)",
                    (scope_label,),
                )
        except Exception:
            logger.debug("Failed to record pending BM25 deletion", exc_info=True)

    # Step 3: Delete summary vectors
    try:
        _remove_summary_vectors(meeting_id, file_id=file_id)
    except Exception:
        logger.warning("Failed to delete summary vectors for %s", scope_label, exc_info=True)

    # Step 4: Invalidate per-file summary BM25
    if file_id is not None:
        try:
            from ...core.database import delete_file_summary_bm25

            with get_write_connection() as conn:
                delete_file_summary_bm25(conn, file_id=file_id)
        except Exception:
            logger.debug(
                "Failed to invalidate file summary BM25 for file %d", file_id, exc_info=True
            )

    logger.info("Meeting %d: deleted vectors and BM25 entries", meeting_id)

    # Step 5: Clear index state
    if file_id is not None:
        try:
            from ...core.database import clear_index_state

            with get_write_connection() as conn:
                clear_index_state(conn, file_id=file_id)
        except Exception:
            logger.warning("Failed to clear index_state for file %d", file_id, exc_info=True)

    # Step 6: Remove from RAGAnything (if enabled)
    if settings.RAGANYTHING_ENABLED:
        _remove_from_raganything(meeting_id, file_id)


def count_legacy_chunks_without_file_id() -> dict[str, int]:
    """Count chunks that lack a ``file_id`` in their metadata."""
    result = {"vector": 0, "bm25": 0}
    try:
        vectorstore = get_vectorstore()
        # Use include=[] (IDs only) to avoid loading all metadatas into memory,
        # then fetch metadatas only for the IDs we need to check.
        all_data = vectorstore.get(include=["metadatas"])
        result["vector"] = sum(1 for m in (all_data.get("metadatas") or []) if "file_id" not in m)
    except Exception:
        logger.debug("Could not count legacy vector chunks", exc_info=True)

    try:
        from ...core.database import get_connection

        with get_connection() as conn:
            # Use json_type to check for missing/NULL file_id without loading all rows.
            count = conn.execute(
                "SELECT COUNT(*) FROM bm25_index "
                "WHERE metadata IS NULL OR json_array_length(metadata) = 0 "
                "OR json_type(metadata, '$.file_id') IS NULL"
            ).fetchone()[0]
        result["bm25"] = count
    except Exception:
        logger.debug("Could not count legacy BM25 chunks", exc_info=True)

    return result


_BACKFILL_BATCH_SIZE = 5000


def _backfill_legacy_bm25_metadata() -> int:
    """Backfill ``chunk_id`` into BM25 metadata rows that don't have it.

    Incremental: tracks ``last_indexed_id`` in ``bm25_stats`` so that on
    restart only rows with ``id > last_indexed_id`` are examined.  Processes
    in batches of 5 000 to keep memory and latency bounded.
    """
    from ...core.database import get_connection, get_write_connection

    updated = 0
    try:
        # Resume from the last processed row.
        with get_connection() as conn:
            last_id_row = conn.execute(
                "SELECT value FROM bm25_stats WHERE key='last_indexed_id'"
            ).fetchone()
        last_id = int(last_id_row["value"]) if last_id_row else 0

        while True:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, meeting_id, chunk_id, metadata "
                    "FROM bm25_index WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, _BACKFILL_BATCH_SIZE),
                ).fetchall()

            if not rows:
                break

            updates: list[tuple[str, int]] = []
            for r in rows:
                try:
                    meta = json.loads(r["metadata"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if meta.get("chunk_id"):
                    continue  # already has chunk_id

                existing = r["chunk_id"]
                file_id = meta.get("file_id")
                chunk_index = meta.get("chunk_index", 0)

                if existing and "_chunk_" in str(existing):
                    meta["chunk_id"] = existing
                else:
                    prefix = _chunk_id_prefix(r["meeting_id"], file_id)
                    meta["chunk_id"] = f"{prefix}_chunk_{chunk_index}"

                updates.append((json.dumps(meta), r["id"]))

            if updates:
                with get_write_connection() as conn:
                    conn.executemany("UPDATE bm25_index SET metadata=? WHERE id=?", updates)
                updated += len(updates)

            # Advance the cursor regardless of whether rows needed updates
            # so we never re-scan the same range.
            last_id = rows[-1]["id"]
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bm25_stats (key, value) VALUES ('last_indexed_id', ?)",
                    (last_id,),
                )

        if updated:
            logger.info("Backfilled chunk_id in %d BM25 metadata rows", updated)
    except Exception:
        logger.debug("BM25 metadata backfill skipped or failed", exc_info=True)
    return updated


def _remove_summary_vectors(meeting_id: int, file_id: int | None = None) -> None:
    """Remove summary vectors for the given meeting / file scope."""
    try:
        from ._summary_vectorstore import delete_file_summary, delete_meeting_summaries

        if file_id is not None:
            delete_file_summary(file_id)
        else:
            delete_meeting_summaries(meeting_id)
            # Also clean the dedicated meeting-summary collection
            from ._meeting_summary_vectorstore import delete_meeting_summary

            delete_meeting_summary(meeting_id)
    except Exception:
        logger.warning(
            "Failed to remove summary vectors (meeting=%s, file=%s)",
            meeting_id,
            file_id,
            exc_info=True,
        )
