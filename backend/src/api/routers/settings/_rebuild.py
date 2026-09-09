"""Settings rebuild tasks - vector and multimodal index rebuilding."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ....api.middleware import limiter
from ....core.audit import audit_log
from ....core.config import settings
from ....core.settings_epoch import get_settings_epoch
from ....models.schemas._common import MessageResponse
from ._common import (
    _active_rebuild_tasks,
    logger,
    rebuild_state,
    release_all_rebuild_locks,
    renew_rebuild_advisory_lock,
    router,
    try_acquire_multimodal_rebuild,
    try_acquire_vectors_rebuild,
)

# O-CONC-2: Batch size for bulk-copying chunks between Chroma collections.
# 1000 balances memory usage against fewer round-trips.
_COPY_BATCH_SIZE = 1000


async def _owned_thread(function, *args, on_cancel=None, **kwargs):
    """Drain a non-cancellable thread before releasing resources it is using."""
    pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(pending)
            break
        except asyncio.CancelledError:
            cancelled = True
            if pending.cancelled():
                raise
    if cancelled:
        if on_cancel is not None:
            on_cancel(result)
        raise asyncio.CancelledError
    return result


def _try_copy_collection_chunks(
    chroma_client: Any,
    source_name: str,
    target_name: str,
    expected_fingerprint: str,
) -> int:
    """Bulk-copy chunks from source to target collection with embeddings intact.

    When the embedding model has not changed, this avoids re-embedding every
    chunk — the most expensive part of a rebuild.  Returns the number of
    chunks copied.

    Returns 0 when the source collection is empty or the embedding dimensions
    are incompatible (signalling the caller to fall through to the full
    re-index path).
    """
    try:
        source_col = chroma_client.get_collection(source_name)
    except Exception:
        logger.debug("Source collection '%s' not found; cannot fast-copy", source_name)
        return 0

    source_count = source_col.count()
    if source_count == 0:
        return 0

    source_dim = (source_col.metadata or {}).get("embedding_dimension")
    target_col = chroma_client.get_collection(target_name)
    target_dim = (target_col.metadata or {}).get("embedding_dimension")

    # Dimensions must be compatible for the copy to be valid.
    if source_dim is not None and target_dim is not None:
        try:
            if int(source_dim) != int(target_dim):
                logger.info(
                    "Embedding dimension mismatch (source=%s, target=%s); "
                    "falling through to full re-index",
                    source_dim,
                    target_dim,
                )
                return 0
        except (TypeError, ValueError):
            pass

    # A same-dimensional model or a different chunking policy is still an
    # incompatible index.  Preflight metadata before copying any IDs so a
    # failed fast path cannot contaminate the shadow collection.
    checked = 0
    offset = 0
    while offset < source_count:
        batch = source_col.get(
            include=["metadatas"],
            limit=_COPY_BATCH_SIZE,
            offset=offset,
        )
        ids = batch.get("ids") or []
        if not ids:
            break
        metadatas = batch.get("metadatas") or []
        if len(metadatas) != len(ids):
            logger.warning("Source index metadata is incomplete; full re-index required")
            return 0
        if any(
            (metadata or {}).get("index_config_fingerprint") != expected_fingerprint
            for metadata in metadatas
        ):
            logger.info("Source index fingerprint is stale; full re-index required")
            return 0
        checked += len(ids)
        offset += len(ids)
    if checked != source_count:
        logger.warning(
            "Source index changed during fast-copy preflight (%d/%d); full re-index required",
            checked,
            source_count,
        )
        return 0

    logger.info(
        "Fast-copying %d chunks from '%s' to '%s' (batch size %d)",
        source_count,
        source_name,
        target_name,
        _COPY_BATCH_SIZE,
    )

    copied = 0
    offset = 0
    while offset < source_count:
        batch = source_col.get(
            include=["embeddings", "documents", "metadatas"],
            limit=_COPY_BATCH_SIZE,
            offset=offset,
        )
        ids = batch.get("ids") or []
        if not ids:
            break
        target_col.add(
            ids=ids,
            embeddings=batch.get("embeddings"),
            documents=batch.get("documents"),
            metadatas=batch.get("metadatas"),
        )
        copied += len(ids)
        offset += len(ids)

    if copied != source_count:
        raise RuntimeError(
            f"Source index changed during fast-copy ({copied}/{source_count} chunks copied)"
        )

    logger.info("Fast-copied %d chunks (skipped re-embedding)", copied)
    return copied


def _swap_vector_collections(client: Any, shadow_name: str, retired_name: str) -> bool:
    """Publish a prepared generation; restore live if the second rename fails."""
    from chromadb.errors import NotFoundError

    try:
        live = client.get_collection("meetings")
    except NotFoundError:
        live = None
    if live is not None:
        live.modify(name=retired_name)
    try:
        client.get_collection(shadow_name).modify(name="meetings")
    except Exception:
        if live is not None:
            client.get_collection(retired_name).modify(name="meetings")
        raise
    return live is not None


async def _rebuild_vectors_task(epoch: int) -> None:
    """Background task: shadow-collection rebuild for atomic vector swap (C-4).

    Instead of deleting and re-indexing files one-by-one into the live
    collection (which leaves the index half-baked on failure), this task:

    1. Creates a shadow Chroma collection.
    2. Indexes every ready file into the shadow (the live collection is
       untouched, so concurrent queries remain consistent).
    3. On success: atomically swaps shadow → live (delete old, rename shadow).
    4. Rebuilds the BM25/FTS5 index from the new Chroma data.
    5. On failure: drops the shadow collection; the live one is unchanged.

    Transcripts are reused as-is — the ASR/parser pipeline is not re-run.
    """
    shadow_name = f"meetings_shadow_{uuid.uuid4().hex[:8]}"
    retired_name = "meetings_retired"
    chroma_client = None
    rows: list[Any] = []
    lease_lost = asyncio.Event()

    async def _renew_lease() -> None:
        while True:
            await asyncio.sleep(30)
            if not await asyncio.to_thread(renew_rebuild_advisory_lock):
                lease_lost.set()
                return

    lease_task = asyncio.create_task(_renew_lease(), name="vector-rebuild-lease")

    try:
        import chromadb
        from langchain_chroma import Chroma

        from ....core import database as db
        from ....core.chroma_security import validate_chroma_runtime
        from ....core.index_manifest import index_config_fingerprint
        from ....services.embedder import get_embeddings
        from ....services.rag import index_meeting
        from ....services.rag._publication import publish_generation, source_snapshot
        from ....services.rag._vectorstore import vectorstore_write_lock

        active_fingerprint = index_config_fingerprint()

        # --- Phase 1: Create shadow collection ---
        def _create_shadow_collection() -> tuple[Any, Any]:
            embeddings = get_embeddings()
            client: Any = chromadb.PersistentClient(path=str(validate_chroma_runtime()))
            try:
                client.get_or_create_collection(
                    name=shadow_name,
                    metadata={
                        "embedding_dimension": settings.EMBEDDING_DIMENSION,
                        "index_config_fingerprint": active_fingerprint,
                    },
                )
                shadow_vs = Chroma(
                    client=client,
                    collection_name=shadow_name,
                    embedding_function=embeddings,
                )
                return client, shadow_vs
            except BaseException:
                client.close()
                raise

        chroma_client, shadow_vs = await _owned_thread(
            _create_shadow_collection, on_cancel=lambda result: result[0].close()
        )
        logger.info("Created shadow collection '%s' for rebuild", shadow_name)

        def _fetch_ready_files():
            with db.get_connection() as conn:
                return conn.execute(
                    """
                    SELECT mf.id AS file_id,
                           mf.meeting_id AS meeting_id,
                           mf.file_name AS file_name,
                           mf.file_type AS file_type,
                           mf.transcript AS transcript,
                           m.title AS meeting_title,
                           m.meeting_date AS meeting_date,
                           m.user_id AS user_id
                    FROM meeting_files mf
                    JOIN meetings m ON m.id = mf.meeting_id
                    WHERE mf.status='ready'
                    ORDER BY mf.meeting_id, mf.created_at
                    """
                ).fetchall()

        def _capture_source():
            with vectorstore_write_lock():
                return _fetch_ready_files(), source_snapshot()

        rows, expected_snapshot = await asyncio.to_thread(_capture_source)

        # --- Phase 1.5: Fast-copy from live collection (O-CONC-2) ---
        # When the embedding model hasn't changed, bulk-copy chunks with
        # their existing embeddings instead of re-embedding every transcript.
        # This turns a rebuild that costs hundreds of API calls into a
        # seconds-long, zero-API-cost operation.
        _copied = await _owned_thread(
            _try_copy_collection_chunks,
            chroma_client,
            "meetings",
            shadow_name,
            active_fingerprint,
        )
        if _copied > 0:
            logger.info("Fast-copy succeeded (%d chunks); skipping Phase 2 re-index", _copied)
            # Skip to Phase 3 (swap)
        else:
            # --- Phase 2: Index all files into shadow ---
            logger.info("Rebuilding %d files into shadow '%s'", len(rows), shadow_name)
            rebuild_generation = uuid.uuid4().hex

            for row in rows:
                if lease_lost.is_set():
                    raise RuntimeError("Vector rebuild lease lost")
                if epoch != get_settings_epoch():
                    logger.warning(
                        "Settings changed during vector rebuild; cancelling at epoch=%d",
                        epoch,
                    )
                    raise asyncio.CancelledError("Settings changed during vector rebuild")

                meeting_id = row["meeting_id"]
                file_id = row["file_id"]
                try:
                    if epoch != get_settings_epoch():
                        logger.warning(
                            "Settings changed during vector rebuild; "
                            "cancelling before index at epoch=%d",
                            epoch,
                        )
                        raise asyncio.CancelledError("Settings changed during vector rebuild")

                    meeting_date = row["meeting_date"]
                    transcript = str(row["transcript"] or "").strip()
                    if not transcript:
                        raise RuntimeError(
                            f"File {file_id} has no persisted transcript; "
                            "use durable file reprocessing instead of a text-only rebuild"
                        )
                    metadata = {
                        "title": row["meeting_title"],
                        "file_type": row["file_type"],
                        "file_id": file_id,
                        "file_name": row["file_name"],
                        "meeting_date": (int(meeting_date.replace("-", "")) if meeting_date else 0),
                        "user_id": row["user_id"] or "default",
                        "index_config_fingerprint": active_fingerprint,
                        "index_generation": rebuild_generation,
                        "chunk_strategy_route": "text",
                    }
                    await _owned_thread(
                        index_meeting,
                        meeting_id=meeting_id,
                        text=transcript,
                        metadata=metadata,
                        target_vs=shadow_vs,
                        skip_bm25=True,
                    )
                    logger.info("Re-indexed meeting %d file %d into shadow", meeting_id, file_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "Failed to re-index meeting %d file %d: %s",
                        meeting_id,
                        file_id,
                        e,
                        exc_info=True,
                    )
                    raise

        # Only local publication drains readers and acquires the write lock.
        # Embedding and shadow preparation above never hold SQLite's lock.
        if lease_lost.is_set() or not await asyncio.to_thread(renew_rebuild_advisory_lock):
            raise RuntimeError("Vector rebuild lease lost before activation")

        publication = asyncio.create_task(
            asyncio.to_thread(
                publish_generation,
                chroma_client,
                shadow_name,
                retired_name,
                rows,
                active_fingerprint,
                expected_snapshot,
                epoch,
            )
        )
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            # A thread cannot be cancelled. Do not close its client or drop its
            # shadow until the commit/rollback has completed.
            await publication
            raise

        # --- Phase 3.5: Invalidate summary vectorstores (O-RAG-3) ---
        # After swapping the main collection, drop all summary vectors so they
        # regenerate from fresh chunk data on next access, preventing stale
        # summaries from referencing obsolete chunk content.
        def _invalidate_summaries() -> None:
            for coll_name in ("meeting_files_summaries", "meeting_summaries"):
                try:
                    chroma_client.delete_collection(coll_name)
                except Exception:
                    logger.debug(
                        "Summary collection '%s' not found; skipping invalidation",
                        coll_name,
                    )
            # Reset singletons so they re-create against the new dimension.
            try:
                from ....services.rag._summary_vectorstore import reset_summary_vectorstore

                reset_summary_vectorstore()
            except Exception:
                logger.debug("Summary vectorstore reset failed", exc_info=True)
            try:
                from ....services.rag._meeting_summary_vectorstore import (
                    reset_meeting_summary_vectorstore,
                )

                reset_meeting_summary_vectorstore()
            except Exception:
                logger.debug("Meeting summary vectorstore reset failed", exc_info=True)
            # Eagerly repopulate summary vectors instead of relying on lazy regen.
            try:
                from ....services.rag._summary_vectorstore import (
                    sync_missing_file_summary_vectors,
                )

                synced = sync_missing_file_summary_vectors()
                logger.info("Post-rebuild file summary sync: %d vectors", synced)
            except Exception:
                logger.warning("Post-rebuild file summary sync failed", exc_info=True)
            try:
                from ....services.chain._meeting_summary_lifecycle import (
                    reconcile_meeting_summaries,
                )

                result = reconcile_meeting_summaries()
                logger.info("Post-rebuild meeting summary reconcile: %s", result)
            except Exception:
                logger.warning("Post-rebuild meeting summary reconcile failed", exc_info=True)
            logger.info("Summary vectorstores invalidated after rebuild swap")

        await _owned_thread(_invalidate_summaries)

        logger.info("Vector rebuild completed (shadow swap)")
    except (Exception, asyncio.CancelledError):
        # Publication owns rollback; preparation failures only own the shadow.
        if chroma_client is not None:
            try:
                if shadow_name in {
                    collection.name for collection in chroma_client.list_collections()
                }:
                    chroma_client.delete_collection(shadow_name)
            except Exception:
                logger.warning("Failed to cleanup shadow '%s'", shadow_name, exc_info=True)
        logger.warning("Vector rebuild did not finish; consult publication journal", exc_info=True)
        raise
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
        if chroma_client is not None:
            await asyncio.to_thread(chroma_client.close)
        rebuild_state.vectors = False


async def _rebuild_multimodal_task(epoch: int) -> None:
    """Background task: backfill multimodal index for ready files missing doc IDs."""
    indexed = 0
    failed = 0
    lease_lost = asyncio.Event()

    async def _renew_lease() -> None:
        while True:
            await asyncio.sleep(30)
            if not await asyncio.to_thread(renew_rebuild_advisory_lock):
                lease_lost.set()
                return

    lease_task = asyncio.create_task(_renew_lease(), name="multimodal-rebuild-lease")
    try:
        from ....core import database as db
        from ....services.rag._raganything import (
            index_file_with_raganything,
            index_with_raganything,
        )

        def _fetch_candidates() -> list[dict[str, Any]]:
            with db.get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        mf.id,
                        mf.meeting_id,
                        mf.file_type,
                        mf.file_name,
                        mf.file_path,
                        mf.transcript,
                        m.user_id
                    FROM meeting_files mf
                    JOIN meetings m ON m.id=mf.meeting_id
                    WHERE mf.status='ready'
                      AND (mf.raganything_doc_id IS NULL OR mf.raganything_doc_id='')
                    ORDER BY mf.id
                    """
                ).fetchall()
                return [dict(r) for r in rows]

        rows = await asyncio.to_thread(_fetch_candidates)
        logger.info("Starting multimodal backfill for %d files", len(rows))
        for row in rows:
            if lease_lost.is_set():
                raise RuntimeError("Multimodal rebuild lease lost")
            if epoch != get_settings_epoch():
                logger.warning(
                    "Settings changed during multimodal rebuild; cancelling at epoch=%d",
                    epoch,
                )
                raise asyncio.CancelledError("Settings changed during multimodal rebuild")
            file_id = int(row["id"])
            meeting_id = int(row["meeting_id"])
            file_type = str(row.get("file_type") or "")
            file_name = str(row.get("file_name") or f"file-{file_id}")
            doc_id = f"meeting_{meeting_id}_file_{file_id}"
            try:
                if epoch != get_settings_epoch():
                    logger.warning(
                        "Settings changed during multimodal rebuild; "
                        "cancelling before index at epoch=%d",
                        epoch,
                    )
                    raise asyncio.CancelledError("Settings changed during multimodal rebuild")
                file_path = row.get("file_path")
                if (
                    isinstance(file_path, str)
                    and file_path
                    and file_type in {"pdf", "ppt", "doc", "xls", "csv", "image", "video"}
                ):
                    await asyncio.to_thread(
                        index_file_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        file_path=file_path,
                        metadata={
                            "title": file_name,
                            "file_type": file_type,
                            "user_id": row.get("user_id", "default"),
                        },
                    )
                else:
                    transcript = str(row.get("transcript") or "").strip()
                    if not transcript:
                        logger.info(
                            "Skipping multimodal backfill for file %d (empty transcript)",
                            file_id,
                        )
                        continue
                    await asyncio.to_thread(
                        index_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        text=transcript,
                        file_path=str(file_path or ""),
                        metadata={
                            "title": file_name,
                            "file_type": file_type,
                            "user_id": row.get("user_id", "default"),
                        },
                    )

                def _mark_indexed(
                    *,
                    file_id: int = file_id,
                    doc_id: str = doc_id,
                    meeting_id: int = meeting_id,
                ) -> None:
                    with db.get_write_connection() as conn:
                        db.update_meeting_file_raganything(
                            conn,
                            file_id,
                            doc_id=doc_id,
                            indexed_at=datetime.now(UTC).isoformat(),
                        )
                        db.mark_raganything_indexed(
                            conn,
                            file_id=file_id,
                            meeting_id=meeting_id,
                            doc_id=doc_id,
                            indexed_at=datetime.now(UTC).isoformat(),
                        )

                await asyncio.to_thread(_mark_indexed)
                indexed += 1
            except Exception:
                failed += 1

                def _mark_failed(*, file_id: int = file_id, meeting_id: int = meeting_id) -> None:
                    with db.get_write_connection() as conn:
                        db.mark_raganything_failed(
                            conn,
                            file_id=file_id,
                            meeting_id=meeting_id,
                            error="multimodal rebuild failed",
                        )

                await asyncio.to_thread(_mark_failed)
                logger.warning("Multimodal backfill failed for file %d", file_id, exc_info=True)

        logger.info("Multimodal backfill completed indexed=%d failed=%d", indexed, failed)
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
        rebuild_state.multimodal = False


