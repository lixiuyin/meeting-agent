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
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """A single typed event in the streaming pipeline."""

    type: str  # "step" | "token" | "sources" | "trace" | "error" | "done"
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0  # HIGH-5: Monotonic sequence for causal ordering
    parent_step: str | None = None  # HIGH-5: Parent step name for causality chain

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type, "seq": self.seq, **self.data}
        if self.parent_step:
            result["parent_step"] = self.parent_step
        return result


class StreamBus:
    """Async iterator that buffers typed events for SSE consumption.

    Thread-safe: emit() can be called from any thread; events are
    queued via asyncio.Queue and yielded by the async for loop.

    The queue is intentionally unbounded so terminal events (error/done/sentinel)
    are never dropped under bursty token streams.
    """

    def __init__(self, maxsize: int | None = None) -> None:
        # CONC-2: Default maxsize=2000 prevents unbounded memory growth from
        # slow clients while leaving enough room for bursty token streams.
        effective_max = maxsize if maxsize is not None and maxsize > 0 else 2000
        self._queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=effective_max)
        # Terminal slot: done/error events stored independently of the main
        # queue so they can never be lost due to back-pressure (H-CONC-5).
        self._terminal_event: StreamEvent | None = None
        # Structural slots: sources/trace events stored independently so
        # long token streams never drop them from the main queue (M-5).
        self._sources_event: StreamEvent | None = None
        self._trace_event: StreamEvent | None = None
        self._terminal_emitted: bool = False
        self._closed = False
        self._seq = itertools.count()  # Atomic monotonic counter
        self._lock = asyncio.Lock()  # protects terminal/structural event read-and-clear

    def emit(self, event: StreamEvent) -> None:
        """Enqueue an event (best-effort, non-blocking).

        Auto-assigns a monotonic sequence number (HIGH-5) so the frontend
        can reconstruct causal ordering even when events arrive out-of-order
        from concurrent pipeline branches.
        """
        if self._closed:
            return
        event.seq = next(self._seq)
        # Terminal events (done/error) go to dedicated slot — never dropped.
        # CPython attribute assignment is atomic under the GIL, so reading
        # _terminal_event in __anext__ (guarded by _lock) will always see
        # the fully assigned object, never a partial write.
        # M-4: Guard with _terminal_emitted so a late error cannot overwrite
        # an already-emitted done event.
        if event.type in ("done", "error"):
            if not self._terminal_emitted:
                self._terminal_event = event
            return
        # Structural events (sources/trace) go to dedicated slots so they
        # survive long token streams that fill the main queue (M-5).
        if event.type == "sources":
            self._sources_event = event
            return
        if event.type == "trace":
            self._trace_event = event
            return
        # Non-terminal: drain one if full to make room.
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("StreamBus queue full, dropping event: %s", event.type)

    def close(self) -> None:
        """Signal end of stream.

        For bounded queues, drain events if necessary to make room
        for the sentinel so the consumer always terminates.
        """
        if self._closed:
            return
        self._closed = True
        for _ in range(3):
            try:
                self._queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
        logger.error("StreamBus sentinel enqueue failed: queue unexpectedly full")

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
        if self._closed or self._terminal_event is not None:
            return
        event = StreamEvent(type="heartbeat")
        event.seq = next(self._seq)
        # Token traffic is flowing when the queue is full; the frontend stall
        # timer is being reset by the events ahead of this heartbeat, so it is
        # safe to drop.  Count stalls for SLO alerting.
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            from ..core.metrics import SSE_HEARTBEAT_STALLED_TOTAL

            SSE_HEARTBEAT_STALLED_TOTAL.inc()

    def emit_done(self, session_id: str, *, error: bool = False) -> None:
        """Emit the final done event.

        When *error* is True the frontend can use it as a signal to terminate
        the stream even when *session_id* is empty (e.g. pipeline failed before
        ``ensure_session`` completed).
        """
        payload: dict[str, object] = {"session_id": session_id}
        if error:
            payload["error"] = True
        self.emit(StreamEvent(type="done", data=payload))
        self.close()

    # -- Async iterator protocol --

    async def __aenter__(self) -> StreamBus:
        return self

    async def __aexit__(self, *args: object) -> None:
        # HIGH-19: Guarantee the bus is drained and closed on scope exit.
        if not self._closed:
            self.close()

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self

    async def __anext__(self) -> dict[str, Any]:
        # Yield structural events (sources, trace) before queue items so they
        # are never starved by token backpressure (M-5).
        async with self._lock:
            if self._sources_event is not None:
                event = self._sources_event
                self._sources_event = None
                return event.to_dict()
            if self._trace_event is not None:
                event = self._trace_event
                self._trace_event = None
                return event.to_dict()
            if self._terminal_event is not None and self._queue.empty():
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
            async with self._lock:
                # Drain any remaining structural events before terminating.
                if self._sources_event is not None:
                    se = self._sources_event
                    self._sources_event = None
                    return se.to_dict()
                if self._trace_event is not None:
                    te = self._trace_event
                    self._trace_event = None
                    return te.to_dict()
                if self._terminal_event is not None:
                    te = self._terminal_event
                    self._terminal_event = None
                    self._terminal_emitted = True
                    return te.to_dict()
            raise StopAsyncIteration
        return event.to_dict()


def serialize_event(event: dict[str, Any]) -> str:
    """Serialize a StreamBus event to an SSE data line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
