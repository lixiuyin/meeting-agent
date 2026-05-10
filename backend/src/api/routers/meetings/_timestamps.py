import asyncio
import json
from pathlib import Path

from fastapi import Depends, HTTPException

from ....core import database as db
from ....core.config import settings
from ....core.security import verify_api_key
from ....models.schemas import MeetingStatus, TranscriptSegment, TranscriptWithTimestampsResponse
from ._common import _ownership_filter, logger, router


def _apply_speaker_mappings(file_id: int, segments: list[dict]) -> list[dict]:
    """Replace diarization speaker codes (e.g. A/B) with user-defined names."""
    with db.get_connection() as conn:
        mappings = db.list_speaker_mappings(conn, file_id)
    if not mappings:
        return segments
    name_map = {m["speaker_code"]: m["speaker_name"] for m in mappings if m.get("speaker_name")}
    if not name_map:
        return segments
    return [
        {**seg, "speaker": name_map.get(seg.get("speaker"), seg.get("speaker"))} for seg in segments
    ]


@router.get("/{meeting_id}/transcript/timestamps", response_model=TranscriptWithTimestampsResponse)
async def get_transcript_with_timestamps(
    meeting_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Get meeting transcript with timestamps for each segment.

    For video/audio files, uses cached segments from DB when available.
    Falls back to re-transcription for legacy data without cached segments.
    For other files, returns the parsed content as a single segment.
    """
    ownership = _ownership_filter(principal)

    def _fetch_meeting():
        with db.get_connection() as conn:
            return db.get_meeting(conn, meeting_id, user_id=ownership)

    m = await asyncio.to_thread(_fetch_meeting)
    if not m:
        raise HTTPException(404, "Meeting not found")

    if m["status"] != MeetingStatus.READY:
        raise HTTPException(400, "Meeting is not ready")

    file_type = m.get("file_type")

    # Resolve file info: prefer meeting_files table, fall back to legacy meetings columns
    file_id: int | None = None
    raw_path = m.get("file_path")
    if not raw_path:

        def _fetch_files():
            with db.get_connection() as conn:
                return db.list_meeting_files(conn, meeting_id, user_id=ownership)

        files = await asyncio.to_thread(_fetch_files)
        if files:
            file_id = files[0]["id"]
            raw_path = files[0]["file_path"]
            file_type = file_type or files[0].get("file_type")
    if not raw_path:
        raise HTTPException(400, "No file associated with this meeting")
    file_path = Path(raw_path)

    # For video/audio, use cached segments or re-transcribe
    if file_type in ("video", "audio"):
        # Try cached segments first
        if file_id is not None:

            def _fetch_segments():
                with db.get_connection() as conn:
                    return db.get_segments_json(conn, file_id)

            cached = await asyncio.to_thread(_fetch_segments)
            if cached:
                segments = json.loads(cached)
                segments = await asyncio.to_thread(_apply_speaker_mappings, file_id, segments)
                total_duration = segments[-1]["end"] if segments else None
                speakers = {s.get("speaker") for s in segments if s.get("speaker")}
                speaker_count = len(speakers) if speakers else None

                return TranscriptWithTimestampsResponse(
                    meeting_id=meeting_id,
                    segments=[TranscriptSegment(**s) for s in segments],
                    total_duration=total_duration,
                    speaker_count=speaker_count,
                )

        # Fallback: re-transcribe (legacy data without cached segments)
        if not file_path.exists():
            raise HTTPException(404, "Source file not found")

        try:
            from ....services.transcriber import transcribe_with_timestamps

            segments = await transcribe_with_timestamps(file_path, provider=settings.ASR_PROVIDER)
            if file_id is not None:
                segments = await asyncio.to_thread(_apply_speaker_mappings, file_id, segments)
            total_duration = segments[-1]["end"] if segments else None
            speakers = {s.get("speaker") for s in segments if s.get("speaker")}
            speaker_count = len(speakers) if speakers else None

            return TranscriptWithTimestampsResponse(
                meeting_id=meeting_id,
                segments=[TranscriptSegment(**s) for s in segments],
                total_duration=total_duration,
                speaker_count=speaker_count,
            )
        except Exception as e:
            logger.error("Failed to generate timestamped transcript: %s", e, exc_info=True)
            raise HTTPException(500, "Failed to generate timestamped transcript") from e
    else:
        # Non-AV files should use the per-file timeline endpoint instead.
        # Return 404 so the frontend can distinguish this from a valid
        # (but empty) AV transcript.
        raise HTTPException(404, "no_av_file")
