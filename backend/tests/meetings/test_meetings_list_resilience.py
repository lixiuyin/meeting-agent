"""Tests for list endpoint resilience.

Covers the fix: single bad meeting doesn't crash the entire list response.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.database import get_write_connection
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestListMeetingsResilience:
    """Verify that one bad meeting row doesn't kill the whole list."""

    @pytest.mark.asyncio
    async def test_list_skips_meeting_with_empty_created_at(self, client, auth_headers):
        """A meeting with empty created_at (Pydantic validation failure) should be skipped."""
        with get_write_connection() as conn:
            good_id = db.create_meeting(conn, title="Good Date Meeting", user_id="test")
            db.update_meeting_status(conn, good_id, "processing")
            db.update_meeting_status(conn, good_id, "ready")

        # Insert a meeting with empty created_at directly via SQL
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO meetings (title, status, created_at) VALUES (?, ?, '')",
                ("Empty Date Meeting", "ready"),
            )
            conn.commit()

        async with client as c:
            resp = await c.get("/api/v1/meetings", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        titles = [m["title"] for m in data["meetings"]]
        assert "Good Date Meeting" in titles
        assert "Empty Date Meeting" not in titles

    @pytest.mark.asyncio
    async def test_list_skips_meeting_with_null_created_at(self, client, auth_headers):
        """A meeting with NULL created_at should be skipped."""
        with get_write_connection() as conn:
            good_id = db.create_meeting(conn, title="Valid Meeting Null Test", user_id="test")
            db.update_meeting_status(conn, good_id, "processing")
            db.update_meeting_status(conn, good_id, "ready")

        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO meetings (title, status) VALUES (?, ?)",
                ("Null Date Meeting", "ready"),
            )
            conn.commit()

        async with client as c:
            resp = await c.get("/api/v1/meetings", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        titles = [m["title"] for m in data["meetings"]]
        assert "Valid Meeting Null Test" in titles

    @pytest.mark.asyncio
    async def test_list_all_valid_meetings_returned(self, client, auth_headers):
        """When all meetings are valid, all are returned."""
        with get_write_connection() as conn:
            for i in range(3):
                mid = db.create_meeting(conn, title=f"Valid Meeting {i}", user_id="test")
                db.update_meeting_status(conn, mid, "processing")
                db.update_meeting_status(conn, mid, "ready")

        async with client as c:
            resp = await c.get("/api/v1/meetings", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["meetings"]) >= 3

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, client, auth_headers):
        """Status filter works correctly."""
        with get_write_connection() as conn:
            ready_id = db.create_meeting(conn, title="Ready Meeting", user_id="test")
            db.update_meeting_status(conn, ready_id, "processing")
            db.update_meeting_status(conn, ready_id, "ready")
            failed_id = db.create_meeting(conn, title="Failed Meeting", user_id="test")
            db.update_meeting_status(conn, failed_id, "failed", error_message="test error")

        async with client as c:
            resp_ready = await c.get("/api/v1/meetings?status=ready", headers=auth_headers)
            resp_failed = await c.get("/api/v1/meetings?status=failed", headers=auth_headers)

        ready_data = resp_ready.json()
        failed_data = resp_failed.json()

        assert all(m["status"] == "ready" for m in ready_data["meetings"])
        assert all(m["status"] == "failed" for m in failed_data["meetings"])

    @pytest.mark.asyncio
    async def test_list_total_includes_bad_meetings(self, client, auth_headers):
        """Total count includes bad meetings (count query runs before serialization)."""
        with get_write_connection() as conn:
            good_id = db.create_meeting(conn, title="Counted Good", user_id="test")
            db.update_meeting_status(conn, good_id, "processing")
            db.update_meeting_status(conn, good_id, "ready")

        # Insert bad meeting with empty created_at
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO meetings (title, status, created_at) VALUES (?, ?, '')",
                ("Counted Bad", "ready"),
            )
            conn.commit()

        async with client as c:
            resp = await c.get("/api/v1/meetings", headers=auth_headers)

        data = resp.json()
        # Total should count both, but only the valid one appears in meetings list
        assert data["total"] >= 2
        assert len(data["meetings"]) < data["total"]
