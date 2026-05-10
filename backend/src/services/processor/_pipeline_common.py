"""Shared helpers for processor pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ...core.config import settings
from ...core.database import (
    get_meeting_file_status_counts,
    get_write_connection,
    update_meeting_file_summary,
    update_meeting_status,
)
from ...core.tracing import otel_span
from ..websocket import websocket_manager

logger = logging.getLogger(__name__)


def _file_content_hash(file_path: Path) -> str:
    """Compute SHA-256 for a file's raw bytes."""
    import hashlib

    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _has_non_text_indexable_content(artefact) -> bool:
    """Whether an artefact can still be indexed when extracted plain text is short."""
    if artefact.segments:
        return True
    if artefact.aux_segments:
        return True
    if artefact.structured_kind == "captions" and artefact.structured_json:
        try:
            payload = json.loads(artefact.structured_json)
        except (TypeError, ValueError):
            payload = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                caption = item.get("caption")
                if (
                    isinstance(caption, str)
                    and len(caption.strip()) >= settings.VISION_CAPTION_MIN_CHARS
                ):
                    return True
                ocr_text = item.get("ocr_text")
                if (
                    isinstance(ocr_text, str)
                    and len(ocr_text.strip()) >= settings.RAG_IMAGE_OCR_MIN_LENGTH
                ):
                    return True
    parsed = artefact.parsed_doc
    if parsed is None:
        return False
    for page in parsed.pages:
        if page.image_assets or page.table_assets:
            return True
        if page.images or page.tables:
            return True
    return False


async def _ws_notify_progress(
    meeting_id: int, status: str, progress: float, message: str, *, user_id: str | None = None
) -> None:
    """Safely notify WebSocket subscribers; log and continue on failure."""
    try:
        with otel_span(
            "processor.ws_notify_progress",
            {"meeting_id": meeting_id, "status": status},
        ):
            await websocket_manager.notify_progress(
                meeting_id,
                status,
                progress,
                message,
                user_id=user_id,
            )
    except Exception:
        logger.warning("WS progress notify failed for meeting %d", meeting_id, exc_info=True)


async def _ws_notify_complete(
    meeting_id: int, status: str, title: str, *, user_id: str | None = None
) -> None:
    """Safely notify WebSocket subscribers; log and continue on failure."""
    try:
        with otel_span(
            "processor.ws_notify_complete",
            {"meeting_id": meeting_id, "status": status},
        ):
            await websocket_manager.notify_complete(meeting_id, status, title, user_id=user_id)
    except Exception:
        logger.warning("WS complete notify failed for meeting %d", meeting_id, exc_info=True)


