"""Settings rebuild tasks - vector and multimodal index rebuilding."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request

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
    router,
    try_acquire_multimodal_rebuild,
    try_acquire_vectors_rebuild,
)

# O-CONC-2: Batch size for bulk-copying chunks between Chroma collections.
# 1000 balances memory usage against fewer round-trips.
_COPY_BATCH_SIZE = 1000


def _try_copy_collection_chunks(
    chroma_client: Any,
    source_name: str,
    target_name: str,
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
        offset += _COPY_BATCH_SIZE

    logger.info("Fast-copied %d chunks (skipped re-embedding)", copied)
    return copied


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
    chroma_client = None

    try:
        import chromadb
        from langchain_chroma import Chroma

        from ....core import database as db
        from ....services.embedder import get_embeddings
        from ....services.rag import index_meeting
        from ....services.rag._bm25_maintenance import rebuild_bm25_from_chroma
        from ....services.rag._vectorstore import reset_vectorstore

        # --- Phase 1: Create shadow collection ---
        def _create_shadow_collection() -> tuple[Any, Any]:
            embeddings = get_embeddings()
            client = chromadb.PersistentClient(path=str(settings.VECTOR_DB_DIR))
            client.get_or_create_collection(
                name=shadow_name,
                metadata={"embedding_dimension": settings.EMBEDDING_DIMENSION},
            )
            shadow_vs = Chroma(
                client=client,
                collection_name=shadow_name,
                embedding_function=embeddings,
            )
            return client, shadow_vs

        chroma_client, shadow_vs = await asyncio.to_thread(_create_shadow_collection)
        logger.info("Created shadow collection '%s' for rebuild", shadow_name)

        # --- Phase 1.5: Fast-copy from live collection (O-CONC-2) ---
        # When the embedding model hasn't changed, bulk-copy chunks with
        # their existing embeddings instead of re-embedding every transcript.
        # This turns a rebuild that costs hundreds of API calls into a
        # seconds-long, zero-API-cost operation.
        _copied = await asyncio.to_thread(
            _try_copy_collection_chunks, chroma_client, "meetings", shadow_name
        )
        if _copied > 0:
            logger.info("Fast-copy succeeded (%d chunks); skipping Phase 2 re-index", _copied)
            # Skip to Phase 3 (swap)
        else:
            # --- Phase 2: Index all files into shadow ---
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
                        WHERE mf.status='ready' AND mf.transcript IS NOT NULL
                        ORDER BY mf.meeting_id, mf.created_at
                        """
                    ).fetchall()

            rows = await asyncio.to_thread(_fetch_ready_files)
            logger.info("Rebuilding %d files into shadow '%s'", len(rows), shadow_name)

            for row in rows:
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
                    metadata = {
                        "title": row["meeting_title"],
                        "file_type": row["file_type"],
                        "file_id": file_id,
                        "file_name": row["file_name"],
                        "meeting_date": (int(meeting_date.replace("-", "")) if meeting_date else 0),
                        "user_id": row.get("user_id", "default"),
                        "chunk_strategy_route": "text",
                    }
                    await asyncio.to_thread(
                        index_meeting,
                        meeting_id=meeting_id,
                        text=row["transcript"],
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

        # --- Phase 3: Atomic swap (three-step rename) ---
        def _swap_collections() -> None:
            live_old_name = "meetings_retired"
            # Step 1: Rename live → retired
            try:
                chroma_client.rename_collection("meetings", live_old_name)
            except Exception:
                logger.warning("Live 'meetings' collection not found; skipping rename to retired")
            # Step 2: Rename shadow → live
            try:
                chroma_client.rename_collection(shadow_name, "meetings")
            except Exception:
                # Rollback: restore old live collection
                logger.error("Failed to rename shadow to 'meetings'; attempting rollback")
                try:
                    chroma_client.rename_collection(live_old_name, "meetings")
                except Exception:
                    logger.error("Rollback failed — manual intervention required")
                raise
            # Step 3: Drop retired (old live)
            try:
                chroma_client.delete_collection(live_old_name)
            except Exception:
                logger.debug("Failed to drop retired collection '%s'", live_old_name)
            reset_vectorstore()

        await asyncio.to_thread(_swap_collections)
        logger.info("Atomic swap complete: shadow '%s' → 'meetings'", shadow_name)

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

        await asyncio.to_thread(_invalidate_summaries)

        # --- Phase 4: Rebuild BM25 from new Chroma data ---
        if settings.HYBRID_SEARCH_ENABLED:
            logger.info("Rebuilding BM25 index from new Chroma data")
            await asyncio.to_thread(rebuild_bm25_from_chroma, True)

        logger.info("Vector rebuild completed (shadow swap)")
    except Exception:
        # Cleanup: drop the shadow collection on any failure
        if chroma_client is not None:
            try:
                chroma_client.delete_collection(shadow_name)
            except Exception:
                logger.warning("Failed to cleanup shadow '%s'", shadow_name, exc_info=True)
        logger.warning("Vector rebuild failed; live collection is unchanged", exc_info=True)
        raise
    finally:
        rebuild_state.vectors = False


async def _rebuild_multimodal_task(epoch: int) -> None:
    """Background task: backfill multimodal index for ready files missing doc IDs."""
    indexed = 0
    failed = 0
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
                        mf.transcript
                    FROM meeting_files mf
                    WHERE mf.status='ready'
                      AND (mf.raganything_doc_id IS NULL OR mf.raganything_doc_id='')
                    ORDER BY mf.id
                    """
                ).fetchall()
                return [dict(r) for r in rows]

        rows = await asyncio.to_thread(_fetch_candidates)
        logger.info("Starting multimodal backfill for %d files", len(rows))
        for row in rows:
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
                        metadata={"title": file_name, "file_type": file_type},
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
                        metadata={"title": file_name, "file_type": file_type},
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
        rebuild_state.multimodal = False


def _reset_rebuild_flag(future: asyncio.Task | None = None) -> None:
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


@router.post("/rebuild-vectors", response_model=MessageResponse)
@limiter.limit("5/minute")
async def rebuild_vectors(request: Request) -> dict[str, str]:
    """Trigger async rebuild of vector indexes from existing transcripts."""
    if not try_acquire_vectors_rebuild():
        raise HTTPException(status_code=409, detail="Vector rebuild already in progress")
    epoch = get_settings_epoch()
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
