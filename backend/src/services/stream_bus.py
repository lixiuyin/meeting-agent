"""Structured streaming event bus for SSE-based pipeline communication.

Provides typed events for pipeline step progress, token streaming,
source delivery, trace metadata, and completion signals.

Usage::

    bus = StreamBus()
    bus.emit_step("retrieve", "start")
    bus.emit_step("retrieve", "done", duration_ms=123.4)
    bus.emit_token("Hello")
    bus.emit_done(session_id="abc123")

    async for event in bus:
        # event is a dict ready for SSE serialization
        yield f"data: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from ..models.errors import ErrorCode

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """A single typed event in the streaming pipeline."""

    type: str  # "step" | "token" | "sources" | "trace" | "status" | "error" | "done"
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0  # HIGH-5: Monotonic sequence for causal ordering
    parent_step: str | None = None  # HIGH-5: Parent step name for causality chain
    buffered_bytes: int = field(default=0, repr=False)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type, "seq": self.seq, **self.data}
        if self.parent_step:
            result["parent_step"] = self.parent_step
        return result


class StreamBus:
    """Async iterator that buffers typed events for SSE consumption.

    ``emit`` may be called from worker threads. Foreign-thread writes are
    marshalled onto the iterator's owning event loop before touching the
    asyncio queue.
    """

    def __init__(self, maxsize: int | None = None, max_buffer_bytes: int | None = None) -> None:
        # CONC-2: Default maxsize=2000 prevents unbounded memory growth from
        # slow clients while leaving enough room for bursty token streams.
        effective_max = maxsize if maxsize is not None and maxsize > 0 else 2000
        self._queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=effective_max)
        self._max_buffer_bytes = (
            max_buffer_bytes if max_buffer_bytes is not None and max_buffer_bytes > 0 else 4 << 20
        )
        self._buffered_bytes = 0
        # Terminal slot: done/error events stored independently of the main
        # queue so they can never be lost due to back-pressure (H-CONC-5).
        self._terminal_event: StreamEvent | None = None
        # Critical overflow events remain ordered. Adjacent answer tokens are
        # coalesced, while sources/trace/web results retain their position.
        self._overflow_events: deque[StreamEvent] = deque()
        self._terminal_emitted: bool = False
        self._closed = False
        self._seq = itertools.count()
        self._state_lock = threading.RLock()
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
            self._owner_thread_id: int | None = threading.get_ident()
        except RuntimeError:
            self._loop = None
            self._owner_thread_id = None

    def _bind_owner_loop(self) -> None:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._loop is None:
                self._loop = loop
                self._owner_thread_id = threading.get_ident()
            elif self._loop is not loop:
                raise RuntimeError("A StreamBus cannot be consumed by multiple event loops")

    def _dispatch(self, callback, *args: Any) -> None:
        loop = self._loop
        if (
            loop is not None
            and loop.is_running()
            and threading.get_ident() != self._owner_thread_id
        ):
            loop.call_soon_threadsafe(callback, *args)
            return
        callback(*args)

    @staticmethod
    def _event_size(event: StreamEvent) -> int:
        if event.type == "token":
            return len(str(event.data.get("content", "")).encode("utf-8"))
        return len(json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8"))

    def _set_backpressure_error(self) -> None:
        if self._terminal_emitted or self._terminal_event is not None:
            return
        event = StreamEvent(
            type="error",
            data={
                "message": "Stream client is too slow; buffered output limit exceeded",
                "code": ErrorCode.STREAM_BACKPRESSURE_LIMIT,
            },
            seq=next(self._seq),
        )
        self._terminal_event = event
        self._closed = True
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        logger.error("StreamBus buffered output exceeded %d bytes", self._max_buffer_bytes)

    def _append_overflow(self, event: StreamEvent) -> None:
        size = self._event_size(event)
        if self._buffered_bytes + size > self._max_buffer_bytes:
            self._set_backpressure_error()
            return
        if event.type == "token" and self._overflow_events:
            previous = self._overflow_events[-1]
            if previous.type == "token":
                previous.data["content"] = str(previous.data.get("content", "")) + str(
                    event.data.get("content", "")
                )
                previous.buffered_bytes += size
                self._buffered_bytes += size
                return
        event.buffered_bytes = size
        self._overflow_events.append(event)
        self._buffered_bytes += size

    def emit(self, event: StreamEvent) -> None:
        """Enqueue an event (best-effort, non-blocking).

        Auto-assigns a monotonic sequence number (HIGH-5) so the frontend
        can reconstruct causal ordering even when events arrive out-of-order
        from concurrent pipeline branches.
        """
        self._dispatch(self._emit_now, event)

    def _emit_now(self, event: StreamEvent) -> None:
        with self._state_lock:
            if self._closed:
                return
            event.seq = next(self._seq)
            # Terminal events (done/error) go to dedicated slot — never dropped.
            # CPython attribute assignment is atomic under the GIL, so reading
            # _terminal_event in __anext__ (guarded by _lock) will always see
            # the fully assigned object, never a partial write.
            # The first terminal event wins.  In particular, a later ``done`` must
            # never erase an earlier error and make a failed request look
            # successful to the client.
            if event.type in ("done", "error"):
                if not self._terminal_emitted and self._terminal_event is None:
                    self._terminal_event = event
                    with suppress(asyncio.QueueFull):
                        self._queue.put_nowait(None)
                return
            critical = event.type in {"token", "sources", "trace", "status", "web_results"}
            if self._overflow_events:
                if critical:
                    self._append_overflow(event)
                else:
                    logger.warning(
                        "StreamBus backpressured, dropping progress event: %s", event.type
                    )
                return
            size = self._event_size(event)
            if self._buffered_bytes + size > self._max_buffer_bytes:
                self._set_backpressure_error()
                return
            event.buffered_bytes = size
            try:
                self._queue.put_nowait(event)
                self._buffered_bytes += size
            except asyncio.QueueFull:
                if critical:
                    self._append_overflow(event)
                    logger.warning("StreamBus queue full, buffering critical event: %s", event.type)
                else:
                    logger.warning("StreamBus queue full, dropping progress event: %s", event.type)

    def close(self) -> None:
        """Signal end of stream.

        For bounded queues, drain events if necessary to make room
        for the sentinel so the consumer always terminates.
        """
        self._dispatch(self._close_now)

    def _close_now(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            # A terminal slot is sufficient to end iteration once queued content is
            # drained.  Do not evict answer tokens merely to insert a sentinel.
            if self._terminal_event is not None:
                return
            with suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)

    # -- Convenience emitters --

    def emit_step(self, step: str, status: str, **meta: Any) -> None:
        """Emit a pipeline step event.

        Args:
            step: Step name (e.g. "retrieve", "generate")
            status: "start" or "done"
            **meta: Optional extra metadata (e.g. duration_ms)
        """
        self.emit(StreamEvent(type="step", data={"step": step, "status": status, **meta}))

    def emit_token(self, content: str) -> None:
        """Emit a single LLM output token."""
        self.emit(StreamEvent(type="token", data={"content": content}))

    def emit_sources(self, sources: list[dict]) -> None:
        """Emit retrieved source documents."""
        self.emit(StreamEvent(type="sources", data={"items": sources}))

    def emit_trace(self, trace: dict) -> None:
        """Emit pipeline execution trace."""
        self.emit(StreamEvent(type="trace", data={"trace": trace}))

    def emit_web_results(self, results: list[dict]) -> None:
        """Emit web search results."""
        self.emit(StreamEvent(type="web_results", data={"items": results}))

    def emit_status(self, status: str, *, reason: str | None = None) -> None:
        """Emit a machine-readable answer status without mixing it into text."""
        data: dict[str, Any] = {"status": status}
        if reason:
            data["reason"] = reason
        self.emit(StreamEvent(type="status", data=data))

    def emit_error(
        self,
        message: str,
        *,
        code: str | None = None,
        detail: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        """Emit an error event.

        Args:
            message: User-friendly error message (shown in UI).
            code: Machine-readable error code (e.g. ``LLM_RATE_LIMIT``).
            detail: Technical detail for debugging (not shown by default).
            exception_type: Python exception class name for classification.
        """
        data: dict[str, Any] = {"message": message}
        if code:
            data["code"] = code
        if detail:
            data["detail"] = detail
        if exception_type:
            data["exception_type"] = exception_type
        self.emit(StreamEvent(type="error", data=data))

    def emit_heartbeat(self) -> None:
        """Emit a keep-alive heartbeat through the main queue.

        Goes through the queue (best-effort via ``put_nowait``) so it actually
        wakes a consumer that is awaiting ``queue.get()`` during quiet phases of
        the pipeline (e.g. retrieval/rerank). When the queue is full, token
        traffic is already resetting the frontend's stall timer, so dropping
        the heartbeat is safe.

        Skipped once a terminal event is set (M-15) to avoid queueing
        heartbeats after the stream has already ended.
        """
        self._dispatch(self._emit_heartbeat_now)

    def _emit_heartbeat_now(self) -> None:
        with self._state_lock:
            if self._closed or self._terminal_event is not None or self._overflow_events:
                return
            event = StreamEvent(type="heartbeat", seq=next(self._seq))
            event.buffered_bytes = self._event_size(event)
            # Token traffic is flowing when the queue is full; the frontend stall
            # timer is being reset by the events ahead of this heartbeat, so it is
            # safe to drop.  Count stalls for SLO alerting.
            try:
                self._queue.put_nowait(event)
                self._buffered_bytes += event.buffered_bytes
            except asyncio.QueueFull:
                from ..core.metrics import SSE_HEARTBEAT_STALLED_TOTAL

                SSE_HEARTBEAT_STALLED_TOTAL.inc()

    def emit_done(
        self,
        session_id: str,
        *,
        error: bool = False,
        message_ids: list[int] | None = None,
    ) -> None:
        """Emit the final done event.

        When *error* is True the frontend can use it as a signal to terminate
        the stream even when *session_id* is empty (e.g. pipeline failed before
        ``ensure_session`` completed).
        """
        payload: dict[str, object] = {"session_id": session_id}
        if error:
            payload["error"] = True
        if message_ids:
            payload["message_ids"] = message_ids
        self._dispatch(self._emit_done_now, payload)

    def _emit_done_now(self, payload: dict[str, object]) -> None:
        self._emit_now(StreamEvent(type="done", data=payload))
        self._close_now()

    # -- Async iterator protocol --

    async def __aenter__(self) -> StreamBus:
        return self

    async def __aexit__(self, *args: object) -> None:
        # HIGH-19: Guarantee the bus is drained and closed on scope exit.
        if not self._closed:
            self.close()

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        self._bind_owner_loop()
        return self

    async def __anext__(self) -> dict[str, Any]:
        self._bind_owner_loop()
        with self._state_lock:
            if self._overflow_events and self._queue.empty():
                event = self._overflow_events.popleft()
                self._buffered_bytes -= event.buffered_bytes
                return event.to_dict()
            if (
                self._terminal_event is not None
                and self._queue.empty()
                and not self._overflow_events
            ):
                event = self._terminal_event
                self._terminal_event = None
                self._terminal_emitted = True
                return event.to_dict()
        try:
            event = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            if self._closed:
                raise StopAsyncIteration from None
            event = await self._queue.get()
        if event is None:
            with self._state_lock:
                if self._overflow_events:
                    buffered = self._overflow_events.popleft()
                    self._buffered_bytes -= buffered.buffered_bytes
                    return buffered.to_dict()
                if self._terminal_event is not None:
                    te = self._terminal_event
                    self._terminal_event = None
                    self._terminal_emitted = True
                    return te.to_dict()
            raise StopAsyncIteration
        with self._state_lock:
            self._buffered_bytes -= event.buffered_bytes
        return event.to_dict()


def serialize_event(event: dict[str, Any]) -> str:
    """Serialize a StreamBus event to an SSE data line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
