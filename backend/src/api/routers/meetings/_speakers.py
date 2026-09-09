"""Speaker identification endpoints — list, rename, and play sample audio."""

import asyncio
import json
import logging
from collections import OrderedDict

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ....api.middleware import limiter
from ....api.routers.file_download import _verify_file_access
from ....core import database as db
from ....core.audit import audit_log
from ....core.config import settings
from ....core.security import _derive_user_id_from_api_key, is_dev_user, verify_api_key
from ....models.schemas import (
    SpeakerInfo,
    SpeakersResponse,
    TranscriptSegment,
    UpdateSpeakersRequest,
    UpdateSpeakersResponse,
)
from ....services.asr._audio_clip import extract_audio_clip
from ....services.files._paths import resolve_upload_path
from ._common import _ownership_filter, router

media_router = APIRouter(prefix="/meetings", tags=["meetings"])

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_meeting_date_int(date_str: str) -> int:
    """Parse 'YYYY-MM-DD' to int YYYYMMDD, returning 0 on invalid input."""
    if not date_str:
        return 0
    try:
        from datetime import date

        d = date.fromisoformat(date_str)
        return d.year * 10000 + d.month * 100 + d.day
    except (ValueError, TypeError):
        return 0


def _format_timestamp(ms: int) -> str:
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _resolve_file(meeting_id: int, file_id: int, *, user_id: str | None = None) -> dict:
    """Fetch and validate meeting + file record. Returns file dict."""
    with db.get_connection() as conn:
        m = db.get_meeting(conn, meeting_id, user_id=user_id)
        if not m:
            raise HTTPException(404, "Meeting not found")
        f = db.get_meeting_file(conn, file_id, user_id=user_id)
        if not f or f["meeting_id"] != meeting_id:
            raise HTTPException(404, "File not found")
        if f["file_type"] not in ("video", "audio"):
            raise HTTPException(
                400, "Speaker identification is only available for audio/video files"
            )
        if f["status"] not in ("ready", "summarizing"):
            raise HTTPException(400, "File is not ready")
    return f


