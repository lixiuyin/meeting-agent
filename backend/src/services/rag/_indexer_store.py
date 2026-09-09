"""Storage helpers for vector/BM25 indexing and cleanup."""

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document

from ...core.trace import TraceContext
from ..embedder import get_embeddings
from ._contextual import contextualize_content
from ._vectorstore import get_vectorstore, vectorstore_write_lock

logger = logging.getLogger(__name__)

_DELETION_SCOPE_RE = re.compile(r"^meeting_(?P<meeting_id>\d+)(?:_file_(?P<file_id>\d+))?$")
_CHUNK_FILE_ID_RE = re.compile(r"^meeting_(?P<meeting_id>\d+)_file_(?P<file_id>\d+)(?:_|$)")


def _content_hash(text: str) -> str:
    """Stable content hash used only to skip unchanged non-generation upserts."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def _chunk_id_prefix(
    meeting_id: int,
    file_id: int | None,
    source_kind: str | None = None,
    index_generation: str | None = None,
) -> str:
    """Build a chunk scope including file, source kind, and shadow generation."""
    prefix = f"meeting_{meeting_id}" if file_id is None else f"meeting_{meeting_id}_file_{file_id}"
    if source_kind:
        normalized = re.sub(r"[^a-z0-9]+", "_", source_kind.strip().lower()).strip("_")
        if normalized:
            prefix = f"{prefix}_source_{normalized}"
    if index_generation:
        normalized = re.sub(r"[^a-z0-9]+", "_", index_generation.strip().lower()).strip("_")
        if normalized:
            prefix = f"{prefix}_generation_{normalized}"
    return prefix


def _file_id_from_chunk_id(chunk_id: object, meeting_id: int) -> int | None:
    """Recover a file ID from a canonical chunk identifier when possible."""
    match = _CHUNK_FILE_ID_RE.match(str(chunk_id or ""))
    if not match or int(match.group("meeting_id")) != meeting_id:
        return None
    return int(match.group("file_id"))


def _dedup_existing_chunks(
    docs: list[Document], ids: list[str], vectorstore: Any
) -> tuple[list[Document], list[str]]:
    """Skip chunks whose content hasn't changed since last index."""
    if not docs:
        return docs, ids
    # Replacement generations must upsert every desired ID so unchanged
    # content receives the new generation marker before stale IDs are pruned.
    if any(doc.metadata.get("index_generation") for doc in docs):
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
        retrieval_content, _metadata = contextualize_content(doc.page_content, doc.metadata)
        new_hash = _content_hash(retrieval_content)
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
    indexed_docs: list[Document] = []
    contextualized = False
    for doc in docs:
        content, metadata = contextualize_content(doc.page_content, doc.metadata)
        contextualized = contextualized or content != doc.page_content
        indexed_docs.append(Document(page_content=content, metadata=metadata))

    # Precomputed embeddings describe the original transcript text.  Reusing
    # them for contextualized text would make stored documents and vectors
    # disagree, so recompute only when a prefix was actually added.
    if embeddings is None or contextualized:
        # Compute embeddings
        if trace:
            trace.start_span("embed", "index")
        try:
            embedding_fn = get_embeddings()
            embeddings = embedding_fn.embed_documents([d.page_content for d in indexed_docs])
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
                documents=[d.page_content for d in indexed_docs],
                embeddings=embeddings,
                metadatas=[d.metadata for d in indexed_docs],
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


