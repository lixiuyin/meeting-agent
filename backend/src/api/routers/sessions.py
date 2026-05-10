"""Session management API - list, get messages, delete, summarize, search sessions"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...api.dependencies import decode_cursor, encode_cursor
from ...api.middleware import limiter
from ...core import database as db
from ...core.audit import audit_log
from ...core.database import get_write_connection
from ...core.security import is_dev_user, verify_api_key
from ...models.schemas import (
    MessageResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionMessageResponse,
    SessionResponse,
    SessionSearchRequest,
    SessionSearchResponse,
    SessionSearchResult,
    SessionSummaryListResponse,
    SessionSummaryResponse,
)
from ...services.memory import invalidate_session, session_summary_service

logger = logging.getLogger(__name__)


def _safe_json_loads(raw: str | None, default: list | None = None) -> list:
    if not raw:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON in DB field, returning default")
        return default if default is not None else []


router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    principal: dict = Depends(verify_api_key),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0),
):
    """List conversation sessions for a user"""
    resolved_offset = decode_cursor(cursor) if cursor else (offset or 0)

    def _fetch():
        with db.get_connection() as conn:
            sessions = db.list_sessions(
                conn, user_id=principal["user_id"], limit=limit + 1, offset=resolved_offset
            )
            total = db.count_sessions(conn, user_id=principal["user_id"])
            return sessions, total

    sessions, total = await asyncio.to_thread(_fetch)
    has_next = len(sessions) > limit
    page = sessions[:limit]
    next_cursor = encode_cursor(resolved_offset + limit) if has_next else None
    items = [SessionResponse(**s) for s in page]

    return SessionListResponse(
        items=items,
        next_cursor=next_cursor,
        total=total,
        sessions=items,
    )


@router.get("/{session_id}/messages", response_model=SessionDetailResponse)
async def get_session_messages(
    session_id: str,
    principal: dict = Depends(verify_api_key),
):
    """Get messages for a specific session"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _fetch():
        with db.get_connection() as conn:
            session = db.get_session(conn, session_id, user_id=ownership)
            if not session:
                raise HTTPException(404, "Session not found")
            messages = db.get_messages(conn, session_id)
            total = db.count_messages(conn, session_id)
            return session, messages, total

    session, messages, total = await asyncio.to_thread(_fetch)

    # Parse sources_json into structured source objects
    parsed_messages = []
    for m in messages:
        sources_raw = m.get("sources_json")
        sources_list = _safe_json_loads(sources_raw)
        parsed_messages.append(
            SessionMessageResponse(role=m["role"], content=m["content"], sources=sources_list)
        )

    return SessionDetailResponse(
        session=SessionResponse(**session),
        messages=parsed_messages,
        total=total,
    )


@router.delete("/{session_id}", response_model=MessageResponse)
@limiter.limit("10/minute")
async def delete_session(
    request: Request,
    session_id: str,
    principal: dict = Depends(verify_api_key),
):
    """Delete a conversation session and its messages"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _delete():
        with get_write_connection() as conn:
            session = db.get_session(conn, session_id, user_id=ownership)
            if not session:
                raise HTTPException(404, "Session not found")
            db.delete_session(conn, session_id, user_id=ownership)
            return session

    session = await asyncio.to_thread(_delete)
    # Invalidate in-memory cache so stale data is not served
    invalidate_session(session_id)
    audit_log("delete", "session", session_id, user_id=session.get("user_id", "unknown"))
    return MessageResponse(message="Session deleted")


@router.post("/{session_id}/summarize", response_model=SessionSummaryResponse)
async def summarize_session(
    session_id: str,
    principal: dict = Depends(verify_api_key),
):
    """Generate or retrieve a summary for a session"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _fetch():
        with db.get_connection() as conn:
            return db.get_session(conn, session_id, user_id=ownership)

    session = await asyncio.to_thread(_fetch)
    if not session:
        raise HTTPException(404, "Session not found")

    result = await session_summary_service.summarize_session(
        session_id, session.get("user_id", "default")
    )
    if not result:
        raise HTTPException(422, "Session has too few messages or summarization failed")
    return SessionSummaryResponse(**result)


@router.get("/{session_id}/summary", response_model=SessionSummaryResponse)
async def get_session_summary(
    session_id: str,
    principal: dict = Depends(verify_api_key),
):
    """Get the existing summary for a session"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _fetch():
        with db.get_connection() as conn:
            return db.get_session_summary(conn, session_id, user_id=ownership)

    summary = await asyncio.to_thread(_fetch)
    if not summary:
        raise HTTPException(404, "No summary found for this session")

    return SessionSummaryResponse(
        session_id=summary["session_id"],
        summary=summary["summary"],
        topics=_safe_json_loads(summary.get("topics")),
        key_entities=_safe_json_loads(summary.get("key_entities")),
        decisions=_safe_json_loads(summary.get("decisions")),
        turn_count=summary.get("turn_count", 0),
        created_at=summary.get("created_at"),
    )


@router.get("/{session_id}/cite", response_model=SessionSummaryResponse)
async def cite_session(
    session_id: str,
    principal: dict = Depends(verify_api_key),
):
    """Citation endpoint: return the full summary, topics, key entities, and decisions.

    Used by the frontend to resolve past-session citations from chat responses.
    """
    return await get_session_summary(session_id, principal=principal)


@router.get("/summaries", response_model=SessionSummaryListResponse)
async def list_summaries(
    principal: dict = Depends(verify_api_key),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0),
):
    """List session summaries for a user, most recent first."""
    resolved_offset = decode_cursor(cursor) if cursor else (offset or 0)

    def _fetch():
        with db.get_connection() as conn:
            summaries = db.list_session_summaries(
                conn, user_id=principal["user_id"], limit=limit + 1, offset=resolved_offset
            )
            total = db.count_session_summaries(conn, user_id=principal["user_id"])
            return summaries, total

    summaries, total = await asyncio.to_thread(_fetch)
    has_next = len(summaries) > limit
    page = summaries[:limit]
    next_cursor = encode_cursor(resolved_offset + limit) if has_next else None
    items = [
        SessionSummaryResponse(
            session_id=s["session_id"],
            summary=s["summary"],
            topics=_safe_json_loads(s.get("topics")),
            key_entities=_safe_json_loads(s.get("key_entities")),
            decisions=_safe_json_loads(s.get("decisions")),
            turn_count=s.get("turn_count", 0),
            session_title=s.get("session_title"),
            created_at=s.get("created_at"),
        )
        for s in page
    ]

    return SessionSummaryListResponse(
        items=items,
        next_cursor=next_cursor,
        total=total,
        summaries=items,
    )


@router.post("/search", response_model=SessionSearchResponse)
async def search_sessions(body: SessionSearchRequest, principal: dict = Depends(verify_api_key)):
    """Search across all past conversation sessions"""
    results = await session_summary_service.search_past_conversations(
        user_id=principal["user_id"],
        query=body.query,
        limit=body.limit,
    )
    return SessionSearchResponse(
        results=[SessionSearchResult(**r) for r in results],
        total=len(results),
    )
