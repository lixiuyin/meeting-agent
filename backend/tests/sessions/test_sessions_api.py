"""Tests for sessions API endpoints"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSessionsCRUD:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client, auth_headers):
        """List sessions for user with no sessions"""
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["sessions"] == []

    @pytest.mark.asyncio
    async def test_list_sessions_with_data(self, client, auth_headers):
        """List sessions returns created sessions - uses direct DB insertion"""
        from src.core import database as db
        from src.core.database import get_write_connection

        # Create a session directly in the database
        user_id = "default"
        with get_write_connection() as conn:
            _ = db.create_session(conn, user_id=user_id, title="Test Session")

        # List sessions
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["sessions"]) >= 1

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, client, auth_headers):
        """Delete non-existent session returns 404"""
        async with client as c:
            resp = await c.delete("/api/v1/sessions/nonexistent_session_id", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_session_success(self, client, auth_headers):
        """Delete an existing session"""
        from src.core import database as db
        from src.core.database import get_write_connection

        # Create a session directly in the database
        user_id = "default"
        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id=user_id, title="Session to Delete")

        # Use single client context for both operations
        async with client as c:
            # Delete the session via API
            resp = await c.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
            assert resp.status_code == 200

            # Verify it's deleted (same client context)
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
            data = resp.json()
            session_ids = [s["id"] for s in data["sessions"]]
            assert sid not in session_ids


class TestSessionMessages:
    @pytest.mark.asyncio
    async def test_get_session_messages_not_found(self, client, auth_headers):
        """GET /sessions/{id}/messages returns 404 for unknown session."""
        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/nonexistent_session_id_xyz/messages",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_session_messages_success(self, client, auth_headers):
        """GET /sessions/{id}/messages returns messages with correct shape."""
        from langchain_core.messages import AIMessage, HumanMessage

        from src.core import database as db
        from src.core.database import get_write_connection
        from src.services.memory import get_session_history

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")

        hist = get_session_history(sid)
        hist.add_message(HumanMessage(content="hello world"))
        hist.add_message(AIMessage(content="hi there"))

        async with client as c:
            resp = await c.get(
                f"/api/v1/sessions/{sid}/messages",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "human"
        assert data["messages"][1]["role"] == "ai"


class TestSessionSummarize:
    @pytest.mark.asyncio
    async def test_summarize_not_found(self, client, auth_headers):
        """POST /sessions/{id}/summarize returns 404 for unknown session."""
        async with client as c:
            resp = await c.post(
                "/api/v1/sessions/nonexistent_sum_session/summarize",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_summarize_too_few_messages_returns_422(self, client, auth_headers):
        """POST /sessions/{id}/summarize returns 422 when session has too few messages."""
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")

        async with client as c:
            resp = await c.post(
                f"/api/v1/sessions/{sid}/summarize",
                headers=auth_headers,
            )
        assert resp.status_code == 422


class TestSessionSummaries:
    @pytest.mark.asyncio
    async def test_list_summaries_empty(self, client, auth_headers):
        """GET /sessions/summaries returns empty list for user with no summaries."""
        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/summaries",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["summaries"] == []

    @pytest.mark.asyncio
    async def test_list_summaries_limit(self, client, auth_headers):
        """GET /sessions/summaries respects limit parameter."""
        from src.core import database as db
        from src.core.database import get_write_connection

        user_id = "default"
        for i in range(3):
            with get_write_connection() as conn:
                sid = db.create_session(conn, user_id=user_id)
                db.upsert_session_summary(
                    conn,
                    session_id=sid,
                    user_id=user_id,
                    summary=f"summary {i}",
                    topics=None,
                    key_entities=None,
                    decisions=None,
                    turn_count=5,
                )

        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/summaries?limit=2",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["summaries"]) == 2

    @pytest.mark.asyncio
    async def test_list_summaries_offset(self, client, auth_headers):
        """GET /sessions/summaries offset pagination works."""
        from src.core import database as db
        from src.core.database import get_write_connection

        user_id = "default"
        for i in range(3):
            with get_write_connection() as conn:
                sid = db.create_session(conn, user_id=user_id)
                db.upsert_session_summary(
                    conn,
                    session_id=sid,
                    user_id=user_id,
                    summary=f"summary {i}",
                    topics=None,
                    key_entities=None,
                    decisions=None,
                    turn_count=5,
                )

        async with client as c:
            page1 = await c.get(
                "/api/v1/sessions/summaries?limit=2&offset=0",
                headers=auth_headers,
            )
            page2 = await c.get(
                "/api/v1/sessions/summaries?limit=2&offset=2",
                headers=auth_headers,
            )
        assert page1.status_code == 200
        assert page2.status_code == 200
        p1_ids = {s["session_id"] for s in page1.json()["summaries"]}
        p2_ids = {s["session_id"] for s in page2.json()["summaries"]}
        # No overlap between pages
        assert not p1_ids.intersection(p2_ids)


class TestSessionSearch:
    @pytest.mark.asyncio
    async def test_search_sessions_empty_returns_ok(self, client, auth_headers):
        """POST /sessions/search returns 200 with empty results when nothing matches."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "src.api.routers.sessions.session_summary_service.search_past_conversations",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/sessions/search",
                    headers=auth_headers,
                    json={"query": "very random xyz query", "user_id": "default"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "results" in data

    @pytest.mark.asyncio
    async def test_search_sessions_missing_query(self, client, auth_headers):
        """POST /sessions/search without query returns 422."""
        async with client as c:
            resp = await c.post(
                "/api/v1/sessions/search",
                headers=auth_headers,
                json={"user_id": "default"},
            )
        assert resp.status_code == 422
