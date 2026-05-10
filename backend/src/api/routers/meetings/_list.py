import asyncio
import logging

from fastapi import Depends, Query

from ....core import database as db
from ....core.security import verify_api_key
from ....models.schemas import MeetingListResponse, MeetingStatus
from ._common import _build_meeting_response, _ownership_filter, router

logger = logging.getLogger(__name__)


@router.get("", response_model=MeetingListResponse)
async def list_meetings(
    status: MeetingStatus = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    principal: dict = Depends(verify_api_key),
):
    """List all meetings"""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            meetings = db.list_meetings(
                conn,
                status=status.value if status else None,
                limit=limit,
                offset=offset,
                user_id=ownership,
            )
            total = db.count_meetings(
                conn,
                status=status.value if status else None,
                user_id=ownership,
            )
            return meetings, total

    meetings, total = await asyncio.to_thread(_fetch)

    # Bulk-fetch distinct file_types per meeting to surface mixed-modality
    # meetings on the overview without an N+1 query pattern.
    def _fetch_file_types() -> dict[int, list[str]]:
        with db.get_connection() as conn:
            return db.list_distinct_file_types_bulk(conn, [m["id"] for m in meetings])

    file_types_map = await asyncio.to_thread(_fetch_file_types) if meetings else {}

    results = []
    for m in meetings:
        try:
            results.append(_build_meeting_response(m, file_types=file_types_map.get(m["id"], [])))
        except Exception:
            logger.warning("Skipping meeting %d: serialization error", m.get("id"), exc_info=True)

    return MeetingListResponse(
        total=total,
        meetings=results,
    )
