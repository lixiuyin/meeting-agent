import asyncio
from pathlib import Path

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

    # Invalidate cached file summaries before deleting
    try:
        from ....services.chain._generate_helpers import invalidate_file_summaries

        invalidate_file_summaries([f["id"] for f in files])
    except Exception:
        logger.warning("Failed to invalidate cached file summaries before delete", exc_info=True)

    def _delete_db() -> None:
        with get_write_connection() as conn:
            existing = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not existing:
                raise HTTPException(404, "Meeting not found")
            db.delete_meeting(conn, meeting_id, user_id=ownership)

    await asyncio.to_thread(_delete_db)

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
                    "INSERT INTO pending_vector_deletions (collection, embedding_id) VALUES (?, ?)",
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

    # Best-effort file cleanup after DB delete.
    for f in files:
        file_path = Path(f["file_path"])
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Failed to delete file for meeting %d: %s",
                meeting_id,
                file_path,
                exc_info=True,
            )

    audit_log("delete", "meeting", meeting_id, detail=f"title={m['title']}")

    return {"message": "Deleted"}
