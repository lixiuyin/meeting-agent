import asyncio

from fastapi import BackgroundTasks, Depends, HTTPException, Request

from ....api.middleware import limiter
from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from ....core.security import verify_api_key
from ....models.schemas import ReprocessResponse
from ._common import _ownership_filter, logger, router


@router.post("/{meeting_id}/reprocess", response_model=ReprocessResponse)
@limiter.limit("5/minute")
async def reprocess_meeting(
    request: Request,
    meeting_id: int,
    background_tasks: BackgroundTasks,
    principal: dict = Depends(verify_api_key),
):
    """Reprocess a meeting file (delete vectors and re-index)"""
    from ....services.websocket import websocket_manager as ws_manager

    ownership = _ownership_filter(principal)

    def _reset():
        with get_write_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")

            # Reset status to processing
            db.update_meeting_status(conn, meeting_id, "processing", user_id=ownership)
            # Reset per-file summary_status so post-ready hook re-runs
            for f in db.list_meeting_files(conn, meeting_id, user_id=ownership):
                db.update_file_summary_status(f["id"], "pending")
            return m

    await asyncio.to_thread(_reset)

    try:
        # Delete old vector chunks
        try:
            from ....services.rag import delete_meeting_chunks

            delete_meeting_chunks(meeting_id)
        except Exception:
            logger.warning("Failed to delete old vectors for meeting %d", meeting_id, exc_info=True)

        # Schedule reprocessing for each file
        from ....services.processor import process_meeting_file

        def _list_files():
            with db.get_connection() as conn:
                return db.list_meeting_files(conn, meeting_id, user_id=ownership)

        files = await asyncio.to_thread(_list_files)

        # Invalidate cached file summaries before reprocessing
        try:
            from ....services.chain._generate_helpers import invalidate_file_summaries

            invalidate_file_summaries([f["id"] for f in files])
        except Exception:
            logger.warning("Failed to invalidate file summaries for reprocess", exc_info=True)

        for f in files:
            background_tasks.add_task(process_meeting_file, f["id"], force_meeting_summary=True)
    except Exception:
        logger.error("Reprocess scheduling failed for meeting %d", meeting_id, exc_info=True)

        def _mark_failed():
            with get_write_connection() as conn:
                for f in db.list_meeting_files(conn, meeting_id, user_id=ownership):
                    db.update_meeting_file_status(
                        conn,
                        f["id"],
                        "error",
                        error_message="Reprocess scheduling failed",
                    )
                db.update_meeting_status(conn, meeting_id, "failed", user_id=ownership)

        await asyncio.to_thread(_mark_failed)
        await ws_manager.notify_meeting_update(meeting_id, {"event": "processing_failed"})
        raise

    audit_log("reprocess", "meeting", meeting_id)

    return ReprocessResponse(message="Reprocessing started", meeting_id=meeting_id)


@router.post("/{meeting_id}/files/{file_id}/reprocess", response_model=ReprocessResponse)
@limiter.limit("5/minute")
async def reprocess_meeting_file(
    request: Request,
    meeting_id: int,
    file_id: int,
    background_tasks: BackgroundTasks,
    principal: dict = Depends(verify_api_key),
):
    """Reprocess a single file in a meeting."""
    from ....services.websocket import websocket_manager as ws_manager

    ownership = _ownership_filter(principal)

    def _reset_file():
        with get_write_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")
            f = db.get_meeting_file(conn, file_id, user_id=ownership)
            if not f or f["meeting_id"] != meeting_id:
                raise HTTPException(404, "File not found")
            db.update_meeting_file_status(conn, file_id, "processing", error_message=None)
            db.update_meeting_status(conn, meeting_id, "processing", user_id=ownership)
        db.update_file_summary_status(file_id, "pending")
        return f

    await asyncio.to_thread(_reset_file)

    try:
        # Invalidate cached file summary for this file
        try:
            from ....services.chain._generate_helpers import invalidate_file_summaries

            invalidate_file_summaries(file_id)
        except Exception:
            logger.warning("Failed to invalidate file summary for reprocess", exc_info=True)

        # Delete old vector chunks for the specific file
        try:
            from ....services.rag import delete_meeting_chunks

            delete_meeting_chunks(meeting_id, file_id=file_id)
        except Exception:
            logger.warning(
                "Failed to delete old vectors for meeting %d file %d",
                meeting_id,
                file_id,
                exc_info=True,
            )

        # Invalidate meeting-level summary — the file content will change,
        # so any existing meeting summary is stale.
        from ....services.chain._meeting_summary_lifecycle import invalidate_meeting_summary

        await asyncio.to_thread(invalidate_meeting_summary, meeting_id)

        from ....services.processor import process_meeting_file

        background_tasks.add_task(process_meeting_file, file_id, force_meeting_summary=True)
    except Exception:
        logger.error("File reprocess scheduling failed for file %d", file_id, exc_info=True)

        def _mark_failed():
            with get_write_connection() as conn:
                db.update_meeting_file_status(
                    conn,
                    file_id,
                    "error",
                    error_message="Reprocess scheduling failed",
                )
                from ....services.processor._pipeline_common import (
                    _update_meeting_status_from_files,
                )

                _update_meeting_status_from_files(conn, meeting_id)

        await asyncio.to_thread(_mark_failed)
        await ws_manager.notify_meeting_update(
            meeting_id, {"event": "processing_failed", "file_id": file_id}
        )
        raise

    audit_log("reprocess", "meeting_file", file_id)
    return ReprocessResponse(message="File reprocessing started", meeting_id=meeting_id)
