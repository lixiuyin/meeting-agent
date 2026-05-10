"""Tests for meeting export endpoint."""

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


def _create_ready_meeting(transcript: str = "Hello world") -> int:
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Export Meeting",
            description="desc",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            meeting_date="2024-01-01",
            user_id="test",
        )
        update_meeting_status(conn, meeting_id, "processing")
        update_meeting_status(conn, meeting_id, "ready", transcript=transcript)
    return meeting_id


class TestExportMeeting:
    @pytest.mark.asyncio
    async def test_export_uses_meeting_files_metadata(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Export Meeting Files",
                description="desc",
                meeting_date="2024-01-01",
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="recording.mp3",
                file_path="/tmp/recording.mp3",
            )
            update_meeting_file_status(conn, fid, "ready", transcript="audio transcript")
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="audio transcript")

        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/export?format=markdown",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        content = resp.json()["content"]
        assert "recording.mp3" in content
        assert "audio transcript" in content

    @pytest.mark.asyncio
    async def test_export_markdown(self, client, auth_headers):
        mid = _create_ready_meeting(transcript="This is the transcript.")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/export?format=markdown",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "markdown"
        assert "Export Meeting" in data["content"]
        assert data["filename"].endswith(".md")

    @pytest.mark.asyncio
    async def test_export_json(self, client, auth_headers):
        mid = _create_ready_meeting(transcript="This is the transcript.")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/export?format=json",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "json"
        assert data["filename"].endswith(".json")
        assert "This is the transcript." in data["content"]

    @pytest.mark.asyncio
    async def test_export_txt(self, client, auth_headers):
        mid = _create_ready_meeting(transcript="This is the transcript.")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/export?format=txt",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "txt"
        assert data["filename"].endswith(".txt")

    @pytest.mark.asyncio
    async def test_export_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings/99999/export", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_export_not_ready(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Not Ready",
                description="",
                file_type="pdf",
                file_name="test.pdf",
                file_path="/tmp/test.pdf",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
        async with client as c:
            resp = await c.get(f"/api/v1/meetings/{mid}/export", headers=auth_headers)
        assert resp.status_code == 400
