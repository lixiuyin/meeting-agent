"""Tests for meeting timestamps endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import (
    bulk_upsert_speaker_mappings,
    create_meeting,
    create_meeting_file,
    get_write_connection,
    save_segments_json,
    update_meeting_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestTimestamps:
    @pytest.mark.asyncio
    async def test_timestamps_non_video_returns_404(self, client, auth_headers):
        """Non-AV files should return 404 with no_av_file detail."""
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="PDF Meeting",
                description="",
                file_type="pdf",
                file_name="test.pdf",
                file_path="/tmp/test.pdf",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="PDF content")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript/timestamps",
                headers=auth_headers,
            )
        assert resp.status_code == 404
        data = resp.json()
        assert "no_av_file" in data.get("detail", "") or "no_av_file" in data.get("message", "")

    @pytest.mark.asyncio
    async def test_timestamps_video(self, client, auth_headers, tmp_path):
        video_path = tmp_path / "test.mp4"
        video_path.write_text("fake video")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Video Meeting",
                description="",
                file_type="video",
                file_name="test.mp4",
                file_path=str(video_path),
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="video transcript")
        fake_segments = [
            {"start": 0.0, "end": 1.0, "text": "hello"},
            {"start": 1.0, "end": 2.0, "text": "world"},
        ]
        with patch(
            "src.services.transcriber.transcribe_with_timestamps",
            new_callable=AsyncMock,
            return_value=fake_segments,
        ):
            async with client as c:
                resp = await c.get(
                    f"/api/v1/meetings/{mid}/transcript/timestamps",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["segments"]) == 2
        assert data["total_duration"] == 2.0

    @pytest.mark.asyncio
    async def test_timestamps_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/99999/transcript/timestamps",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_timestamps_not_ready(self, client, auth_headers):
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
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript/timestamps",
                headers=auth_headers,
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_timestamps_assemblyai(self, client, auth_headers, tmp_path):
        video_path = tmp_path / "meeting.mp3"
        video_path.write_text("fake audio")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="AssemblyAI Meeting",
                description="",
                file_type="audio",
                file_name="meeting.mp3",
                file_path=str(video_path),
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="A: hello\nB: world")
        fake_segments = [
            {"start": 0.0, "end": 3.0, "text": "hello", "speaker": "A"},
            {"start": 3.5, "end": 6.0, "text": "world", "speaker": "B"},
        ]
        with (
            patch(
                "src.services.transcriber.transcribe_with_timestamps",
                new_callable=AsyncMock,
                return_value=fake_segments,
            ),
            patch("src.core.config.settings") as mock_settings,
        ):
            mock_settings.ASR_PROVIDER = "assemblyai"
            async with client as c:
                resp = await c.get(
                    f"/api/v1/meetings/{mid}/transcript/timestamps",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["segments"]) == 2
        assert data["segments"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_timestamps_apply_speaker_mappings(self, client, auth_headers, tmp_path):
        audio_path = tmp_path / "meeting.wav"
        audio_path.write_text("fake audio")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Mapped Speakers", description="", user_id="test")
            file_id = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="meeting.wav",
                file_path=str(audio_path),
            )
            save_segments_json(
                conn,
                file_id,
                '[{"start":0.0,"end":1.5,"text":"hello","speaker":"A"},{"start":1.5,"end":3.0,"text":"world","speaker":"B"}]',
            )
            bulk_upsert_speaker_mappings(
                conn,
                file_id,
                mid,
                [("A", "Alice"), ("B", "Bob")],
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="Alice: hello\nBob: world")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript/timestamps",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["segments"][0]["speaker"] == "Alice"
        assert data["segments"][1]["speaker"] == "Bob"
