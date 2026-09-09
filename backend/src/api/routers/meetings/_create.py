import asyncio

from fastapi import Depends, Request

from ....api.dependencies import IdempotencyGuard, idempotency_key_header
from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from ....core.security import verify_api_key
from ....models.schemas import CreateMeetingRequest, CreateMeetingResponse
from ._common import router


@router.post("", response_model=CreateMeetingResponse)
async def create_meeting(
    request: Request,
    body: CreateMeetingRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Create a new empty meeting (without files).

    Use this to create a meeting first, then upload files to it.
    """
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return CreateMeetingResponse(**cached)

    def _create():
        with get_write_connection() as conn:
            meeting_id = db.create_meeting(
                conn,
                title=body.title,
                description=body.description,
                meeting_date=body.meeting_date.isoformat() if body.meeting_date else None,
                user_id=principal["user_id"],
            )
            response = CreateMeetingResponse(
                meeting_id=meeting_id,
                message="Meeting created successfully",
            )
            guard.save_in_transaction(conn, response.model_dump(mode="json"))
            return response

    response = await asyncio.to_thread(_create)
    await guard.finish_transaction()

    audit_log("create", "meeting", response.meeting_id, detail=f"title={body.title}")
    return response
