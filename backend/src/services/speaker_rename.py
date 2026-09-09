"""Restart-safe post-processing for persisted speaker-name changes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from ..core import database as db
from ..core.config import settings
from ..core.index_manifest import index_config_fingerprint

logger = logging.getLogger(__name__)


def _meeting_date_int(value: str) -> int:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return 0
    return parsed.year * 10000 + parsed.month * 100 + parsed.day


def _load_state(
    meeting_id: int, file_id: int, user_id: str | None
) -> tuple[dict, dict, list[dict]]:
    with db.get_connection() as conn:
        meeting = db.get_meeting(conn, meeting_id, user_id=user_id)
        file_record = db.get_meeting_file(conn, file_id, user_id=user_id)
        raw_segments = db.get_segments_json(conn, file_id)
    if meeting is None or file_record is None or file_record["meeting_id"] != meeting_id:
        raise RuntimeError("Speaker rename target no longer exists")
    if not raw_segments:
        raise RuntimeError("Speaker rename target has no persisted segments")
    segments = json.loads(raw_segments)
    if not isinstance(segments, list):
        raise RuntimeError("Speaker rename segments are malformed")
    return meeting, file_record, segments


def _base_metadata(meeting: dict, file_record: dict, file_id: int) -> dict[str, Any]:
    return {
        "title": file_record.get("file_name", ""),
        "file_type": file_record["file_type"],
        "file_id": file_id,
        "file_name": file_record.get("file_name", ""),
        "meeting_date": _meeting_date_int(meeting.get("meeting_date", "")),
        "user_id": meeting.get("user_id") or file_record.get("user_id") or "default",
        "index_config_fingerprint": index_config_fingerprint(),
        "chunk_strategy_route": "native",
    }


def _replace_native_index(
    meeting_id: int,
    file_id: int,
    metadata: dict[str, Any],
    segments: list[dict],
) -> None:
    """Replace only this file's generation, preserving the live one on failure."""
    from .rag import index_meeting_segments
    from .rag._indexer_store import (
        atomic_file_index_replacement,
        inspect_native_index_generation,
    )

    with db.get_write_connection() as conn:
        db.mark_native_index_building(conn, file_id=file_id, meeting_id=meeting_id)
    try:
        with atomic_file_index_replacement(meeting_id, file_id) as generation:
            generation_metadata = {**metadata, "index_generation": generation}
            index_meeting_segments(
                meeting_id=meeting_id,
                segments=segments,
                metadata=generation_metadata,
                strict_bm25=True,
            )
        manifest = inspect_native_index_generation(
            meeting_id,
            file_id,
            generation,
            str(metadata["index_config_fingerprint"]),
        )
    except Exception as exc:
        with db.get_write_connection() as conn:
            db.mark_native_index_failed(
                conn,
                file_id=file_id,
                meeting_id=meeting_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        raise
    with db.get_write_connection() as conn:
        db.mark_native_index_ready(
            conn,
            file_id=file_id,
            meeting_id=meeting_id,
            indexed_at=datetime.now(UTC).isoformat(),
            generation=manifest.generation,
            config_fingerprint=manifest.config_fingerprint,
            chroma_chunk_count=manifest.chroma_chunk_count,
            bm25_chunk_count=manifest.bm25_chunk_count,
            manifest_checksum=manifest.checksum,
        )


def _reindex_raganything(
    meeting_id: int,
    file_id: int,
    file_record: dict,
    transcript: str,
) -> None:
    """Keep the optional multimodal index consistent with the native index."""
    if not settings.RAGANYTHING_ENABLED:
        return
    from .rag._raganything import index_with_raganything

    index_with_raganything(
        meeting_id=meeting_id,
        file_id=file_id,
        text=transcript,
        file_path=str(file_record.get("file_path", "")),
        metadata={
            "title": file_record.get("file_name", ""),
            "file_type": file_record["file_type"],
            "user_id": file_record.get("user_id", "default"),
        },
    )


async def _queue_regenerated_summaries(file_id: int, meeting_id: int) -> None:
    from .chain._meeting_summary_lifecycle import invalidate_meeting_summary
    from .processor._pipeline_common import _update_meeting_status_from_files
    from .rag._summary_vectorstore import delete_file_summary
    from .summaries import enqueue_file_summary

    await asyncio.to_thread(db.update_file_summary_status, file_id, "pending")
    await asyncio.to_thread(delete_file_summary, file_id)

    def _reset() -> None:
        with db.get_write_connection() as conn:
            db.update_meeting_file_status(conn, file_id, "summarizing")
            _update_meeting_status_from_files(conn, meeting_id)

    await asyncio.to_thread(_reset)
    await asyncio.to_thread(invalidate_meeting_summary, meeting_id, force=True)
    await enqueue_file_summary(file_id, meeting_id)


async def run_speaker_rename_job(payload: dict[str, Any]) -> None:
    meeting_id = int(payload["meeting_id"])
    file_id = int(payload["file_id"])
    user_id = str(payload["user_id"]) if payload.get("user_id") else None
    meeting, file_record, segments = await asyncio.to_thread(
        _load_state, meeting_id, file_id, user_id
    )
    metadata = _base_metadata(meeting, file_record, file_id)
    await asyncio.to_thread(_replace_native_index, meeting_id, file_id, metadata, segments)
    await asyncio.to_thread(
        _reindex_raganything,
        meeting_id,
        file_id,
        file_record,
        str(file_record.get("transcript", "")),
    )
    await _queue_regenerated_summaries(file_id, meeting_id)
