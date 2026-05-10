"""Tests for segment persistence in the processing pipeline."""

import json

import pytest

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_segments_json,
    get_write_connection,
    save_segments_json,
    update_meeting_file_status,
)


class TestSegmentPersistence:
    def test_save_and_load_segments(self, tmp_path):
        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Test",
                description="",
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
            )
            segments = [
                {"start": 0.0, "end": 3.0, "text": "Hello", "speaker": "A"},
                {"start": 3.5, "end": 6.0, "text": "World", "speaker": "B"},
            ]
            save_segments_json(conn, fid, json.dumps(segments))

        with get_write_connection() as conn:
            cached = get_segments_json(conn, fid)

        assert cached is not None
        loaded = json.loads(cached)
        assert len(loaded) == 2
        assert loaded[0]["speaker"] == "A"

    def test_get_segments_json_returns_none_when_absent(self, tmp_path):
        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Test",
                description="",
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
            )
            result = get_segments_json(conn, fid)

        assert result is None

    def test_overwrite_segments(self, tmp_path):
        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Test",
                description="",
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
            )
            save_segments_json(conn, fid, json.dumps([{"start": 0, "end": 1, "text": "old"}]))
            save_segments_json(conn, fid, json.dumps([{"start": 0, "end": 1, "text": "new"}]))

        with get_write_connection() as conn:
            cached = get_segments_json(conn, fid)

        loaded = json.loads(cached)
        assert loaded[0]["text"] == "new"

    def test_clear_segments_on_reprocess(self, tmp_path):
        """Simulate clearing segments_json when reprocessing."""
        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Test",
                description="",
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="t.mp3",
                file_path=str(audio_path),
            )
            save_segments_json(conn, fid, json.dumps([{"start": 0, "end": 1, "text": "old"}]))
            # Simulate reprocess: set segments_json to NULL
            save_segments_json(conn, fid, None)

        with get_write_connection() as conn:
            cached = get_segments_json(conn, fid)

        assert cached is None


class TestTimestampsCache:
    @pytest.mark.asyncio
    async def test_timestamps_uses_cached_segments(self, auth_headers, tmp_path):
        """Verify timestamps endpoint reads from DB cache first."""
        from httpx import ASGITransport, AsyncClient

        from src.core.database import update_meeting_status
        from src.main import app

        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake audio")
        segments = [
            {"start": 0.0, "end": 3.0, "text": "cached hello", "speaker": "A"},
        ]
        with get_write_connection() as conn:
            # Create meeting without file_path so the endpoint resolves via
            # list_meeting_files and discovers the file_id for cache lookup
            mid = create_meeting(
                conn,
                title="Cached Meeting",
                description="",
                file_type="audio",
                file_name="test.mp3",
                file_path=None,
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="test.mp3",
                file_path=str(audio_path),
            )
            update_meeting_file_status(conn, fid, "ready", transcript="cached hello")
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="cached hello")
            save_segments_json(conn, fid, json.dumps(segments))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/transcript/timestamps",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["segments"]) == 1
        assert data["segments"][0]["text"] == "cached hello"
