"""Schedule evidence-backed memory extraction directly from ingested files."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from ...core import database as db
from ...core.config import settings
from ...core.memory_admission import file_memory_policy
from ...core.metrics import MEMORY_INGEST_WINDOWS_TOTAL
from ..jobs import enqueue_durable_job

logger = logging.getLogger(__name__)


def _evidence_windows(text: str) -> list[tuple[str, int, int]]:
    """Return all overlapping evidence windows with lossless source offsets."""
    if not text.strip():
        return []
    chunks: list[tuple[str, int, int]] = []
    start = 0
    chunk_chars = settings.MEMORY_INGEST_CHUNK_CHARS
    overlap = min(settings.MEMORY_INGEST_CHUNK_OVERLAP, chunk_chars - 1)
    while start < len(text):
        end = min(len(text), start + chunk_chars)
        if end < len(text):
            boundary = text.rfind("\n", start + chunk_chars // 2, end)
            if boundary > start:
                end = boundary
        chunks.append((text[start:end], start, end))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _evidence_chunks(text: str) -> list[str]:
    """Compatibility wrapper used by existing callers and tests."""
    return [chunk for chunk, _start, _end in _evidence_windows(text)]


async def schedule_file_memory_extraction(
    *,
    user_id: str,
    meeting_id: int,
    file_id: int,
    file_name: str,
    text: str,
) -> int:
    """Enqueue idempotent extraction jobs for all bounded source windows."""
    if not settings.MEMORY_AUTO_EXTRACT:
        return 0
    windows = _evidence_windows(text)
    with db.get_connection() as conn:
        file_record = db.get_meeting_file(conn, file_id, user_id=user_id)
        meeting_record = db.get_meeting(conn, meeting_id, user_id=user_id)
    if file_memory_policy(file_record or {}, file_name) != "project_state":
        logger.info(
            "Keeping reference document in RAG without long-term extraction: file=%s", file_id
        )
        return 0
    source_revision = str(
        (file_record or {}).get("content_hash")
        or (file_record or {}).get("updated_at")
        or hashlib.sha256(text.encode()).hexdigest()
    )
    from ...core.source_revision_fence import meeting_file_source_token

    source_file_revision = meeting_file_source_token(file_record or {})
    file_updated_at = file_record.get("updated_at") if file_record else None
    source_event_time = meeting_record.get("meeting_date") if meeting_record else None
    for index, (chunk, start, end) in enumerate(windows, start=1):
        digest = hashlib.sha256(chunk.encode()).hexdigest()[:24]
        await enqueue_durable_job(
            kind="fact_extraction",
            dedupe_key=f"meeting:{meeting_id}:file:{file_id}:part:{index}:{digest}",
            payload={
                "user_id": user_id,
                "question": f"Meeting evidence from {file_name}, part {index} of {len(windows)}.",
                "answer": chunk,
                "session_id": None,
                "meeting_ids": [meeting_id],
                "file_ids": [file_id],
                "evidence_message_ids": [],
                "evidence_text": chunk,
                "memory_mode": "balanced",
                "source_revision": source_revision,
                "source_window_hash": digest,
                "source_window_count": len(windows),
                "source_window_start": start,
                "source_window_end": end,
                "source_file_updated_at": file_updated_at,
                "source_file_revision": source_file_revision,
                "source_event_time": source_event_time,
            },
            max_attempts=3,
        )
        MEMORY_INGEST_WINDOWS_TOTAL.inc()
        if index % settings.MEMORY_INGEST_MAX_CHUNKS_PER_FILE == 0:
            # The legacy setting now bounds one scheduling burst, not source
            # coverage. Yielding keeps very large uploads responsive while all
            # remaining windows are still durably enqueued.
            await asyncio.sleep(0)
    return len(windows)
