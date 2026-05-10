"""Tests for speaker identification API endpoints."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    list_speaker_mappings,
    save_segments_json,
    update_meeting_file_status,
    update_meeting_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _create_audio_meeting(tmp_path, segments=None, transcript="hello world"):
    """Helper: create a ready audio meeting with optional cached segments."""
    audio_path = tmp_path / "test.mp3"
    audio_path.write_text("fake audio")
    with get_write_connection() as conn:
        mid = create_meeting(
            conn,
            title="Audio Meeting",
            description="",
            file_type="audio",
            file_name="test.mp3",
            file_path=str(audio_path),
            user_id="test",
        )
        fid = create_meeting_file(
            conn,
            meeting_id=mid,
            file_type="audio",
            file_name="test.mp3",
            file_path=str(audio_path),
        )
        update_meeting_file_status(conn, fid, "ready", transcript=transcript)
        update_meeting_status(conn, mid, "processing")
        update_meeting_status(conn, mid, "ready", transcript=transcript)
        if segments is not None:
            save_segments_json(conn, fid, json.dumps(segments))
    return mid, fid


SAMPLE_SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "Hello everyone", "speaker": "A"},
    {"start": 3.5, "end": 6.0, "text": "Hi there", "speaker": "B"},
    {"start": 7.0, "end": 10.0, "text": "Let's begin", "speaker": "A"},
]


class TestGetSpeakers:
    @pytest.mark.asyncio
    async def test_get_speakers_from_cached_segments(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_id"] == fid
        assert len(data["speakers"]) == 2
        # Speaker A appears first (OrderedDict preserves insertion order)
        speaker_a = data["speakers"][0]
        assert speaker_a["speaker_code"] == "A"
        assert speaker_a["utterance_count"] == 2
        assert speaker_a["sample"]["text"] == "Hello everyone"
        speaker_b = data["speakers"][1]
        assert speaker_b["speaker_code"] == "B"
        assert speaker_b["utterance_count"] == 1

    @pytest.mark.asyncio
    async def test_get_speakers_fallback_retranscribe(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(tmp_path, segments=None)
        fake_segments = [
            {"start": 0.0, "end": 2.0, "text": "Fallback text", "speaker": "X"},
        ]
        # Patch at the source module since _load_segments does a local import
        with patch(
            "src.services.transcriber.transcribe_with_timestamps",
            new_callable=AsyncMock,
            return_value=fake_segments,
        ):
            async with client as c:
                resp = await c.get(
                    f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["speakers"]) == 1
        assert data["speakers"][0]["speaker_code"] == "X"

    @pytest.mark.asyncio
    async def test_get_speakers_meeting_not_found(self, client, auth_headers, tmp_path):
        _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/99999/files/1/speakers",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_speakers_file_not_found(self, client, auth_headers, tmp_path):
        mid, _ = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/99999/speakers",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_speakers_non_audio_file(self, client, auth_headers, tmp_path):
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_text("fake pdf")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="PDF Meeting",
                description="",
                file_type="pdf",
                file_name="test.pdf",
                file_path=str(pdf_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="test.pdf",
                file_path=str(pdf_path),
            )
            update_meeting_file_status(conn, fid, "ready", transcript="PDF content")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                headers=auth_headers,
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_speakers_file_not_ready(self, client, auth_headers, tmp_path):
        audio_path = tmp_path / "test.mp3"
        audio_path.write_text("fake audio")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Not Ready",
                description="",
                file_type="audio",
                file_name="test.mp3",
                file_path=str(audio_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="test.mp3",
                file_path=str(audio_path),
            )
            # Status stays "processing" (default)
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                headers=auth_headers,
            )
        assert resp.status_code == 400


class TestUpdateSpeakers:
    @pytest.mark.asyncio
    async def test_update_speaker_names(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        # Patch at the source module since _update_db_and_index does local imports.
        # Also mock the background summary regeneration to avoid flaky LLM API calls.
        # Force shadow-swap reindex to fail so the test deterministically exercises
        # the fallback path (delete_meeting_chunks + index_meeting_segments).
        with (
            patch("src.services.rag.delete_meeting_chunks") as mock_del,
            patch("src.services.rag.index_meeting_segments") as mock_idx,
            patch(
                "src.api.routers.meetings._speakers._regenerate_summaries_after_rename",
                new_callable=lambda: lambda *a, **kw: AsyncMock(),
            ),
            patch(
                "chromadb.PersistentClient",
                side_effect=RuntimeError("force fallback path in test"),
            ),
        ):
            async with client as c:
                resp = await c.put(
                    f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                    headers=auth_headers,
                    json={
                        "mappings": [
                            {"speaker_code": "A", "speaker_name": "Alice"},
                            {"speaker_code": "B", "speaker_name": "Bob"},
                        ],
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_id"] == fid
        assert "Updated 2 speaker name(s)" in data["message"]
        # Check mappings persisted
        with get_write_connection() as conn:
            mappings = list_speaker_mappings(conn, fid)
        mapping_dict = {m["speaker_code"]: m["speaker_name"] for m in mappings}
        assert mapping_dict["A"] == "Alice"
        assert mapping_dict["B"] == "Bob"
        # Verify re-index was called
        mock_del.assert_called_once_with(mid, file_id=fid)
        mock_idx.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_speakers_empty_mappings(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        # Even with empty mappings, the endpoint calls delete_meeting_chunks
        # which needs a Chroma vectorstore — mock it
        with (
            patch("src.services.rag.delete_meeting_chunks"),
            patch("src.services.rag.index_meeting_segments"),
        ):
            async with client as c:
                resp = await c.put(
                    f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                    headers=auth_headers,
                    json={"mappings": []},
                )
        # Empty mappings list is valid — returns success with 0 updates
        assert resp.status_code == 200
        assert "Updated 0 speaker name(s)" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_update_speakers_regenerates_transcript(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(
            tmp_path, segments=SAMPLE_SEGMENTS, transcript="old transcript"
        )
        with (
            patch("src.services.rag.delete_meeting_chunks"),
            patch("src.services.rag.index_meeting_segments"),
        ):
            async with client as c:
                resp = await c.put(
                    f"/api/v1/meetings/{mid}/files/{fid}/speakers",
                    headers=auth_headers,
                    json={
                        "mappings": [
                            {"speaker_code": "A", "speaker_name": "Alice"},
                        ],
                    },
                )
        assert resp.status_code == 200
        # Verify transcript was updated (should contain "Alice" not "A")
        from src.core.database import get_meeting_file

        with get_write_connection() as conn:
            f = get_meeting_file(conn, fid)
        assert "Alice" in f["transcript"]


class TestGetSpeakerAudio:
    @pytest.mark.asyncio
    async def test_get_speaker_audio_not_found_speaker(self, client, auth_headers, tmp_path):
        mid, fid = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/speakers/Z/audio",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_speaker_audio_source_missing(self, client, auth_headers, tmp_path):
        # Create meeting pointing to a non-existent file
        mid, fid = _create_audio_meeting(tmp_path, segments=SAMPLE_SEGMENTS)
        # Remove the source file
        (tmp_path / "test.mp3").unlink()
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/speakers/A/audio",
                headers=auth_headers,
            )
        assert resp.status_code == 404


class TestSpeakerMappingPersistence:
    def test_upsert_and_list_mappings(self, tmp_path):
        from src.core.database import bulk_upsert_speaker_mappings

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
            bulk_upsert_speaker_mappings(conn, fid, mid, [("A", "Alice"), ("B", "Bob")])
            mappings = list_speaker_mappings(conn, fid)

        assert len(mappings) == 2
        codes = {m["speaker_code"] for m in mappings}
        assert codes == {"A", "B"}

    def test_upsert_overwrites_existing(self, tmp_path):
        from src.core.database import bulk_upsert_speaker_mappings

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
            bulk_upsert_speaker_mappings(conn, fid, mid, [("A", "Alice")])
            bulk_upsert_speaker_mappings(conn, fid, mid, [("A", "Alicia")])
            mappings = list_speaker_mappings(conn, fid)

        assert len(mappings) == 1
        assert mappings[0]["speaker_name"] == "Alicia"
