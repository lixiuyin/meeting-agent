"""Chat Q&A API - RAG-based meeting questions"""

import asyncio
import contextlib
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...api.middleware import limiter
from ...core.database import get_connection
from ...core.exceptions import map_error
from ...core.security import is_dev_user, verify_api_key
from ...models.errors import ApiErrorResponse, ErrorCode
from ...models.schemas import (
    ChatRequest,
    ChatResponse,
    PastSessionRef,
    SearchChunkItem,
    SearchChunksResponse,
    SourceResponse,
    WebResultResponse,
)
from ...services.chain import ask, ask_stream
from ...services.stream_bus import serialize_event

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)

_STREAM_QUEUE_WAIT_S = 30.0
_STREAM_CLOSE_TIMEOUT_S = 10.0


def _validate_scope_ownership(
    user_id: str,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
) -> None:
    """Verify that all requested meeting_ids and file_ids belong to *user_id*.

    Raises ``HTTPException(403)`` on any mismatch.  Skipped in dev mode
    where no authentication is enforced.
    """
    if is_dev_user(user_id):
        return

    with get_connection() as conn:
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


_stream_semaphore: asyncio.Semaphore | None = None
_stream_semaphore_lock = threading.Lock()


def _get_stream_semaphore() -> asyncio.Semaphore:
    global _stream_semaphore
    if _stream_semaphore is None:
        with _stream_semaphore_lock:
            if _stream_semaphore is None:
                import os

                from ...core.config import settings

                _stream_semaphore = asyncio.Semaphore(settings.STREAM_CONCURRENT_LIMIT)
                # L-3: Warn if running with multiple workers — each worker gets
                # its own semaphore, so actual concurrency = workers x limit.
                workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
                if workers > 1:
                    logger.warning(
                        "Running with WEB_CONCURRENCY=%d; effective stream "
                        "concurrency = %d x %d = %d (per-worker semaphore). "
                        "Consider Redis-based distributed semaphore for strict "
                        "enforcement.",
                        workers,
                        workers,
                        settings.STREAM_CONCURRENT_LIMIT,
                        workers * settings.STREAM_CONCURRENT_LIMIT,
                    )
    return _stream_semaphore


def set_stream_semaphore(sem: asyncio.Semaphore) -> None:
    """Replace the stream concurrency semaphore (used by lifespan / registry)."""
    global _stream_semaphore
    with _stream_semaphore_lock:
        _stream_semaphore = sem


def reset_stream_semaphore() -> None:
    """Reset the semaphore to reflect current settings (registered in registry)."""
    from ...core.config import settings

    set_stream_semaphore(asyncio.Semaphore(settings.STREAM_CONCURRENT_LIMIT))


@router.post("", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    chat_request: ChatRequest,
    principal: dict = Depends(verify_api_key),
):
    """Ask a question about meeting content with RAG and memory"""
    from ...core.metrics import CHAT_REQUEST_TOTAL

    await asyncio.to_thread(
        _validate_scope_ownership,
        principal["user_id"],
        chat_request.meeting_ids,
        chat_request.file_ids,
    )
    try:
        CHAT_REQUEST_TOTAL.labels(intent="retrieval").inc()
        result = await ask(
            question=chat_request.question,
            session_id=chat_request.session_id,
            user_id=principal["user_id"],
            meeting_ids=chat_request.meeting_ids,
            file_ids=chat_request.file_ids,
            top_k=chat_request.top_k,
            use_web_search=chat_request.use_web_search,
            web_search_results=chat_request.web_search_results,
            file_types=chat_request.file_types,
            date_from=chat_request.date_from,
            date_to=chat_request.date_to,
            rag_mode=chat_request.rag_mode,
        )
        return ChatResponse(
            answer=result.answer,
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
        error_resp = ApiErrorResponse(
            error="Failed to process chat request",
            code=ErrorCode.INTERNAL_ERROR,
            detail=None,
        )
        raise HTTPException(status_code=500, detail=error_resp.model_dump()) from e


@router.post("/stream")
@limiter.limit("20/minute")
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    principal: dict = Depends(verify_api_key),
):
    """Streaming variant — yields answer tokens via Server-Sent Events as they arrive."""
    await asyncio.to_thread(
        _validate_scope_ownership,
        principal["user_id"],
        chat_request.meeting_ids,
        chat_request.file_ids,
    )

    async def event_generator():
        saw_terminal = False
        semaphore = _get_stream_semaphore()
        # Only timeout the queue wait — stream generation itself has no hard cap.
        try:
            async with asyncio.timeout(_STREAM_QUEUE_WAIT_S):
                await semaphore.acquire()
        except TimeoutError:
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
                web_search_results=chat_request.web_search_results,
                file_types=chat_request.file_types,
                date_from=chat_request.date_from,
                date_to=chat_request.date_to,
                rag_mode=chat_request.rag_mode,
            )
            try:
                async for event in stream:
                    if event.get("type") in {"done", "error"}:
                        saw_terminal = True
                    yield serialize_event(event)
            except Exception as e:
                logger.error("Stream failed: %s", e, exc_info=True)
                mapped = map_error(e)
                status_code = getattr(mapped, "status_code", None)
                code = (
                    ErrorCode.LLM_ERROR if isinstance(status_code, int) else ErrorCode.STREAM_ERROR
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
            if not saw_terminal:
                logger.debug(
                    "Stream ended without terminal event; client may have disconnected early (M-16)"
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
            "Cache-Control": "no-cache",
        },
    )


@router.post("/search", response_model=SearchChunksResponse)
async def search_chunks(request: ChatRequest, principal: dict = Depends(verify_api_key)):
    """Retrieve only, without calling LLM (for debugging)"""
    from ...services.rag import retrieve

    await asyncio.to_thread(
        _validate_scope_ownership, principal["user_id"], request.meeting_ids, request.file_ids
    )
    try:
        chunks, _qa = await asyncio.to_thread(
            retrieve,
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
        error_resp = ApiErrorResponse(
            error="Search failed",
            code=ErrorCode.INTERNAL_ERROR,
            detail=None,
        )
        raise HTTPException(status_code=500, detail=error_resp.model_dump()) from e
