import asyncio

from fastapi import Depends, HTTPException, Request

from ....api.middleware import limiter
from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from ....core.security import verify_api_key
from ....models.schemas import MessageResponse
from ._common import _ownership_filter, logger, router


@router.delete("/{meeting_id}", response_model=MessageResponse)
@limiter.limit("10/minute")
async def delete_meeting(
    request: Request, meeting_id: int, principal: dict = Depends(verify_api_key)
):
    """Delete a meeting and all its files"""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")
            files = db.list_meeting_files(conn, meeting_id, user_id=ownership)
            return m, files

    m, files = await asyncio.to_thread(_fetch)

    from ....services.jobs import cancel_durable_jobs

    for file_row in files:
        file_id = int(file_row["id"])
        await cancel_durable_jobs(
            kind="file_processing", dedupe_prefix=f"file:{file_id}", exact=True
        )
        await cancel_durable_jobs(kind="file_summary", dedupe_prefix=f"file:{file_id}", exact=True)
    await cancel_durable_jobs(
        kind="meeting_summary", dedupe_prefix=f"meeting:{meeting_id}", exact=True
    )

    # Invalidate cached file summaries before deleting
    try:
        from ....services.chain._generate_helpers import invalidate_file_summaries

        invalidate_file_summaries([f["id"] for f in files])
    except Exception:
        logger.warning("Failed to invalidate cached file summaries before delete", exc_info=True)

    def _delete_db() -> list[str]:
        with get_write_connection() as conn:
            affected_memories: list[str] = []
            existing = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not existing:
                raise HTTPException(404, "Meeting not found")
            for file_row in files:
                affected_memories.extend(
                    db.detach_memory_file_evidence(
                        conn,
                        user_id=principal["user_id"],
                        file_id=int(file_row["id"]),
                    )
                )
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions (collection, embedding_id) "
                    "VALUES ('file', ?)",
                    (str(file_row["file_path"]),),
                )
            from ....core.config import settings

            conn.execute(
                "INSERT OR IGNORE INTO pending_vector_deletions (collection, embedding_id) "
                "VALUES ('directory', ?)",
                (str(settings.UPLOAD_DIR / "meeting_assets" / str(meeting_id)),),
            )
            db.delete_meeting(conn, meeting_id, user_id=ownership)
            return list(dict.fromkeys(affected_memories))

    changed_memories = await asyncio.to_thread(_delete_db)

    from ....services.memory._service._index_sync import index_current_memory

    for key in changed_memories:
        await asyncio.to_thread(index_current_memory, principal["user_id"], key)

    # Clean up vector data after SQL delete; queue retries when vector store fails.
    try:
        from ....services.rag import delete_meeting_chunks

        delete_meeting_chunks(meeting_id)
    except Exception:
        logger.error(
            "Failed to clean up vectors for meeting %d; queued retry",
            meeting_id,
            exc_info=True,
        )

        def _queue_vector_delete() -> None:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES (?, ?)",
                    ("meeting", str(meeting_id)),
                )

        await asyncio.to_thread(_queue_vector_delete)

    # Clean up meeting-level summary vectors (dedicated collection)
    try:
        from ....services.chain._meeting_summary_lifecycle import invalidate_meeting_summary

        await asyncio.to_thread(invalidate_meeting_summary, meeting_id)
    except Exception:
        logger.debug(
            "Failed to clean up meeting summary vectors for %d",
            meeting_id,
            exc_info=True,
        )

    # Process durable filesystem/vector deletion jobs immediately.  Failures
    # remain queued for the lifecycle reconciler.
    from ....services.memory._service._crud import cleanup_pending_vector_deletions

    try:
        await asyncio.to_thread(
            cleanup_pending_vector_deletions,
            collections={"file", "directory"},
        )
    except Exception:
        logger.warning(
            "Immediate meeting resource cleanup failed; jobs remain queued", exc_info=True
        )

    audit_log("delete", "meeting", meeting_id, detail=f"title={m['title']}")

    return {"message": "Deleted"}
