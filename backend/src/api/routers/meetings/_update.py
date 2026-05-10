import asyncio

from fastapi import Depends, HTTPException, Request

from ....api.dependencies import IdempotencyGuard, idempotency_key_header
from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from ....core.security import verify_api_key
from ....models.schemas import MeetingResponse, UpdateMeetingRequest
from ._common import _build_meeting_response, _ownership_filter, router


@router.put("/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
    meeting_id: int,
    request: Request,
    body: UpdateMeetingRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Update meeting metadata (title, description, meeting_date)"""
    ownership = _ownership_filter(principal)
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MeetingResponse(**cached)

    def _update():
        with get_write_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")

            # Build update fields
            updates = {}
            if body.title is not None:
                updates["title"] = body.title
            if body.description is not None:
                updates["description"] = body.description
            if body.meeting_date is not None:
                updates["meeting_date"] = body.meeting_date.isoformat()

            if updates:
                db.update_meeting(conn, meeting_id, user_id=ownership, **updates)
                audit_log("update", "meeting", meeting_id, detail=str(list(updates.keys())))

            # Fetch updated record
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if m is None:
                raise HTTPException(404, "Meeting not found")
            return m

    m = await asyncio.to_thread(_update)

    def _fetch_types() -> list[str]:
        with db.get_connection() as conn:
            return db.list_distinct_file_types_bulk(conn, [m["id"]]).get(m["id"], [])

    file_types = await asyncio.to_thread(_fetch_types)
    response = _build_meeting_response(m, file_types=file_types)
    await guard.save(response.model_dump(mode="json"))
    return response
