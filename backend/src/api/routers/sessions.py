"""Session management API - list, get messages, delete, summarize, search sessions"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...api.dependencies import MAX_PAGE_OFFSET, decode_cursor, encode_cursor
from ...api.middleware import limiter
from ...core import database as db
from ...core.audit import audit_log
from ...core.database import get_write_connection
from ...core.security import is_dev_user, verify_api_key
from ...models.schemas import (
    MessageResponse,
    SessionBatchDeleteRequest,
    SessionBatchDeleteResponse,
    SessionBranchRequest,
    SessionBranchResponse,
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
from ...models.schemas.continuation import ContinuationPreviewResponse
from ...services.memory import invalidate_session, session_summary_service

logger = logging.getLogger(__name__)


def _safe_json_loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON in DB field, returning default")
        return default if default is not None else []


router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(verify_api_key)])


def _message_response(message: dict) -> SessionMessageResponse:
    return SessionMessageResponse(
        id=message["id"],
        role=message["role"],
        content=message["content"],
        sources=_safe_json_loads(message.get("sources_json")),
        degraded=bool(message.get("degradation_reason")),
        degradation_reason=message.get("degradation_reason"),
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    principal: dict = Depends(verify_api_key),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0, le=MAX_PAGE_OFFSET),
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


@router.post("/batch-delete", response_model=SessionBatchDeleteResponse)
@limiter.limit("10/minute")
async def batch_delete_sessions(
    request: Request,
    body: SessionBatchDeleteRequest,
    principal: dict = Depends(verify_api_key),
):
    """Delete up to 100 sessions and durably enqueue summary-vector cleanup."""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    from ...services.chain._steps_generate import cancel_fact_extraction

    await asyncio.gather(*(cancel_fact_extraction(session_id) for session_id in body.session_ids))

    def _delete_batch():
        deleted_sessions: list[dict] = []
        missing: list[str] = []
        with get_write_connection() as conn:
            for session_id in body.session_ids:
                session = db.get_session(conn, session_id, user_id=ownership)
                if not session:
                    missing.append(session_id)
                    continue
                summary = db.get_session_summary(conn, session_id, user_id=ownership)
                if summary and summary.get("embedding_id"):
                    conn.execute(
                        "INSERT OR IGNORE INTO pending_vector_deletions "
                        "(collection, embedding_id) VALUES ('session_summary', ?)",
                        (summary["embedding_id"],),
                    )
                if body.retract_derived_memories:
                    db.retract_memories_from_session(
                        conn,
                        user_id=str(session["user_id"]),
                        session_id=session_id,
                    )
                db.delete_session(conn, session_id, user_id=ownership)
                deleted_sessions.append(session)
        return deleted_sessions, missing

    deleted_sessions, missing = await asyncio.to_thread(_delete_batch)

    from ...services.memory._service._crud import cleanup_pending_vector_deletions

    try:
        await asyncio.to_thread(
            cleanup_pending_vector_deletions,
            collections={"session_summary"},
        )
    except Exception:
        logger.warning(
            "Immediate batch session-vector cleanup failed; durable jobs remain queued",
            exc_info=True,
        )

    for session in deleted_sessions:
        session_id = session["id"]
        invalidate_session(session_id)
        audit_log("delete", "session", session_id, user_id=session.get("user_id", "unknown"))
    return SessionBatchDeleteResponse(deleted=len(deleted_sessions), missing=missing)


@router.get("/{session_id}/continuation-preview", response_model=ContinuationPreviewResponse)
async def continuation_preview(session_id: str, principal: dict = Depends(verify_api_key)):
    from ...services.continuation_preview import build_continuation_preview

    def read():
        with db.get_connection() as conn:
            session = db.get_session(conn, session_id, user_id=principal["user_id"])
            if session is None:
                raise HTTPException(404, "Session not found")
            return build_continuation_preview(conn, session, principal["user_id"])

    return await asyncio.to_thread(read)


@router.get("/{session_id}/messages", response_model=SessionDetailResponse)
async def get_session_messages(
    session_id: str,
    principal: dict = Depends(verify_api_key),
    limit: int = Query(200, ge=1, le=500),
    before_id: int | None = Query(None, ge=1),
):
    """Get messages for a specific session"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _fetch():
        with db.get_connection() as conn:
            session = db.get_session(conn, session_id, user_id=ownership)
            if not session:
                raise HTTPException(404, "Session not found")
            messages = db.get_messages(conn, session_id, limit + 1, before_id=before_id)
            total = db.count_messages(conn, session_id)
            run = conn.execute(
                "SELECT id,question,status FROM chat_runs WHERE id=("
                "SELECT id FROM chat_runs WHERE session_id=? AND user_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1) "
                "AND status IN ('running','interrupted') AND saved_ai_id IS NULL",
                (session_id, user_id),
            ).fetchone()
            return session, messages, total, dict(run) if run else None

    session, messages, total, pending_run = await asyncio.to_thread(_fetch)

    # Parse sources_json into structured source objects
    has_older = len(messages) > limit
    page = messages[-limit:]
    parsed_messages = []
    for m in page:
        sources_raw = m.get("sources_json")
        parsed_messages.append(_message_response({**m, "sources_json": sources_raw}))

    session_config = None
    task_state = None
    try:
        parsed_config = _safe_json_loads(session.get("config_json"))
        session_config = parsed_config if isinstance(parsed_config, dict) else None
    except (TypeError, ValueError):
        session_config = None
    try:
        parsed_task_state = _safe_json_loads(session.get("task_state_json"))
        task_state = parsed_task_state if isinstance(parsed_task_state, dict) else None
    except (TypeError, ValueError):
        task_state = None

    return SessionDetailResponse(
        session=SessionResponse(**session),
        messages=parsed_messages,
        total=total,
        next_before_id=page[0]["id"] if has_older and page else None,
        pending_run=pending_run,
        session_config=session_config,
        task_state=task_state,
    )


