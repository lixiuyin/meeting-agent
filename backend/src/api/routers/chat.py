"""Chat Q&A API - RAG-based meeting questions"""

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from ...api.middleware import limiter
from ...core.config import activate_settings_snapshot, build_retrieval_profile_snapshot
from ...core.database import get_connection
from ...core.exceptions import (
    ContinuationSnapshotError,
    LLMAuthenticationError,
    LLMCircuitBreakerError,
    LLMConfigError,
    LLMContextWindowError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTransientResponseError,
    map_error,
)
from ...core.security import is_dev_user, verify_api_key
from ...core.settings_epoch import get_settings_epoch
from ...models.errors import ApiErrorResponse, ErrorCode
from ...models.schemas import (
    ChatRequest,
    ChatResponse,
    PastSessionRef,
    SearchChunkItem,
    SearchChunksResponse,
    SessionBranchResponse,
    SessionMessageResponse,
    SessionResponse,
    SessionSourceResponse,
    SourceResponse,
    WebResultResponse,
)
from ...services.chain import ask, ask_stream
from ...services.concurrency import (
    get_session_turn_lock,
    reset_stream_semaphore,
    set_stream_semaphore,
)
from ...services.concurrency import get_stream_semaphore as _get_stream_semaphore
from ...services.stream_bus import serialize_event

__all__ = ["reset_stream_semaphore", "set_stream_semaphore"]

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)

_STREAM_QUEUE_WAIT_S = 30.0
_STREAM_CLOSE_TIMEOUT_S = 10.0


