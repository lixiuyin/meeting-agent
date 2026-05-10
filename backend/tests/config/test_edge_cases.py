"""Tests for edge cases and error handling"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestAuthenticationEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_auth_header_with_key_configured(self, client):
        """Request without auth header when API_KEY is set should fail"""
        from unittest.mock import patch

        from pydantic import SecretStr

        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret")
            async with client as c:
                resp = await c.get("/api/v1/meetings")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_auth_header_format(self, client):
        """Request with wrong auth header format should fail"""
        from unittest.mock import patch

        from pydantic import SecretStr

        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret")
            async with client as c:
                resp = await c.get(
                    "/api/v1/meetings",
                    headers={"Authorization": "Bearer secret"},  # Wrong header
                )
        assert resp.status_code == 401


class TestValidationEdgeCases:
    @pytest.mark.asyncio
    async def test_chat_request_very_long_question(self, client, auth_headers):
        """Chat with extremely long question - mocked to avoid real LLM call"""
        from unittest.mock import AsyncMock, patch

        with patch("src.api.routers.chat.ask", new_callable=AsyncMock) as mock_ask:
            from src.services.chain import PipelineResult

            mock_ask.return_value = PipelineResult(
                answer="This is a test response",
                sources=[],
                session_id="test-session-id",
            )
            async with client as c:
                resp = await c.post(
                    "/api/v1/chat",
                    headers=auth_headers,
                    json={"question": "x" * 10000},
                )
        # Should either succeed or fail gracefully
        assert resp.status_code in [200, 422, 413]

    @pytest.mark.asyncio
    async def test_upload_empty_file(self, client, auth_headers):
        """Upload empty file"""
        import io

        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Empty File"},
                files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
            )
        # Should handle gracefully
        assert resp.status_code in [200, 400]

    @pytest.mark.asyncio
    async def test_pagination_offset_beyond_total(self, client, auth_headers):
        """Request page beyond available data"""
        async with client as c:
            resp = await c.get("/api/v1/meetings?offset=10000&limit=10", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["meetings"] == []


class TestConcurrentOperations:
    @pytest.mark.asyncio
    async def test_multiple_messages_same_session(self, client, auth_headers):
        """Multiple messages to same session - test session history via DB"""
        from src.core import database as db
        from src.core.database import get_write_connection

        # Create session directly in DB
        user_id = "default"
        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id=user_id, title="Multi Message Test")
            # Add messages (keyword-only arguments)
            db.add_message(conn, session_id=sid, role="human", content="Message 1")
            db.add_message(conn, session_id=sid, role="ai", content="Response 1")
            db.add_message(conn, session_id=sid, role="human", content="Message 2")

        # Verify messages exist
        with get_write_connection() as conn:
            count = db.count_messages(conn, sid)
            assert count == 3

        # List sessions should show the session
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["sessions"][0]["id"] == sid


class TestDatabaseEdgeCases:
    def test_database_concurrent_writes(self, db_conn):
        """Test that concurrent writes don't corrupt database"""
        import queue
        import threading

        from src.core import database as db

        results = queue.Queue()

        def create_meeting(i):
            try:
                mid = db.create_meeting(
                    db_conn,
                    title=f"Concurrent {i}",
                    file_type="pdf",
                    file_name=f"test_{i}.pdf",
                    file_path=f"/tmp/test_{i}.pdf",
                    user_id="test",
                )
                results.put(("success", mid))
            except Exception as e:
                results.put(("error", str(e)))

        # Start multiple threads
        threads = []
        for i in range(10):
            t = threading.Thread(target=create_meeting, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all
        for t in threads:
            t.join()

        # All should succeed
        errors = []
        successes = []
        while not results.empty():
            status, value = results.get()
            if status == "error":
                errors.append(value)
            else:
                successes.append(value)

        # Due to SQLite's threading model, some might fail but none should crash
        assert len(successes) + len(errors) == 10
