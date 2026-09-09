"""Tests for chat API endpoints"""

import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_missing_question(self, client, auth_headers):
        """Chat request without question should fail validation"""
        async with client as c:
            resp = await c.post("/api/v1/chat", headers=auth_headers, json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_empty_question(self, client, auth_headers):
        """Chat request with empty question should fail validation"""
        async with client as c:
            resp = await c.post("/api/v1/chat", headers=auth_headers, json={"question": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_invalid_top_k(self, client, auth_headers):
        """Chat request with invalid top_k should fail validation"""
        async with client as c:
            resp = await c.post(
                "/api/v1/chat",
                headers=auth_headers,
                json={"question": "test", "top_k": 100},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_too_many_meeting_ids(self, client, auth_headers):
        """Chat request with too many meeting_ids should fail validation"""
        async with client as c:
            resp = await c.post(
                "/api/v1/chat",
                headers=auth_headers,
                json={"question": "test", "meeting_ids": list(range(51))},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_search_endpoint(self, client, auth_headers):
        """Chat search endpoint should return chunks without LLM call"""
        mock_chunks = [{"content": "chunk text", "metadata": {"meeting_id": 1}, "score": 0.5}]
        with patch("src.services.rag.retrieve", return_value=(mock_chunks, None)):
            async with client as c:
                resp = await c.post(
                    "/api/v1/chat/search",
                    headers=auth_headers,
                    json={"question": "test query"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_same_session_turns_are_serialized(self, client, auth_headers):
        from src.services.chain import PipelineResult

        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0
        active = 0
        max_active = 0

        async def _ask(**kwargs):
            nonlocal calls, active, max_active
            calls += 1
            active += 1
            max_active = max(max_active, active)
            if kwargs["question"] == "first":
                first_started.set()
                await release_first.wait()
            active -= 1
            return PipelineResult(answer=kwargs["question"], sources=[], session_id="shared")

        with patch("src.api.routers.chat.ask", _ask):
            async with client as c:
                first = asyncio.create_task(
                    c.post(
                        "/api/v1/chat",
                        headers=auth_headers,
                        json={"question": "first", "session_id": "shared"},
                    )
                )
                await asyncio.wait_for(first_started.wait(), timeout=2)
                second = asyncio.create_task(
                    c.post(
                        "/api/v1/chat",
                        headers=auth_headers,
                        json={"question": "second", "session_id": "shared"},
                    )
                )
                await asyncio.sleep(0.05)
                assert calls == 1
                release_first.set()
                responses = await asyncio.gather(first, second)

        assert [response.status_code for response in responses] == [200, 200]
        assert calls == 2
        assert max_active == 1


class TestChatValidation:
    @pytest.mark.asyncio
    async def test_chat_request_model(self):
        """Test ChatRequest model validation"""
        from src.models.schemas import ChatRequest

        # Valid request
        req = ChatRequest(question="What was discussed?")
        assert req.question == "What was discussed?"
        assert req.use_web_search is False

        # With web search enabled
        req = ChatRequest(
            question="What was discussed?",
            use_web_search=True,
            web_search_results=5,
        )
        assert req.use_web_search is True
        assert req.web_search_results == 5

        req = ChatRequest(question="What was discussed?", web_search_mode="fallback")
        assert req.web_search_mode == "fallback"

        req = ChatRequest(
            question="What was known then?",
            valid_at="2025-03-01T10:00:00+08:00",
            known_at="2025-03-02T10:00:00Z",
        )
        assert req.valid_at is not None and req.valid_at.utcoffset() is not None

        with pytest.raises(ValueError):
            ChatRequest(question="What was known then?", known_at="2025-03-02T10:00:00")

    @pytest.mark.asyncio
    async def test_chat_response_model(self):
        """Test ChatResponse model"""
        from src.models.schemas import ChatResponse, SourceResponse

        response = ChatResponse(
            answer="Test answer",
            sources=[
                SourceResponse(meeting_id=1, meeting_title="Test", content="content", score=0.9)
            ],
            session_id="abc123",
        )
        assert response.answer == "Test answer"
        assert len(response.sources) == 1
        assert response.session_id == "abc123"


class TestChatErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected_status", "expected_code"),
        [
            (TimeoutError("provider stalled"), 504, "LLM_ERROR"),
            (RuntimeError("rate limit exceeded"), 429, "RATE_LIMITED"),
            (RuntimeError("maximum context length exceeded"), 422, "VALIDATION_ERROR"),
            (RuntimeError("unexpected provider failure"), 500, "INTERNAL_ERROR"),
        ],
    )
    def test_non_stream_errors_use_stable_public_envelope(
        self, exc, expected_status, expected_code
    ):
        from src.api.routers.chat import _mapped_error_response

        response = _mapped_error_response(exc, "Chat request")

        assert response.status_code == expected_status
        assert response.detail["code"] == expected_code
        assert "provider stalled" not in response.detail["error"]
        assert response.detail["detail"].startswith("LLM")


class TestChatStream:
    @pytest.mark.asyncio
    async def test_chat_stream_emits_unexpected_end_error(self, client, auth_headers):
        async def _incomplete_stream(*_args, **_kwargs):
            yield {"type": "token", "content": "partial"}

        with patch("src.api.routers.chat.ask_stream", _incomplete_stream):
            async with client as c:
                resp = await c.post(
                    "/api/v1/chat/stream",
                    headers=auth_headers,
                    json={"question": "hello"},
                )

        assert resp.status_code == 200
        body = resp.text
        assert '"type": "error"' in body
        assert '"code": "STREAM_UNEXPECTED_END"' in body
