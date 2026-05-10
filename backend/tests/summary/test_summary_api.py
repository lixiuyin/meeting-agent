"""Tests for session summary API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.database import get_write_connection
from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _create_session_with_messages(user_id: str, title: str, messages: list[tuple[str, str]]) -> str:
    """Helper: create a session with messages directly in DB."""
    with get_write_connection() as conn:
        session_id = db.create_session(conn, user_id=user_id, title=title)
        for role, content in messages:
            db.add_message(conn, session_id=session_id, role=role, content=content)
    return session_id


class TestSessionSummaryAPI:
    @pytest.mark.asyncio
    async def test_get_summary_not_found(self, client, auth_headers):
        """GET summary for session with no summary returns 404."""
        async with client as c:
            resp = await c.get("/api/v1/sessions/nonexistent/summary", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_summarize_session_not_found(self, client, auth_headers):
        """POST summarize for non-existent session returns 404."""
        async with client as c:
            resp = await c.post("/api/v1/sessions/nonexistent/summarize", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_summarize_short_session(self, client, auth_headers):
        """POST summarize for session with too few messages returns 422."""
        user_id = "short_api_user"
        session_id = _create_session_with_messages(
            user_id, "Short", [("human", "hi"), ("ai", "hello")]
        )
        async with client as c:
            resp = await c.post(f"/api/v1/sessions/{session_id}/summarize", headers=auth_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_search_sessions_validation(self, client, auth_headers):
        """POST search with empty query fails validation."""
        async with client as c:
            resp = await c.post(
                "/api/v1/sessions/search",
                json={"query": "", "user_id": "test"},
                headers=auth_headers,
            )
        assert resp.status_code == 422