def _reset_rebuild_flag(future: asyncio.Task | None = None) -> None:
    if future is not None:
        rebuild_state.vector_result = (
            "cancelled" if future.cancelled() else "failed" if future.exception() else "completed"
        )
    if future is not None and not future.cancelled():
        exc = future.exception()
        if exc is not None:
            logger.error("Vector rebuild task failed: %s", exc, exc_info=exc)
    rebuild_state.vectors = False
    release_all_rebuild_locks()


def _reset_multimodal_rebuild_flag(future: asyncio.Task | None = None) -> None:
    if future is not None and not future.cancelled():
        exc = future.exception()
        if exc is not None:
            logger.error("Multimodal rebuild task failed: %s", exc, exc_info=exc)
    rebuild_state.multimodal = False
    release_all_rebuild_locks()


class VectorRebuildStatus(BaseModel):
    active: bool
    result: Literal["idle", "running", "completed", "failed", "cancelled"]


@router.get("/rebuild-status", response_model=VectorRebuildStatus)
async def vector_rebuild_status() -> VectorRebuildStatus:
    """Process-local result; restarts reset it to idle (single-instance API)."""
    return VectorRebuildStatus(active=rebuild_state.active, result=rebuild_state.vector_result)


@router.post("/rebuild-vectors", response_model=MessageResponse)
@limiter.limit("5/minute")
async def rebuild_vectors(request: Request) -> dict[str, str]:
    """Trigger async rebuild of vector indexes from existing transcripts."""
    if not try_acquire_vectors_rebuild():
        raise HTTPException(status_code=409, detail="Vector rebuild already in progress")
    epoch = get_settings_epoch()
    rebuild_state.vector_result = "running"
    task = asyncio.create_task(_rebuild_vectors_task(epoch))
    _active_rebuild_tasks.add(task)
    task.add_done_callback(lambda t: (_reset_rebuild_flag(t), _active_rebuild_tasks.discard(t)))
    audit_log("rebuild_start", "settings", f"vectors@epoch={epoch}")
    return {"message": "Vector rebuild started in background"}


@router.post("/rebuild-multimodal", response_model=MessageResponse)
@limiter.limit("5/minute")
async def rebuild_multimodal(request: Request) -> dict[str, str]:
    """Trigger async backfill of multimodal index for existing ready files."""
    if not settings.RAGANYTHING_ENABLED:
        raise HTTPException(status_code=400, detail="RAGAnything is disabled")
    if not try_acquire_multimodal_rebuild():
        raise HTTPException(status_code=409, detail="Multimodal rebuild already in progress")
    epoch = get_settings_epoch()
    task = asyncio.create_task(_rebuild_multimodal_task(epoch))
    _active_rebuild_tasks.add(task)
    task.add_done_callback(
        lambda t: (_reset_multimodal_rebuild_flag(t), _active_rebuild_tasks.discard(t))
    )
    audit_log("rebuild_start", "settings", f"multimodal@epoch={epoch}")
    return {"message": "Multimodal rebuild started in background"}
