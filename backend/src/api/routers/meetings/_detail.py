import asyncio

from fastapi import Depends, HTTPException

from ....core import database as db
from ....core.security import verify_api_key
from ....models.schemas import MeetingDetailResponse, MeetingFileResponse, MeetingStatus
from ._common import _build_meeting_file_response, _ownership_filter, router


@router.get("/{meeting_id}", response_model=MeetingDetailResponse)
async def get_meeting(meeting_id: int, principal: dict = Depends(verify_api_key)):
    """Get meeting details with file list"""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")
            files = db.list_meeting_files(conn, meeting_id, user_id=ownership)
            return m, files

    m, files = await asyncio.to_thread(_fetch)

    # Look up pre-generated meeting summary if available
    meeting_summary = db.get_meeting_summary(meeting_id)

    return MeetingDetailResponse(
        id=m["id"],
        title=m["title"],
        description=m["description"],
        status=MeetingStatus(m["status"]),
        meeting_date=m["meeting_date"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        files=[_build_meeting_file_response(f) for f in files],
        summary_status=m.get("summary_status"),
        summary=meeting_summary["summary"] if meeting_summary else None,
    )


@router.get("/{meeting_id}/files", response_model=list[MeetingFileResponse])
async def get_meeting_files(meeting_id: int, principal: dict = Depends(verify_api_key)):
    """List all files for a meeting"""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")
            files = db.list_meeting_files(conn, meeting_id, user_id=ownership)
            return files

    files = await asyncio.to_thread(_fetch)
    return [_build_meeting_file_response(f) for f in files]