@router.post("/{session_id}/branches", response_model=SessionBranchResponse)
async def create_session_branch(
    session_id: str,
    body: SessionBranchRequest,
    principal: dict = Depends(verify_api_key),
):
    """Create an immutable branch before a user message for edit/retry."""

    def _branch():
        with get_write_connection() as conn:
            try:
                branch_id = db.branch_session(
                    conn,
                    source_session_id=session_id,
                    user_id=principal["user_id"],
                    before_message_id=body.from_message_id,
                    reason=body.reason,
                )
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            session = db.get_session(conn, branch_id, user_id=principal["user_id"])
            total = db.count_messages(conn, branch_id)
            messages = db.get_messages(conn, branch_id, limit=200)
            assert session is not None
            return session, messages, total

    session, messages, total = await asyncio.to_thread(_branch)
    audit_log(
        "create",
        "session_branch",
        session["id"],
        user_id=principal["user_id"],
        detail=(f"parent={session_id} from_message={body.from_message_id} reason={body.reason}"),
    )
    return SessionBranchResponse(
        session=SessionResponse(**session),
        messages=[_message_response(message) for message in messages],
        total=total,
        next_before_id=messages[0]["id"] if total > len(messages) and messages else None,
    )


@router.delete("/{session_id}", response_model=MessageResponse)
@limiter.limit("10/minute")
async def delete_session(
    request: Request,
    session_id: str,
    principal: dict = Depends(verify_api_key),
    retract_derived_memories: bool = Query(
        False,
        description="Retract facts attributed to this session before deleting it",
    ),
):
    """Delete a conversation session and its messages"""
    user_id = principal.get("user_id", "default")
    ownership = user_id if not is_dev_user(user_id) else None

    def _assert_owned() -> None:
        with db.get_connection() as conn:
            if not db.get_session(conn, session_id, user_id=ownership):
                raise HTTPException(404, "Session not found")

    await asyncio.to_thread(_assert_owned)

    # A just-finished response may still be extracting durable memories and
    # entities. Stop it before removing the parent session so no background
    # work can write new session-attributed records after deletion.
    from ...services.chain._steps_generate import cancel_fact_extraction

    await cancel_fact_extraction(session_id)

    def _delete():
        with get_write_connection() as conn:
            session = db.get_session(conn, session_id, user_id=ownership)
            if not session:
                raise HTTPException(404, "Session not found")
            summary = db.get_session_summary(conn, session_id, user_id=ownership)
            if summary and summary.get("embedding_id"):
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions (collection, embedding_id) "
                    "VALUES ('session_summary', ?)",
                    (summary["embedding_id"],),
                )
            if retract_derived_memories:
                db.retract_memories_from_session(
                    conn,
                    user_id=str(session["user_id"]),
                    session_id=session_id,
                )
            db.delete_session(conn, session_id, user_id=ownership)
            return session

    session = await asyncio.to_thread(_delete)
    # Attempt the durable outbox immediately; failures remain queued and are
    # retried during lifecycle reconciliation.
    from ...services.memory._service._crud import cleanup_pending_vector_deletions

    try:
        await asyncio.to_thread(
            cleanup_pending_vector_deletions,
            collections={"session_summary"},
        )
    except Exception:
        logger.warning("Immediate session-vector cleanup failed; job remains queued", exc_info=True)
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
        session_id, session.get("user_id", "default"), force=True
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
    offset: int | None = Query(None, ge=0, le=MAX_PAGE_OFFSET),
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
