"""Legacy single-file meeting processor entrypoint."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ...core.config import settings
from ...core.database import get_meeting, get_write_connection, update_meeting_status
from ..parser import parse
from ..rag import index_meeting
from ..transcriber import transcribe
from ._pipeline_common import _ws_notify_complete, _ws_notify_progress

logger = logging.getLogger(__name__)


async def process_meeting(meeting_id: int) -> None:
    """Process an uploaded meeting: transcribe/parse -> chunk -> index -> update status."""
    title = "Unknown"
    try:
        await _ws_notify_progress(meeting_id, "processing", 0.0, "Starting processing")

        def _fetch_and_mark():
            with get_write_connection() as conn:
                m = get_meeting(conn, meeting_id)
                if m:
                    update_meeting_status(conn, meeting_id, "processing")
            return m

        m = await asyncio.to_thread(_fetch_and_mark)
        if not m:
            logger.error("Meeting %d not found", meeting_id)
            return

        file_path = Path(m["file_path"])
        file_type = m["file_type"]
        title = m["title"]
        meeting_user_id = m.get("user_id", "default")
        ws_user_id = meeting_user_id

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.2,
            f"Extracting text from {file_type} file",
            user_id=ws_user_id,
        )

        if file_type in ("video", "audio"):
            text = await transcribe(file_path, provider=settings.ASR_PROVIDER)
        else:
            text = await asyncio.wait_for(
                asyncio.to_thread(parse, file_path),
                timeout=settings.PARSE_TIMEOUT_SECONDS,
            )

        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.6,
            "Text extraction complete",
            user_id=ws_user_id,
        )

        _MIN_EXTRACTED_LENGTH = 50
        text_len = len(text.strip()) if text else 0
        if not text or text_len < _MIN_EXTRACTED_LENGTH:
            logger.warning(
                "Meeting %d: extracted text too short (%d chars), marking as failed",
                meeting_id,
                text_len,
            )
            error_msg = "No text content could be extracted from the file"

            def _mark_empty():
                with get_write_connection() as conn:
                    update_meeting_status(conn, meeting_id, "failed", error_message=error_msg)

            await asyncio.to_thread(_mark_empty)
            return

        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.8,
            "Indexing into vector database",
            user_id=ws_user_id,
        )

        await asyncio.to_thread(
            index_meeting,
            meeting_id=meeting_id,
            text=text,
            metadata={"title": title, "file_type": file_type, "user_id": meeting_user_id},
        )

        def _mark_ready():
            with get_write_connection() as conn:
                update_meeting_status(conn, meeting_id, "ready", transcript=text)

        await asyncio.to_thread(_mark_ready)

        logger.info("Meeting %d processed successfully", meeting_id)
        await _ws_notify_complete(meeting_id, "ready", title, user_id=ws_user_id)

    except Exception as e:
        logger.error("Meeting %d processing failed: %s", meeting_id, e, exc_info=True)
        await _ws_notify_complete(meeting_id, "failed", title, user_id=ws_user_id)
        error_msg = f"{type(e).__name__}: {e}"[:500]
        try:

            def _mark_failed():
                with get_write_connection() as conn:
                    update_meeting_status(conn, meeting_id, "failed", error_message=error_msg)

            await asyncio.to_thread(_mark_failed)
        except Exception:
            logger.error(
                "Failed to update failure status for meeting %d",
                meeting_id,
                exc_info=True,
            )
