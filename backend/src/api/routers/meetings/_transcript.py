import asyncio

from fastapi import Depends, HTTPException, Query

from ....core import database as db
from ....core.security import verify_api_key
from ....models.schemas import TranscriptResponse
from ....models.schemas.meetings import TranscriptFileItem
from ._common import _ownership_filter, router


@router.get("/{meeting_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    meeting_id: int,
    format: str = Query("plain", enum=["plain", "markdown"]),
    principal: dict = Depends(verify_api_key),
):
    """Get the full transcript of a meeting"""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")

            # Get combined transcripts from all files
            transcript = db.get_meeting_transcripts(conn, meeting_id, user_id=ownership)
            file_rows = db.list_meeting_files(conn, meeting_id, user_id=ownership)
            files = [
                TranscriptFileItem(
                    file_id=f["id"],
                    file_name=f["file_name"],
                    file_type=f["file_type"],
                    content=f["transcript"] or "",
                )
                for f in file_rows
                if f.get("status") == "ready" and (f.get("transcript") or "").strip()
            ]
            return m, transcript, files

    m, transcript, files = await asyncio.to_thread(_fetch)
    if not transcript:
        raise HTTPException(400, "Meeting has no transcript")

    # Format as markdown if requested
    if format == "markdown":
        md_header = f"# {m['title']}\n\n**Date:** {m.get('meeting_date', 'N/A')}\n\n---\n\n"
        transcript = md_header + transcript

    return TranscriptResponse(
        meeting_id=meeting_id,
        transcript=transcript,
        format=format,
        files=files,
    )
