"""Measure chat completion, including terminal SSE failures under HTTP 200."""

import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core.metrics import CHAT_COMPLETION_DURATION, CHAT_COMPLETION_TOTAL


class ChatCompletionMetricsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or path not in {"/api/v1/chat", "/api/v1/chat/stream"}
        ):
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500
        complete = False
        finished_at: float | None = None
        recorded = False
        saw_done = False
        saw_error = False
        saw_degraded = False
        # serialize_event puts type first. Retain only the line prefix, never
        # whole answers or evidence. Bound memory even for very large tokens.
        prefix = b""

        async def observe(message: Message) -> None:
            nonlocal status, complete, finished_at, saw_done, saw_error, saw_degraded, prefix
            if message["type"] == "http.response.start":
                status = message["status"]
                saw_degraded = any(
                    name.lower() == b"x-chat-outcome" and value == b"degraded"
                    for name, value in message.get("headers", [])
                )
            elif message["type"] == "http.response.body" and path.endswith("/stream"):
                parts = message.get("body", b"").split(b"\n")
                for index, part in enumerate(parts):
                    prefix = (prefix + part[:256])[:256]
                    if index < len(parts) - 1:
                        match = re.match(
                            rb'data:\s*\{\s*"type"\s*:\s*"(done|error|status)"', prefix
                        )
                        if match:
                            saw_done |= match[1] == b"done"
                            saw_error |= match[1] == b"error"
                            saw_degraded |= match[1] == b"status" and bool(
                                re.search(rb'"status"\s*:\s*"degraded"', prefix)
                            )
                        prefix = b""
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True
                finished_at = time.monotonic()
                record_completion()

        def record_completion() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            if not 200 <= status < 300:
                outcome = "http_error"
            elif saw_error:
                outcome = "stream_error"
            elif not complete or (path.endswith("/stream") and not saw_done):
                outcome = "incomplete"
            elif saw_degraded:
                outcome = "degraded"
            else:
                outcome = "success"
            CHAT_COMPLETION_TOTAL.labels(endpoint=path, outcome=outcome).inc()
            CHAT_COMPLETION_DURATION.labels(endpoint=path, outcome=outcome).observe(
                (finished_at if finished_at is not None else time.monotonic()) - started
            )

        try:
            await self.app(scope, receive, observe)
        finally:
            record_completion()