def _add_to_bm25(
    meeting_id: int,
    text: str,
    metadata: dict,
    separators: list[str],
    *,
    strict: bool = False,
) -> None:
    """Add document chunks to FTS5-backed full-text index via bm25_index table.

    When parent-child chunking is enabled, BM25 uses the same two-level
    split (parent then child) so that chunk granularity matches the
    vector index and RRF dedup keys align.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from ...core.config import settings
    from ...core.database import get_write_connection
    from ..tokenizer import count_tokens

    def _budget(token_name: str, legacy_name: str) -> int:
        value = getattr(settings, token_name, None)
        return value if isinstance(value, int) else int(getattr(settings, legacy_name))

    prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
        metadata.get("index_generation"),
    )
    logical_prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
    )

    # Build chunk rows outside the write lock to minimise lock hold time.
    rows: list[tuple[str, int, str, str, str | None]] = []
    if settings.PARENT_CHILD_ENABLED:
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=_budget("CHUNK_SIZE_TOKENS", "CHUNK_SIZE"),
            chunk_overlap=_budget("CHUNK_OVERLAP_TOKENS", "CHUNK_OVERLAP"),
            length_function=count_tokens,
            separators=separators,
        )
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=_budget("CHILD_CHUNK_SIZE_TOKENS", "CHILD_CHUNK_SIZE"),
            chunk_overlap=_budget("CHILD_CHUNK_OVERLAP_TOKENS", "CHILD_CHUNK_OVERLAP"),
            length_function=count_tokens,
            separators=separators,
        )
        parent_chunks = parent_splitter.split_text(text)
        for i, parent_text in enumerate(parent_chunks):
            parent_id = f"{prefix}_parent_{i}"
            logical_parent_id = f"{logical_prefix}_parent_{i}"
            for j, child_text in enumerate(child_splitter.split_text(parent_text)):
                chunk_id = f"{prefix}_child_{i}_{j}"
                logical_chunk_id = f"{logical_prefix}_child_{i}_{j}"
                row_metadata = {
                    "meeting_id": meeting_id,
                    "chunk_index": i * 1000 + j,
                    "chunk_id": chunk_id,
                    "logical_chunk_id": logical_chunk_id,
                    "chunk_type": "child",
                    "parent_id": parent_id,
                    "logical_parent_id": logical_parent_id,
                    "context_hint": parent_text[:400],
                    **metadata,
                }
                indexed_text, row_metadata = contextualize_content(child_text, row_metadata)
                rows.append(
                    (
                        chunk_id,
                        meeting_id,
                        indexed_text,
                        "[]",
                        json.dumps(row_metadata),
                    )
                )
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=_budget("CHUNK_SIZE_TOKENS", "CHUNK_SIZE"),
            chunk_overlap=_budget("CHUNK_OVERLAP_TOKENS", "CHUNK_OVERLAP"),
            length_function=count_tokens,
            separators=separators,
        )
        for chunk_index, chunk_text in enumerate(splitter.split_text(text)):
            chunk_id = f"{prefix}_chunk_{chunk_index}"
            logical_chunk_id = f"{logical_prefix}_chunk_{chunk_index}"
            row_metadata = {
                "meeting_id": meeting_id,
                "chunk_index": chunk_index,
                "chunk_id": chunk_id,
                "logical_chunk_id": logical_chunk_id,
                **metadata,
            }
            indexed_text, row_metadata = contextualize_content(chunk_text, row_metadata)
            rows.append(
                (
                    chunk_id,
                    meeting_id,
                    indexed_text,
                    "[]",
                    json.dumps(row_metadata),
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
    message = "Failed to persist BM25 chunks after 2 attempts"
    if strict:
        raise RuntimeError(message) from last_exc
    logger.warning("%s: %s", message, last_exc)


def _add_docs_to_bm25(
    meeting_id: int,
    docs: list[Document],
    ids: list[str],
    *,
    strict: bool = False,
) -> None:
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
                    content, metadata = contextualize_content(doc.page_content, doc.metadata)
                    add_bm25_chunk(
                        conn,
                        chunk_id=chunk_id,
                        meeting_id=meeting_id,
                        content=content,
                        metadata=json.dumps({**metadata, "chunk_id": chunk_id}),
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
    message = "Failed to persist BM25 chunks after 2 attempts"
    if strict:
        raise RuntimeError(message) from last_exc
    logger.warning("%s: %s", message, last_exc)


@dataclass(frozen=True)
class _FileIndexSnapshot:
    vector_ids: list[str]
    bm25_ids: list[str]


@dataclass(frozen=True)
class NativeIndexManifest:
    """Verified physical state for one committed file generation."""

    generation: str
    config_fingerprint: str
    chroma_chunk_count: int
    bm25_chunk_count: int
    checksum: str


def _file_where(meeting_id: int, file_id: int) -> dict[str, Any]:
    return {"$and": [{"meeting_id": meeting_id}, {"file_id": file_id}]}


def _generation_where(meeting_id: int, file_id: int, generation: str) -> dict[str, Any]:
    return {
        "$and": [
            {"meeting_id": meeting_id},
            {"file_id": file_id},
            {"index_generation": generation},
        ]
    }


def inspect_native_index_generation(
    meeting_id: int,
    file_id: int,
    generation: str,
    config_fingerprint: str,
) -> NativeIndexManifest:
    """Read back both native stores and build a compact integrity manifest."""
    vector = get_vectorstore().get(
        where=_generation_where(meeting_id, file_id, generation),
        include=["metadatas"],
    )
    vector_ids = [str(value) for value in (vector.get("ids") or [])]
    vector_meta = list(vector.get("metadatas") or [])
    if any(meta.get("index_config_fingerprint") != config_fingerprint for meta in vector_meta):
        raise RuntimeError("Chroma generation contains a mismatched config fingerprint")

    from ...core.database import get_connection

    with get_connection() as conn:
        bm25_rows = conn.execute(
            "SELECT chunk_id, metadata FROM bm25_index WHERE meeting_id=? "
            "AND CAST(json_extract(metadata, '$.file_id') AS INTEGER)=? "
            "AND json_extract(metadata, '$.index_generation')=?",
            (meeting_id, file_id, generation),
        ).fetchall()
    bm25_ids = [str(row["chunk_id"]) for row in bm25_rows]
    for row in bm25_rows:
        metadata = json.loads(row["metadata"] or "{}")
        if metadata.get("index_config_fingerprint") != config_fingerprint:
            raise RuntimeError("BM25 generation contains a mismatched config fingerprint")
    checksum_payload = json.dumps(
        {"vector": sorted(vector_ids), "bm25": sorted(bm25_ids)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return NativeIndexManifest(
        generation=generation,
        config_fingerprint=config_fingerprint,
        chroma_chunk_count=len(vector_ids),
        bm25_chunk_count=len(bm25_ids),
        checksum=hashlib.sha256(checksum_payload).hexdigest(),
    )


def _snapshot_file_indexes(meeting_id: int, file_id: int) -> _FileIndexSnapshot:
    """Capture only old physical IDs; content and embeddings remain untouched."""
    vector = get_vectorstore().get(
        where=_file_where(meeting_id, file_id),
        include=[],
    )
    from ...core.database import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT chunk_id "
            "FROM bm25_index WHERE meeting_id=? AND ("
            "CAST(json_extract(metadata, '$.file_id') AS INTEGER)=? "
            "OR chunk_id LIKE ?)",
            (meeting_id, file_id, f"meeting_{meeting_id}_file_{file_id}_%"),
        ).fetchall()
    return _FileIndexSnapshot(
        vector_ids=list(vector.get("ids") or []),
        bm25_ids=[str(row["chunk_id"]) for row in rows],
    )


def _delete_native_file_indexes_strict(meeting_id: int, file_id: int) -> None:
    """Delete Chroma and BM25 state, propagating either failure to the caller."""
    with vectorstore_write_lock():
        get_vectorstore().delete(where=_file_where(meeting_id, file_id))
    _remove_from_bm25(meeting_id, file_id)


def _rollback_generation(
    meeting_id: int,
    file_id: int,
    generation: str,
) -> None:
    """Remove only the failed shadow generation; the live generation was untouched."""
    vectorstore = get_vectorstore()
    with vectorstore_write_lock():
        vectorstore.delete(where=_generation_where(meeting_id, file_id, generation))

    from ...core.database import get_write_connection

    with get_write_connection() as conn:
        conn.execute(
            "DELETE FROM bm25_index WHERE meeting_id=? "
            "AND CAST(json_extract(metadata, '$.file_id') AS INTEGER)=? "
            "AND json_extract(metadata, '$.index_generation')=?",
            (meeting_id, file_id, generation),
        )


def _delete_stale_file_indexes(
    meeting_id: int,
    file_id: int,
    generation: str,
    snapshot: _FileIndexSnapshot,
) -> None:
    """Prune only pre-generation IDs after the replacement is fully durable."""
    vectorstore = get_vectorstore()
    with vectorstore_write_lock():
        stale_vector_ids = sorted(set(snapshot.vector_ids))
        if stale_vector_ids:
            vectorstore.delete(ids=stale_vector_ids)

    from ...core.database import get_write_connection

    old_bm25_ids = set(snapshot.bm25_ids)
    with get_write_connection() as conn:
        stale_bm25_ids = sorted(old_bm25_ids)
        if stale_bm25_ids:
            conn.executemany(
                "DELETE FROM bm25_index WHERE chunk_id=?",
                [(chunk_id,) for chunk_id in stale_bm25_ids],
            )


@contextmanager
def atomic_file_index_replacement(meeting_id: int, file_id: int) -> Iterator[str]:
    """Replace one file without an empty-index window, with failure rollback."""
    from ._indexer import _acquire_file_reindex_lock
    from ._publication import index_read_lease

    with index_read_lease(), _acquire_file_reindex_lock(meeting_id, file_id):
        snapshot = _snapshot_file_indexes(meeting_id, file_id)
        generation = uuid.uuid4().hex
        try:
            # Keep the old generation readable while all new vector and BM25
            # writes complete. Only then prune IDs not present in the new set.
            yield generation
            _delete_stale_file_indexes(
                meeting_id,
                file_id,
                generation,
                snapshot,
            )
        except BaseException as original:
            try:
                _rollback_generation(meeting_id, file_id, generation)
            except Exception as rollback_error:
                logger.critical(
                    "Native index rollback failed for meeting=%d file=%d",
                    meeting_id,
                    file_id,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Index replacement failed and rollback also failed: {rollback_error}"
                ) from original
            raise


def _delete_raganything_doc_id(doc_id: str) -> None:
    """Delete one LightRAG document and propagate failures to the caller."""
    from ._raganything import _get_raganything, _run_async

    ra = _get_raganything()

    async def _delete() -> None:
        await ra.lightrag.adelete_by_doc_id(doc_id)

    _run_async(_delete())


def _remove_from_raganything(meeting_id: int, file_id: int | None = None) -> None:
    """Remove meeting chunks from RAGAnything/LightRAG by doc_id.

    M-15: On deletion failure, records a pending deletion so the startup
    reconciler can retry it instead of silently leaving orphan data.
    """
    doc_id = f"meeting_{meeting_id}_file_{file_id if file_id is not None else 'unknown'}"
    try:
        _delete_raganything_doc_id(doc_id)
        logger.info("Meeting %d: removed from RAGAnything (doc_id=%s)", meeting_id, doc_id)
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

    with get_write_connection() as conn:
        if file_id is None:
            delete_bm25_chunks_by_meeting(conn, meeting_id)
        else:
            delete_bm25_chunks_by_file(conn, meeting_id, file_id)

    if file_id is None:
        logger.info("Meeting %d: removed from FTS5 index", meeting_id)
    else:
        logger.info("Meeting %d file %d: removed from FTS5 index", meeting_id, file_id)


def _parse_deletion_scope(scope: str) -> tuple[int, int | None]:
    """Decode the stable scope stored in ``pending_vector_deletions``."""
    match = _DELETION_SCOPE_RE.fullmatch(scope)
    if not match:
        raise ValueError(f"Invalid pending deletion scope: {scope!r}")
    meeting_id = int(match.group("meeting_id"))
    raw_file_id = match.group("file_id")
    return meeting_id, int(raw_file_id) if raw_file_id is not None else None


def retry_pending_index_deletion(collection: str, scope: str) -> None:
    """Retry one previously failed index deletion without re-queueing it.

    The reconciler owns retry accounting, so this helper deliberately lets
    every failure propagate instead of creating duplicate queue rows.
    """
    if collection == "raganything":
        _delete_raganything_doc_id(scope)
        return

    meeting_id, file_id = _parse_deletion_scope(scope)
    if collection == "chroma":
        where: dict[str, Any]
        if file_id is None:
            where = {"meeting_id": meeting_id}
        else:
            where = {"$and": [{"meeting_id": meeting_id}, {"file_id": file_id}]}
        with vectorstore_write_lock():
            get_vectorstore().delete(where=where)
        return
    if collection == "bm25":
        _remove_from_bm25(meeting_id, file_id)
        return
    if collection == "summary":
        _remove_summary_vectors(meeting_id, file_id)
        return
    if collection == "meeting":
        # Legacy catch-all jobs predate the typed per-store entries. Retrying
        # the idempotent operation is safe; any partial failure is re-queued as
        # a typed job by ``delete_meeting_chunks``.
        delete_meeting_chunks(meeting_id, file_id)
        return
    raise ValueError(f"Unknown pending deletion collection: {collection!r}")


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
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES ('summary', ?)",
                    (scope_label,),
                )
        except Exception:
            logger.debug("Failed to record pending summary deletion", exc_info=True)

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
        result["vector"] = sum(
            1
            for metadata in (all_data.get("metadatas") or [])
            if not isinstance(metadata, dict) or metadata.get("file_id") is None
        )
    except Exception:
        logger.debug("Could not count legacy vector chunks", exc_info=True)

    try:
        from ...core.database import get_connection

        with get_connection() as conn:
            # json_array_length(JSON_OBJECT) returns zero, so it must not be
            # used as an emptiness check here: doing so flags every valid row.
            # CASE also prevents json_type() from evaluating malformed JSON.
            count = conn.execute(
                "SELECT COUNT(*) FROM bm25_index WHERE CASE "
                "WHEN metadata IS NULL OR NOT json_valid(metadata) THEN 1 "
                "WHEN json_type(metadata, '$.file_id') IS NULL THEN 1 "
                "ELSE 0 END = 1"
            ).fetchone()[0]
        result["bm25"] = count
    except Exception:
        logger.debug("Could not count legacy BM25 chunks", exc_info=True)

    return result


_BACKFILL_BATCH_SIZE = 5000


def _backfill_legacy_bm25_metadata() -> int:
    """Backfill ``file_id`` and ``chunk_id`` into legacy BM25 metadata.

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
                "SELECT value FROM bm25_stats WHERE key='legacy_metadata_v2_last_indexed_id'"
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
                    decoded = json.loads(r["metadata"])
                    meta = decoded if isinstance(decoded, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                existing = r["chunk_id"]
                changed = False
                file_id = meta.get("file_id")
                if file_id is None:
                    file_id = _file_id_from_chunk_id(existing, r["meeting_id"])
                    if file_id is not None:
                        meta["file_id"] = file_id
                        changed = True
                chunk_index = meta.get("chunk_index", 0)

                if not meta.get("chunk_id"):
                    if existing:
                        meta["chunk_id"] = existing
                    else:
                        prefix = _chunk_id_prefix(
                            r["meeting_id"],
                            file_id,
                            meta.get("source_kind"),
                            meta.get("index_generation"),
                        )
                        meta["chunk_id"] = f"{prefix}_chunk_{chunk_index}"
                    changed = True

                if changed:
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
                    "INSERT OR REPLACE INTO bm25_stats (key, value) "
                    "VALUES ('legacy_metadata_v2_last_indexed_id', ?)",
                    (last_id,),
                )

        if updated:
            logger.info("Backfilled legacy metadata in %d BM25 rows", updated)
    except Exception:
        logger.debug("BM25 metadata backfill skipped or failed", exc_info=True)
    return updated


def _remove_summary_vectors(meeting_id: int, file_id: int | None = None) -> None:
    """Remove summary vectors for the given meeting / file scope."""
    from ._summary_vectorstore import delete_file_summary, delete_meeting_summaries

    if file_id is not None:
        delete_file_summary(file_id)
    else:
        delete_meeting_summaries(meeting_id)
        # Also clean the dedicated meeting-summary collection.
        from ._meeting_summary_vectorstore import delete_meeting_summary

        delete_meeting_summary(meeting_id)