def _safe_sources(raw: str | None) -> list[SessionSourceResponse]:
    """Decode historical source data without making branch recovery fail."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    sources: list[SessionSourceResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            sources.append(SessionSourceResponse.model_validate(item))
        except ValueError:
            continue
    return sources


def _mapped_error_response(exc: Exception, operation: str) -> HTTPException:
    """Map provider/retrieval failures to one consistent HTTP error envelope."""
    if isinstance(exc, ContinuationSnapshotError):
        response = ApiErrorResponse(
            error=str(exc),
            code=ErrorCode.CONFLICT,
            detail="SNAPSHOT_UNAVAILABLE",
        )
        return HTTPException(status_code=409, detail=response.model_dump())
    mapped = map_error(exc)
    if isinstance(mapped, LLMRateLimitError):
        status, code, message = 429, ErrorCode.RATE_LIMITED, "Upstream service is rate limited"
    elif isinstance(mapped, LLMTimeoutError):
        status, code, message = 504, ErrorCode.LLM_ERROR, f"{operation} timed out"
    elif isinstance(mapped, LLMContextWindowError):
        status, code, message = 422, ErrorCode.VALIDATION_ERROR, "Request exceeds model context"
    elif isinstance(mapped, (LLMConfigError, LLMCircuitBreakerError)):
        status, code, message = 503, ErrorCode.LLM_ERROR, "Model service is unavailable"
    elif isinstance(mapped, (LLMAuthenticationError, LLMTransientResponseError)):
        status, code, message = 502, ErrorCode.LLM_ERROR, "Upstream model service failed"
    else:
        status, code, message = 500, ErrorCode.INTERNAL_ERROR, f"{operation} failed"
    response = ApiErrorResponse(error=message, code=code, detail=type(mapped).__name__)
    return HTTPException(status_code=status, detail=response.model_dump())


def _validate_scope_ownership(
    user_id: str,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    session_id: str | None = None,
) -> None:
    """Verify that all requested meeting_ids and file_ids belong to *user_id*.

    Raises ``HTTPException(403)`` on any mismatch.  Skipped in dev mode
    where no authentication is enforced.
    """
    if is_dev_user(user_id):
        return

    with get_connection() as conn:
        if session_id:
            row = conn.execute(
                "SELECT user_id FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is not None and row["user_id"] != user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: session not owned by user",
                )
        if meeting_ids:
            placeholders = ",".join("?" for _ in meeting_ids)
            rows = conn.execute(
                f"SELECT id FROM meetings WHERE id IN ({placeholders}) AND user_id=?",
                (*meeting_ids, user_id),
            ).fetchall()
            found = {row["id"] for row in rows}
            missing = set(meeting_ids) - found
            if missing:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: meeting(s) not found or not owned by user",
                )

        if file_ids:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"SELECT mf.id FROM meeting_files mf "
                f"JOIN meetings m ON mf.meeting_id = m.id "
                f"WHERE mf.id IN ({placeholders}) AND m.user_id=?",
                (*file_ids, user_id),
            ).fetchall()
            found = {row["id"] for row in rows}
            missing = set(file_ids) - found
            if missing:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: file(s) not found or not owned by user",
                )


@router.post("", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    response: Response,
    chat_request: ChatRequest,
    principal: dict = Depends(verify_api_key),
):
    """Ask a question about meeting content with RAG and memory"""
    from ...core.metrics import CHAT_MODE_REQUEST_TOTAL, CHAT_REQUEST_TOTAL

    await asyncio.to_thread(
        _validate_scope_ownership,
        principal["user_id"],
        chat_request.meeting_ids,
        chat_request.file_ids,
        chat_request.session_id,
    )
    try:
        CHAT_REQUEST_TOTAL.labels(intent="retrieval").inc()
        CHAT_MODE_REQUEST_TOTAL.labels(
            endpoint="chat",
            retrieval_profile=chat_request.retrieval_profile,
            memory_mode=chat_request.memory_mode,
        ).inc()
        session_lock = (
            get_session_turn_lock(principal["user_id"], chat_request.session_id)
            if chat_request.session_id
            else None
        )
        if session_lock is not None:
            try:
                async with asyncio.timeout(_STREAM_QUEUE_WAIT_S):
                    await session_lock.acquire()
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=409, detail="Session already has an active turn"
                ) from exc
        try:
            result = await ask(
                question=chat_request.question,
                session_id=chat_request.session_id,
                user_id=principal["user_id"],
                meeting_ids=chat_request.meeting_ids,
                file_ids=chat_request.file_ids,
                top_k=chat_request.top_k,
                use_web_search=chat_request.use_web_search,
                web_search_mode=chat_request.web_search_mode,
                web_search_results=chat_request.web_search_results,
                file_types=chat_request.file_types,
                date_from=chat_request.date_from,
                date_to=chat_request.date_to,
                valid_at=chat_request.valid_at,
                known_at=chat_request.known_at,
                rag_mode=chat_request.rag_mode,
                retrieval_profile=chat_request.retrieval_profile,
                memory_mode=chat_request.memory_mode,
                continuation_mode=chat_request.continuation_mode,
            )
        finally:
            if session_lock is not None:
                session_lock.release()
        response.headers["X-Chat-Outcome"] = "degraded" if result.degraded else "success"
        return ChatResponse(
            answer=result.answer,
            degraded=result.degraded,
            degradation_reason=result.degradation_reason,
            sources=[SourceResponse(**s) for s in (result.sources or [])],
            session_id=result.session_id,
            web_results=[WebResultResponse(**r) for r in result.web_results]
            if result.web_results
            else None,
            past_sessions=[PastSessionRef(**s) for s in result.past_sessions]
            if result.past_sessions
            else [],
            extraction_failed=result.extraction_failed,
            trace=result.trace,
            context_truncated=result.context_truncated,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Chat request failed: %s", e, exc_info=True)
        raise _mapped_error_response(e, "Chat request") from e


@router.post("/stream")
@limiter.limit("20/minute")
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    principal: dict = Depends(verify_api_key),
):
    """Streaming variant — yields answer tokens via Server-Sent Events as they arrive."""
    from ...core.metrics import CHAT_MODE_REQUEST_TOTAL

    CHAT_MODE_REQUEST_TOTAL.labels(
        endpoint="stream",
        retrieval_profile=chat_request.retrieval_profile,
        memory_mode=chat_request.memory_mode,
    ).inc()
    await asyncio.to_thread(
        _validate_scope_ownership,
        principal["user_id"],
        chat_request.meeting_ids,
        chat_request.file_ids,
        chat_request.session_id,
    )

    async def event_generator(resolved_session_id: str | None = None):
        if resolved_session_id is not None:
            chat_request.session_id = resolved_session_id
        saw_terminal = False
        session_lock = (
            get_session_turn_lock(principal["user_id"], chat_request.session_id)
            if chat_request.session_id
            else None
        )
        session_lock_acquired = False
        if session_lock is not None:
            try:
                async with asyncio.timeout(_STREAM_QUEUE_WAIT_S):
                    await session_lock.acquire()
                session_lock_acquired = True
            except TimeoutError:
                yield serialize_event(
                    {
                        "type": "error",
                        "message": "This session already has an active turn.",
                        "code": ErrorCode.SESSION_BUSY,
                    }
                )
                return
        semaphore = _get_stream_semaphore()
        # Only timeout the queue wait — stream generation itself has no hard cap.
        try:
            async with asyncio.timeout(_STREAM_QUEUE_WAIT_S):
                await semaphore.acquire()
        except TimeoutError:
            if session_lock_acquired and session_lock is not None:
                session_lock.release()
                session_lock_acquired = False
            yield serialize_event(
                {
                    "type": "error",
                    "message": "Too many concurrent streaming requests. Try again shortly.",
                    "code": ErrorCode.STREAM_QUEUE_FULL,
                }
            )
            return
        # Semaphore acquired; release unconditionally when the stream finishes.
        try:
            stream = ask_stream(
                question=chat_request.question,
                session_id=chat_request.session_id,
                user_id=principal["user_id"],
                meeting_ids=chat_request.meeting_ids,
                file_ids=chat_request.file_ids,
                top_k=chat_request.top_k,
                use_web_search=chat_request.use_web_search,
                web_search_mode=chat_request.web_search_mode,
                web_search_results=chat_request.web_search_results,
                file_types=chat_request.file_types,
                date_from=chat_request.date_from,
                date_to=chat_request.date_to,
                valid_at=chat_request.valid_at,
                known_at=chat_request.known_at,
                rag_mode=chat_request.rag_mode,
                retrieval_profile=chat_request.retrieval_profile,
                memory_mode=chat_request.memory_mode,
                continuation_mode=chat_request.continuation_mode,
            )
            try:
                async for event in stream:
                    if event.get("type") in {"done", "error"}:
                        saw_terminal = True
                    yield serialize_event(event)
            except Exception as e:
                logger.error("Stream failed: %s", e, exc_info=True)
                mapped = map_error(e)
                code = (
                    ErrorCode.RATE_LIMITED
                    if isinstance(mapped, LLMRateLimitError)
                    else ErrorCode.LLM_ERROR
                )
                yield serialize_event(
                    {
                        "type": "error",
                        "message": "Stream failed",
                        "code": code,
                    }
                )
                saw_terminal = True
            finally:
                try:
                    await asyncio.wait_for(stream.aclose(), timeout=_STREAM_CLOSE_TIMEOUT_S)
                except TimeoutError:
                    logger.warning(
                        "ask_stream aclose timed out after %ds, forcing generator close",
                        _STREAM_CLOSE_TIMEOUT_S,
                    )
                    with contextlib.suppress(StopAsyncIteration, Exception):
                        await stream.athrow(GeneratorExit)
                except Exception:
                    logger.debug("ask_stream aclose raised", exc_info=True)
                if not saw_terminal:
                    yield serialize_event(
                        {
                            "type": "error",
                            "message": "Stream ended unexpectedly",
                            "code": ErrorCode.STREAM_UNEXPECTED_END,
                        }
                    )
        finally:
            semaphore.release()
            if session_lock_acquired and session_lock is not None:
                session_lock.release()
            if not saw_terminal:
                logger.debug(
                    "Stream ended without terminal event; client may have disconnected early (M-16)"
                )

    key = request.headers.get("Idempotency-Key")
    if key:
        from ...services.chat_runs import replay_run, start_run

        run_id = await start_run(
            principal["user_id"], key, chat_request.model_dump(mode="json"), event_generator
        )
        generator = replay_run(run_id, principal["user_id"])
    else:
        run_id = None
        generator = event_generator()
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
            "Cache-Control": "no-cache",
            **({"X-Run-ID": run_id} if run_id else {}),
        },
    )


@router.get("/runs/{run_id}")
async def read_chat_run(run_id: str, principal: dict = Depends(verify_api_key)):
    from ...services.chat_runs import get_run

    return await asyncio.to_thread(get_run, run_id, principal["user_id"])


@router.get("/run-lookup")
async def lookup_chat_run(
    key: str = Query(..., min_length=1, max_length=200),
    principal: dict = Depends(verify_api_key),
):
    """Resolve a client turn key after a response-header/network failure."""
    from ...services.chat_runs import get_run, run_identity

    run_id = run_identity(principal["user_id"], key)
    return await asyncio.to_thread(get_run, run_id, principal["user_id"])


@router.post("/run-cancel")
async def cancel_chat_run_by_key(
    key: str = Query(..., min_length=1, max_length=200),
    principal: dict = Depends(verify_api_key),
):
    """Cancel an execution even when its response headers never reached the client."""
    from ...services.chat_runs import cancel_run, run_identity

    run_id = run_identity(principal["user_id"], key)
    await cancel_run(run_id, principal["user_id"])
    return {"message": "Cancellation processed", "run_id": run_id}


@router.get("/runs/{run_id}/events")
async def replay_chat_run(
    request: Request, run_id: str, after: int = 0, principal: dict = Depends(verify_api_key)
):
    from ...services.chat_runs import get_run, replay_run

    if not after and request.headers.get("Last-Event-ID"):
        try:
            after = int(request.headers["Last-Event-ID"])
        except ValueError as exc:
            raise HTTPException(422, "Invalid Last-Event-ID") from exc
    if after < 0:
        raise HTTPException(422, "after must be nonnegative")
    await asyncio.to_thread(get_run, run_id, principal["user_id"])
    return StreamingResponse(
        replay_run(run_id, principal["user_id"], after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-ID": run_id},
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_chat_run(run_id: str, principal: dict = Depends(verify_api_key)):
    from ...services.chat_runs import cancel_run

    await cancel_run(run_id, principal["user_id"])
    return {"message": "Cancellation processed"}


@router.post("/runs/{run_id}/withdraw", response_model=SessionBranchResponse)
async def withdraw_chat_run(run_id: str, principal: dict = Depends(verify_api_key)):
    """Remove the active turn from future context by creating an immutable branch."""
    from ...services.chat_runs import withdraw_run

    result = await withdraw_run(run_id, principal["user_id"])
    return SessionBranchResponse(
        session=SessionResponse(**result["session"]),
        messages=[
            SessionMessageResponse(
                id=item["id"],
                role=item["role"],
                content=item["content"],
                sources=_safe_sources(item.get("sources_json")),
                degraded=bool(item.get("degradation_reason")),
                degradation_reason=item.get("degradation_reason"),
            )
            for item in result["messages"]
        ],
        total=result["total"],
        next_before_id=result["next_before_id"],
    )


@router.post("/search", response_model=SearchChunksResponse)
async def search_chunks(request: ChatRequest, principal: dict = Depends(verify_api_key)):
    """Retrieve only, without calling LLM (for debugging)"""
    from ...core.metrics import CHAT_MODE_REQUEST_TOTAL
    from ...services.rag import retrieve

    CHAT_MODE_REQUEST_TOTAL.labels(
        endpoint="search",
        retrieval_profile=request.retrieval_profile,
        memory_mode=request.memory_mode,
    ).inc()

    await asyncio.to_thread(
        _validate_scope_ownership,
        principal["user_id"],
        request.meeting_ids,
        request.file_ids,
        request.session_id,
    )
    try:
        snapshot = build_retrieval_profile_snapshot(
            epoch=get_settings_epoch(),
            profile=request.retrieval_profile,
            memory_mode=request.memory_mode,
        )

        def _retrieve():
            with activate_settings_snapshot(snapshot):
                return retrieve(
                    query=request.question,
                    meeting_ids=request.meeting_ids,
                    file_ids=request.file_ids,
                    top_k=request.top_k,
                    file_types=request.file_types,
                    date_from=request.date_from,
                    date_to=request.date_to,
                    rag_mode=request.rag_mode,
                    user_id=principal["user_id"],
                )

        chunks, _qa = await asyncio.to_thread(_retrieve)
        results = [
            SearchChunkItem(
                meeting_id=c.get("metadata", {}).get("meeting_id", 0),
                meeting_title=c.get("metadata", {}).get("title", ""),
                content=c.get("content", ""),
                score=c.get("score", 0.0),
            )
            for c in chunks
        ]
        return SearchChunksResponse(results=results, total=len(results))
    except Exception as e:
        logger.error("Search request failed: %s", e, exc_info=True)
        raise _mapped_error_response(e, "Search") from e