def _metric_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_float(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _build_ingest_observability_metadata(metrics: dict[str, int | float | str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in (
        "cleaned_line_count",
        "removed_page_marker_count",
        "removed_repetitive_line_count",
        "image_asset_count",
        "image_caption_success_count",
        "image_ocr_success_count",
    ):
        value = _metric_int(metrics.get(key))
        if value is not None:
            out[f"ingest_{key}"] = value
    return out


def _persist_file_summary(
    file_id: int, summary: str, key_points: list[str], *, meeting_id: int | None = None
) -> None:
    with get_write_connection() as conn:
        update_meeting_file_summary(
            conn,
            file_id,
            summary=summary,
            key_points_json=json.dumps(key_points),
        )
    _sync_file_summary_vector(file_id, summary, meeting_id=meeting_id)


def _sync_file_summary_vector(file_id: int, summary: str, *, meeting_id: int | None = None) -> None:
    """Mirror the file's summary into the summary vector collection.

    Fail-open: any error logs and returns. Summary routing falls back
    to the legacy enumeration when its collection is unavailable, so a
    failure here cannot break ingest.
    """
    summary = (summary or "").strip()
    try:
        from ...core.database import get_connection, get_meeting_file
        from ..rag._summary_vectorstore import delete_file_summary, upsert_file_summary

        if not summary:
            delete_file_summary(file_id)
            return

        if meeting_id is None:
            with get_connection() as conn:
                row = get_meeting_file(conn, file_id)
            if not row:
                return
            meeting_id = int(row["meeting_id"])
            file_name = row.get("file_name")
            file_type = row.get("file_type")
        else:
            # meeting_id was provided by the caller — avoid the extra DB round-trip
            file_name = None
            file_type = None

        upsert_file_summary(
            file_id,
            summary,
            meeting_id=meeting_id,
            file_name=file_name,
            file_type=file_type,
        )
    except Exception:
        logger.warning("Summary vector sync failed for file %d", file_id, exc_info=True)


def _update_meeting_status_from_files(conn, meeting_id: int) -> None:
    """Update meeting status based on its files' statuses.

    Lifecycle: processing -> summarizing -> ready.
    - 'processing': at least one file is still extracting / parsing content.
    - 'summarizing': all files done parsing; either some are still generating
        per-file summaries, or all files ready but the meeting-level summary
        has not been persisted yet (when MEETING_AUTO_SUMMARIZE_FILES is on).
    - 'ready': all files ready and meeting summary already persisted.
    - 'failed': at least one file failed (and no files succeeded, or even one
        error in a partial-success batch).
    """
    counts = get_meeting_file_status_counts(conn, meeting_id)
    ready_count = counts.get("ready", 0)
    error_count = counts.get("error", 0)
    processing_count = counts.get("processing", 0)
    summarizing_count = counts.get("summarizing", 0)
    total = ready_count + error_count + processing_count + summarizing_count

    if total == 0:
        return

    if processing_count > 0:
        # Content still being extracted from at least one file.
        new_status = "processing"
    elif summarizing_count > 0:
        # All files have finished parsing; only per-file summaries left.
        new_status = "summarizing"
    elif error_count > 0 and ready_count == 0:
        # All files errored — meeting is fully failed.
        new_status = "failed"
    elif error_count > 0 and ready_count > 0:
        # Mixed outcomes are still a failed ingest at the meeting level.
        ratio = ready_count / max(total, 1)
        logger.info(
            "Meeting %d: partial success — %d/%d files ready (%.0f%%), %d files errored",
            meeting_id,
            ready_count,
            total,
            ratio * 100,
            error_count,
        )
        new_status = "failed"
    elif ready_count > 0 and settings.MEETING_AUTO_SUMMARIZE_FILES:
        # All files ready, no errors. Defer to 'summarizing' until the
        # meeting-level summary has been persisted; flip back to 'ready'
        # only when the summary is already complete (avoid bouncing a
        # finalized meeting back into 'summarizing').
        row = conn.execute(
            "SELECT summary_status FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()
        current_summary = row["summary_status"] if row else None
        new_status = "ready" if current_summary == "ready" else "summarizing"
    elif ready_count > 0:
        new_status = "ready"
    else:
        return

    try:
        update_meeting_status(conn, meeting_id, new_status)
    except ValueError:
        logger.debug(
            "Skipping meeting status transition -> %s for meeting %d "
            "(concurrent file completion race)",
            new_status,
            meeting_id,
        )


async def _ws_notify_summary_status(
    meeting_id: int,
    file_id: int | None = None,
    summary_status: str = "",
) -> None:
    """Notify WebSocket clients about summary_status changes."""
    try:
        payload: dict[str, Any] = {
            "type": "summary_status",
            "meeting_id": meeting_id,
            "summary_status": summary_status,
        }
        if file_id is not None:
            payload["file_id"] = file_id
        await websocket_manager.broadcast(payload)
    except Exception:
        logger.warning("WS summary_status notify failed for meeting %d", meeting_id, exc_info=True)


async def schedule_post_ready_summary(file_id: int, meeting_id: int) -> None:
    """Auto-generate per-file summary after the file reaches ``status='summarizing'``.

    Idempotent: skips when ``summary_status`` is already ``'ready'``.
    On completion (success or failure) transitions the file from
    ``status='summarizing'`` to ``status='ready'`` and updates meeting status.
    """
    from ...core.database import get_connection, get_write_connection, update_file_summary_status
    from ..chain._per_file_summary import generate_per_file_summary

    # Idempotency guard
    with get_connection() as conn:
        from ...core.database import get_meeting_file

        row = get_meeting_file(conn, file_id)
    if not row or row.get("summary_status") == "ready":
        return

    file_type = row.get("file_type", "unknown")
    file_name = row.get("file_name", "")
    transcript = (row.get("transcript") or "").strip()
    is_summarizing = row.get("status") == "summarizing"

    def _finalize_file_status() -> None:
        """Transition file from summarizing → ready and recompute meeting status."""
        if not is_summarizing:
            return
        with get_write_connection() as wconn:
            from ...core.database import update_meeting_file_status

            update_meeting_file_status(wconn, file_id, "ready")
            _update_meeting_status_from_files(wconn, meeting_id)

    if not transcript:
        # Nothing to summarize — advance file to ready immediately
        update_file_summary_status(file_id, "ready")
        await asyncio.to_thread(_finalize_file_status)
        await _maybe_finalize_meeting_summary(meeting_id)
        return

    update_file_summary_status(file_id, "generating")
    await _ws_notify_summary_status(meeting_id, file_id, "generating")

    try:
        # Load segments for audio/video
        segments: list[dict] | None = None
        if file_type in ("video", "audio"):
            raw = row.get("segments_json")
            if raw:
                try:
                    segments = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    segments = None

        summary, key_points = await generate_per_file_summary(
            file_type=file_type,
            file_name=file_name,
            text=transcript,
            segments=segments,
        )
        await asyncio.to_thread(
            _persist_file_summary,
            file_id,
            summary,
            key_points,
            meeting_id=meeting_id,
        )
        update_file_summary_status(file_id, "ready")
        await asyncio.to_thread(_finalize_file_status)
        await _ws_notify_summary_status(meeting_id, file_id, "ready")
        # Bridge: emit a progress event so the frontend's existing
        # useMeetings WS handler triggers fetchMeetings().
        from ...core.database import get_meeting

        with get_connection() as conn:
            m = get_meeting(conn, meeting_id)
        ws_uid = m.get("user_id") if m else None
        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.95,
            f"Summary ready: {file_name}",
            user_id=ws_uid,
        )
    except Exception:
        logger.warning("Post-ready summary failed for file %d", file_id, exc_info=True)
        update_file_summary_status(file_id, "failed")
        # Advance file to ready even on summary failure — content is indexed
        await asyncio.to_thread(_finalize_file_status)
        await _ws_notify_summary_status(meeting_id, file_id, "failed")

    await _maybe_finalize_meeting_summary(meeting_id)


async def _maybe_finalize_meeting_summary(meeting_id: int) -> None:
    """Trigger meeting-level summary when all files have finished summarizing.

    Waits for both: no files in 'summarizing' status, and all 'ready' files
    have a terminal summary_status ('ready' or 'failed').
    """
    from ...core.database import get_connection

    with get_connection() as conn:
        summarizing_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM meeting_files WHERE meeting_id=? AND status='summarizing'",
            (meeting_id,),
        ).fetchone()["cnt"]
        if summarizing_count > 0:
            return

        rows = conn.execute(
            "SELECT summary_status FROM meeting_files WHERE meeting_id=? AND status='ready'",
            (meeting_id,),
        ).fetchall()

    if not rows:
        return

    # Check if all ready files have reached a terminal summary state
    pending = sum(1 for r in rows if (r["summary_status"] or "pending") not in ("ready", "failed"))
    if pending > 0:
        return

    # All files done — trigger meeting-level summary
    _maybe_trigger_meeting_summary(meeting_id)


def _maybe_trigger_meeting_summary(meeting_id: int) -> None:
    """Fire-and-forget meeting summary generation.

    Reuses the existing ``_auto_summarize_meeting`` if the event loop is
    running; otherwise silently skips (will be caught on next startup).
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    from ._pipeline_summary import _auto_summarize_meeting

    _task = loop.create_task(_auto_summarize_meeting(meeting_id))
    _task.add_done_callback(lambda _t: None)  # prevent "task not awaited" warning
