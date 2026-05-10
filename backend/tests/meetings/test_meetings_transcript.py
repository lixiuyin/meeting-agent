"""Tests for meeting transcript endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    update_meeting_file_status,
    update_meeting_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _create_ready_meeting_with_file(transcript: str = "Hello transcript") -> int:
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Transcript Meeting",
            description="desc",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            user_id="test",
        )
        update_meeting_status(conn, meeting_id, "processing")
        update_meeting_status(conn, meeting_id, "ready", transcript=transcript)
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
        )
        update_meeting_file_status(conn, file_id, "ready", transcript=transcript)
    return meeting_id


class TestTranscript:
    @pytest.mark.asyncio
    async def test_get_transcript_plain(self, client, auth_headers):
        mid = _create_ready_meeting_with_file(transcript="This is the plain transcript.")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript?format=plain",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "plain"
        assert "This is the plain transcript." in data["transcript"]
        assert len(data["files"]) == 1
        assert data["files"][0]["file_name"] == "test.pdf"

    @pytest.mark.asyncio
    async def test_get_transcript_markdown(self, client, auth_headers):
        mid = _create_ready_meeting_with_file(transcript="This is the markdown transcript.")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript?format=markdown",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "markdown"
        assert "# Transcript Meeting" in data["transcript"]
        assert len(data["files"]) == 1

    @pytest.mark.asyncio
    async def test_get_transcript_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings/99999/transcript", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_transcript_no_transcript(self, client, auth_headers):
        with get_write_connection() as conn:
            meeting_id = create_meeting(
                conn,
                title="No Transcript",
                description="",
                file_type="pdf",
                file_name="test.pdf",
                file_path="/tmp/test.pdf",
                user_id="test",
            )
            update_meeting_status(conn, meeting_id, "processing")
            update_meeting_status(conn, meeting_id, "ready")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{meeting_id}/transcript",
                headers=auth_headers,
            )
        assert resp.status_code == 400
