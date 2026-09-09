"""Durable scheduling facade for meeting-file processing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ...core import database as db
from ...core.database import get_write_connection
from ..jobs import enqueue_durable_job

logger = logging.getLogger(__name__)


def active_processing_file_ids() -> set[int]:
    """Return file IDs represented by queued or leased durable jobs."""
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT dedupe_key
            FROM durable_jobs
            WHERE kind='file_processing' AND status IN ('pending', 'running')
            """
        ).fetchall()
    active: set[int] = set()
    for row in rows:
        key = str(row["dedupe_key"])
        if key.startswith("file:"):
            try:
                active.add(int(key.removeprefix("file:")))
            except ValueError:
                logger.warning("Ignoring malformed durable processing key %s", key)
    return active


async def schedule_meeting_file_processing(
    file_id: int,
    *,
    force_meeting_summary: bool = False,
    force_native_reindex: bool = False,
) -> bool:
    """Persist one idempotent file-processing job before returning."""
    already_active = file_id in active_processing_file_ids()
    with db.get_connection() as conn:
        row = db.get_meeting_file(conn, file_id)
    source_revision = int((row or {}).get("source_revision") or 1)
    await enqueue_durable_job(
        kind="file_processing",
        dedupe_key=f"file:{file_id}",
        payload={
            "file_id": file_id,
            "force_meeting_summary": force_meeting_summary,
            "force_native_reindex": force_native_reindex,
            "source_revision": source_revision,
        },
        max_attempts=3,
    )
    return not already_active


async def resume_interrupted_processing(*, stale_only: bool = False) -> int:
    """Resume persisted processing rows after a crash or lost task."""

    def _load() -> list[dict]:
        with db.get_connection() as conn:
            sql = "SELECT id, meeting_id, file_path FROM meeting_files WHERE status='processing'"
            if stale_only:
                sql += (
                    " AND COALESCE(processing_started_at, updated_at) "
                    "< datetime('now', '-30 minutes')"
                )
            return [dict(row) for row in conn.execute(sql).fetchall()]

    rows = await asyncio.to_thread(_load)
    scheduled = 0
    for row in rows:
        file_id = int(row["id"])
        if file_id in active_processing_file_ids():
            continue
        if not Path(row["file_path"]).is_file():

            def _mark_missing(file_id: int = file_id) -> None:
                with get_write_connection() as conn:
                    db.update_meeting_file_status(
                        conn,
                        file_id,
                        "error",
                        error_message="Processing interrupted and source file is missing",
                    )

            await asyncio.to_thread(_mark_missing)
            continue
        if await schedule_meeting_file_processing(file_id):
            scheduled += 1
    if scheduled:
        logger.warning("Resumed %d interrupted meeting-file processing task(s)", scheduled)
    return scheduled
