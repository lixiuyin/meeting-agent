import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.api import slo_metrics


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,parts,outcome",
    [
        (200, [b'data: {"type": "do', b'ne", "answer": "ok"}\n\n'], "success"),
        (200, [b'data: {"type": "error"}\n\n', b'data: {"type": "done"}\n\n'], "stream_error"),
        (200, [b'data: {"type": "token", "content": "x"}\n\n'], "incomplete"),
        (429, [b"queue full"], "http_error"),
        (
            200,
            [b'data: {"type":"status","status":"degraded"}\n\n', b'data: {"type":"done"}\n\n'],
            "degraded",
        ),
    ],
)
async def test_sse_completion_not_http_headers_decides_success(monkeypatch, status, parts, outcome):
    counter, histogram = Mock(), Mock()
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_TOTAL", counter)
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_DURATION", histogram)
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status})
        for part in parts:
            await asyncio.sleep(0.005)
            await send({"type": "http.response.body", "body": part, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        sent.append(message)

    await slo_metrics.ChatCompletionMetricsMiddleware(app)(
        {"type": "http", "method": "POST", "path": "/api/v1/chat/stream"}, AsyncMock(), send
    )
    counter.labels.assert_called_once_with(endpoint="/api/v1/chat/stream", outcome=outcome)
    assert histogram.labels.return_value.observe.call_args.args[0] >= 0.005 * len(parts)
    assert [message.get("body") for message in sent[1:-1]] == parts


@pytest.mark.asyncio
async def test_send_failure_is_not_counted_as_success(monkeypatch):
    counter = Mock()
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_TOTAL", counter)
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_DURATION", Mock())

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        if message["type"] == "http.response.body":
            raise ConnectionError("client disconnected")

    with pytest.raises(ConnectionError):
        await slo_metrics.ChatCompletionMetricsMiddleware(app)(
            {"type": "http", "method": "POST", "path": "/api/v1/chat"}, AsyncMock(), send
        )
    counter.labels.assert_called_once_with(endpoint="/api/v1/chat", outcome="incomplete")


@pytest.mark.asyncio
async def test_completed_response_is_counted_before_background_cleanup(monkeypatch):
    counter = Mock()
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_TOTAL", counter)
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_DURATION", Mock())

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})
        counter.labels.return_value.inc.assert_called_once()
        # A post-response task failure must not double count or rewrite success.
        raise RuntimeError("background cleanup")

    with pytest.raises(RuntimeError, match="background cleanup"):
        await slo_metrics.ChatCompletionMetricsMiddleware(app)(
            {"type": "http", "method": "POST", "path": "/api/v1/chat"}, AsyncMock(), AsyncMock()
        )
    counter.labels.assert_called_once_with(endpoint="/api/v1/chat", outcome="success")


def test_production_registration_order_exposes_completion_metrics(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api import middleware

    counter = Mock()
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_TOTAL", counter)
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_DURATION", Mock())
    monkeypatch.setattr(middleware, "_METRICS_ENABLED", False)
    app = FastAPI()
    # Follow src.main, including the initially uninitialized metrics flag.
    middleware.setup_rate_limiter(app)
    middleware.setup_middleware(app)

    @app.post("/api/v1/chat")
    async def chat():
        return {"answer": "ok"}

    response = TestClient(app).post("/api/v1/chat", json={})
    assert response.status_code == 200
    counter.labels.assert_called_once_with(endpoint="/api/v1/chat", outcome="success")


@pytest.mark.asyncio
async def test_rest_degraded_response_is_not_success(monkeypatch):
    counter = Mock()
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_TOTAL", counter)
    monkeypatch.setattr(slo_metrics, "CHAT_COMPLETION_DURATION", Mock())

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-chat-outcome", b"degraded")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"degraded": true}'})

    await slo_metrics.ChatCompletionMetricsMiddleware(app)(
        {"type": "http", "method": "POST", "path": "/api/v1/chat"}, AsyncMock(), AsyncMock()
    )
    counter.labels.assert_called_once_with(endpoint="/api/v1/chat", outcome="degraded")
