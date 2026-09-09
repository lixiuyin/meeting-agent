"""Durable summary scheduling shared by ingest and chat services."""

from __future__ import annotations

from .jobs import enqueue_durable_job


async def enqueue_file_summary(file_id: int, meeting_id: int) -> str:
    return await enqueue_durable_job(
        kind="file_summary",
        dedupe_key=f"file:{file_id}",
        payload={"file_id": file_id, "meeting_id": meeting_id},
        max_attempts=3,
    )


async def enqueue_meeting_summary(meeting_id: int) -> str:
    return await enqueue_durable_job(
        kind="meeting_summary",
        dedupe_key=f"meeting:{meeting_id}",
        payload={"meeting_id": meeting_id},
        max_attempts=3,
    )
