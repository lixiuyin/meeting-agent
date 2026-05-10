"""Tests for meeting update endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import create_meeting, get_write_connection, update_meeting_status
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _create_meeting(status: str = "ready") -> int:
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Original Title",
            description="Original desc",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            meeting_date="2024-01-01",
            user_id="test",
        )
        if status == "ready":
            update_meeting_status(conn, meeting_id, "processing")
        update_meeting_status(conn, meeting_id, status, transcript="test transcript")
    return meeting_id


class TestUpdateMeeting:
    @pytest.mark.asyncio
    async def test_update_meeting_success(self, client, auth_headers):
        mid = _create_meeting()
        async with client as c:
            resp = await c.put(
                f"/api/v1/meetings/{mid}",
                headers=auth_headers,
                json={
                    "title": "New Title",
                    "description": "New desc",
                    "meeting_date": "2024-12-31",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"
        assert data["description"] == "New desc"
        assert data["meeting_date"].startswith("2024-12-31")

    @pytest.mark.asyncio
    async def test_update_meeting_partial(self, client, auth_headers):
        mid = _create_meeting()
        async with client as c:
            resp = await c.put(
                f"/api/v1/meetings/{mid}",
                headers=auth_headers,
                json={"title": "Only Title"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Only Title"
        assert data["description"] == "Original desc"

    @pytest.mark.asyncio
    async def test_update_meeting_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.put(
                "/api/v1/meetings/99999",
                headers=auth_headers,
                json={"title": "X"},
            )
        assert resp.status_code == 404