async def _load_segments(file_record: dict) -> list[dict]:
    """Load segments from DB cache, or re-transcribe via async path."""
    file_id = file_record["id"]

    def _read_cached() -> str | None:
        with db.get_connection() as conn:
            return db.get_segments_json(conn, file_id)

    cached = await asyncio.to_thread(_read_cached)
    if cached:
        return json.loads(cached)

    # Fallback: re-transcribe (expensive, for legacy data)
    try:
        file_path = await asyncio.to_thread(
            resolve_upload_path,
            file_record["file_path"],
            expected_hash=file_record.get("content_hash"),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Source file not found") from exc

    from ....services.transcriber import transcribe_with_timestamps

    segments = await transcribe_with_timestamps(file_path, provider=settings.ASR_PROVIDER)

    # Cache for future use
    await asyncio.to_thread(_cache_segments_sync, file_id, segments)
    return segments


def _cache_segments_sync(file_id: int, segments: list[dict]) -> None:
    with db.get_write_connection() as conn:
        db.save_segments_json(conn, file_id, json.dumps(segments))


def _apply_speaker_names(segments: list[dict], name_map: dict[str, str]) -> list[dict]:
    """Return new segment list with speaker codes replaced by names."""
    return [
        {**s, "speaker": name_map.get(s.get("speaker", ""), s.get("speaker"))} for s in segments
    ]


def _build_transcript_text(segments: list[dict]) -> str:
    """Build human-readable transcript from segments with timestamps."""
    lines = []
    for seg in segments:
        speaker = seg.get("speaker")
        text_part = seg.get("text", "").strip()
        if not text_part:
            continue
        start_ms = int(seg.get("start", 0) * 1000)
        timestamp = _format_timestamp(start_ms)
        if speaker:
            lines.append(f"[{timestamp}] {speaker}: {text_part}")
        else:
            lines.append(f"[{timestamp}] {text_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{meeting_id}/files/{file_id}/speakers", response_model=SpeakersResponse)
async def get_speakers(
    meeting_id: int,
    file_id: int,
    principal: dict = Depends(verify_api_key),
):
    """List all unique speakers for a file with sample utterances."""
    user_id = _ownership_filter(principal)
    file_record = await asyncio.to_thread(_resolve_file, meeting_id, file_id, user_id=user_id)
    segments = await _load_segments(file_record)

    # Load existing mappings
    def _load_mappings():
        with db.get_connection() as conn:
            return db.list_speaker_mappings(conn, file_id)

    mappings_list = await asyncio.to_thread(_load_mappings)
    name_map = {m["speaker_code"]: m["speaker_name"] for m in mappings_list}

    # Group by speaker code
    speaker_segments: OrderedDict[str, list[dict]] = OrderedDict()
    for seg in segments:
        code = seg.get("speaker")
        if code:
            speaker_segments.setdefault(code, []).append(seg)

    speakers: list[SpeakerInfo] = []
    for code, segs in speaker_segments.items():
        # Find first non-empty utterance as sample
        sample_seg = None
        for s in segs:
            if s.get("text", "").strip():
                sample_seg = s
                break

        speakers.append(
            SpeakerInfo(
                speaker_code=code,
                speaker_name=name_map.get(code),
                utterance_count=len(segs),
                sample=TranscriptSegment(**sample_seg) if sample_seg else None,
            )
        )

    return SpeakersResponse(file_id=file_id, speakers=speakers)


@router.put("/{meeting_id}/files/{file_id}/speakers", response_model=UpdateSpeakersResponse)
@limiter.limit("3/minute")
async def update_speaker_names(
    request: Request,
    meeting_id: int,
    file_id: int,
    body: UpdateSpeakersRequest,
    principal: dict = Depends(verify_api_key),
):
    """Save speaker mappings, regenerate transcript, re-index vectors, and regenerate summary."""
    user_id = _ownership_filter(principal)
    file_record = await asyncio.to_thread(_resolve_file, meeting_id, file_id, user_id=user_id)
    task_name = f"speaker_rename:{meeting_id}:{file_id}"
    with db.get_connection() as conn:
        rename_active = db.is_job_active(
            conn,
            kind="speaker_rename",
            dedupe_key=task_name,
        )
    if rename_active:
        raise HTTPException(status_code=409, detail="Speaker rename is already in progress")
    segments = await _load_segments(file_record)

    mappings = [(m.speaker_code, m.speaker_name) for m in body.mappings]
    # QR-1: Validate speaker names against a whitelist to prevent prompt injection.
    import re as _re

    _SPEAKER_NAME_RE = _re.compile(
        r"^[A-Za-z0-9一-鿿぀-ゟ゠-ヿ가-힯\s\-'.]{1,80}$"  # noqa: RUF001
    )
    for _code, _name in mappings:
        if not _SPEAKER_NAME_RE.match(_name):
            raise HTTPException(
                400,
                f"Invalid speaker name: {_name!r}. "
                "Use letters, numbers, spaces, hyphens, apostrophes.",
            )
    name_map = dict(mappings)

    # 1. Apply name substitution to segments
    updated_segments = _apply_speaker_names(segments, name_map)

    # 2. Regenerate transcript text
    new_transcript = _build_transcript_text(updated_segments)

    # 3. Atomically persist mappings, transcript state, and durable work.
    #    the HTTP response so the saved transcript is correct).
    #    Set status to "summarizing" immediately so the client sees
    #    "rebuilding" state instead of a transient "ready" that would
    #    flip back to "summarizing" when the background task runs.
    def _update_db_transcript():
        from ....services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        with db.get_write_connection() as conn:
            # Repeat the optimistic pre-check under the write transaction so
            # two concurrent rename requests cannot mutate state behind an
            # already-running post-processing job.
            if db.is_job_active(conn, kind="speaker_rename", dedupe_key=task_name):
                raise HTTPException(status_code=409, detail="Speaker rename is already in progress")
            db.bulk_upsert_speaker_mappings(conn, file_id, meeting_id, mappings)
            # Update file transcript + mark as summarizing (H-11: avoid
            # exposing a transient "ready" state before reindex/summary).
            db.update_meeting_file_status(conn, file_id, "summarizing", transcript=new_transcript)
            # Persist renamed segments so downstream consumers (per-file
            # summary speaker timeline, future re-renders) see the new
            # names instead of mixing old codes with the renamed transcript.
            db.save_segments_json(conn, file_id, json.dumps(updated_segments))
            conn.execute(
                "UPDATE meeting_files SET summary_status='pending', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (file_id,),
            )
            # Recompute meeting status so it reflects the file's new
            # 'summarizing' state. Without this, a previously-Ready meeting
            # keeps showing "Ready" while one of its files is summarizing.
            _update_meeting_status_from_files(conn, meeting_id)
            # Update meeting-level combined transcript
            combined = db.get_meeting_transcripts(conn, meeting_id)
            conn.execute(
                "UPDATE meetings SET transcript=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (combined, meeting_id),
            )
            db.enqueue_job(
                conn,
                kind="speaker_rename",
                dedupe_key=task_name,
                payload={"meeting_id": meeting_id, "file_id": file_id, "user_id": user_id},
                max_attempts=3,
            )

    await asyncio.to_thread(_update_db_transcript)
    from ....services.jobs import wake_durable_job_workers

    wake_durable_job_workers()

    # Invalidate query-rewrite cache so that stale entries keyed on old speaker
    # names are not reused after the rename (H-16).
    from ....services.rag._query import _clear_rewrite_cache

    _clear_rewrite_cache()

    # 4. Build response
    speaker_info: list[SpeakerInfo] = []
    seen_codes: set[str] = set()
    for seg in segments:
        code = seg.get("speaker")
        if code and code not in seen_codes:
            seen_codes.add(code)
            count = sum(1 for s in segments if s.get("speaker") == code)
            sample_seg = next(
                (s for s in segments if s.get("speaker") == code and s.get("text", "").strip()),
                None,
            )
            speaker_info.append(
                SpeakerInfo(
                    speaker_code=code,
                    speaker_name=name_map.get(code),
                    utterance_count=count,
                    sample=TranscriptSegment(**sample_seg) if sample_seg else None,
                )
            )

    audit_log(
        "update",
        "meeting_speakers",
        f"{meeting_id}/{file_id}",
        detail=f"renamed {len(mappings)} speaker(s)",
    )

    return UpdateSpeakersResponse(
        file_id=file_id,
        mappings=speaker_info,
        message=f"Updated {len(mappings)} speaker name(s)",
    )


@media_router.get("/{meeting_id}/files/{file_id}/speakers/{speaker_code}/audio")
async def get_speaker_audio(
    meeting_id: int,
    file_id: int,
    speaker_code: str,
    token: str | None = Query(None, description="Short-lived file download token"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Stream a short audio clip of a speaker's first utterance.

    Authentication: Either X-API-Key header or ?token= query parameter.
    """
    configured_key = settings.API_KEY.get_secret_value()
    # Token-only media requests have no custom header.  The deployment has a
    # single configured proxy principal, so validate scoped tokens against it.
    user_id = _derive_user_id_from_api_key(configured_key) if configured_key else "default"

    _verify_file_access(x_api_key, token, meeting_id=meeting_id, file_id=file_id, user_id=user_id)
    ownership_filter = user_id if not is_dev_user(user_id) else None
    file_record = await asyncio.to_thread(
        _resolve_file, meeting_id, file_id, user_id=ownership_filter
    )
    segments = await _load_segments(file_record)

    # Find first segment for this speaker
    sample = None
    for seg in segments:
        if seg.get("speaker") == speaker_code and seg.get("text", "").strip():
            sample = seg
            break
    if not sample:
        raise HTTPException(404, "No utterance found for the specified speaker")

    try:
        source_path = await asyncio.to_thread(
            resolve_upload_path,
            file_record["file_path"],
            expected_hash=file_record.get("content_hash"),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Source file not found") from exc

    # Extract audio clip
    clip_path = await asyncio.to_thread(
        extract_audio_clip,
        source_path,
        sample["start"],
        sample["end"],
    )

    return FileResponse(
        path=str(clip_path),
        media_type="audio/wav",
        filename=f"speaker_{speaker_code}.wav",
        background=BackgroundTask(clip_path.unlink, missing_ok=True),
    )
