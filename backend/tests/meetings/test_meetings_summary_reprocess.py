"""Tests for meeting summary and reprocess endpoints."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    update_meeting_file_status,
    update_meeting_file_summary,
    update_meeting_status,
    update_meeting_summary_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _create_ready_meeting_with_file(transcript: str = "Hello world") -> int:
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Summary Meeting",
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


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_generate_summary_success(self, client, auth_headers):
        mid = _create_ready_meeting_with_file(transcript="Discussed project timeline.")
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Great summary"))
        with (
            patch("src.services.llm.get_llm", return_value=mock_llm),
            patch("src.services.tokenizer.count_tokens", return_value=10),
        ):
            async with client as c:
                resp = await c.post(
                    f"/api/v1/meetings/{mid}/summary",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "Great summary"
        assert data["tokens_used"] == 20
        assert len(data["per_file_summaries"]) == 1
        assert data["per_file_summaries"][0]["file_name"] == "test.pdf"

    @pytest.mark.asyncio
    async def test_generate_summary_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/99999/summary",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_summary_not_ready(self, client, auth_headers):
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
            resp = await c.post(
                f"/api/v1/meetings/{mid}/summary",
                headers=auth_headers,
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_generate_summary_no_transcript(self, client, auth_headers):
        mid = _create_ready_meeting_with_file(transcript="")
        async with client as c:
            resp = await c.post(
                f"/api/v1/meetings/{mid}/summary",
                headers=auth_headers,
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_generate_summary_mixed_files_uses_per_file_summaries(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Mixed Meeting",
                description="",
                file_type="video",
                file_name="demo.mp4",
                file_path="/tmp/demo.mp4",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="legacy transcript")

            video_id = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="video",
                file_name="demo.mp4",
                file_path="/tmp/demo.mp4",
            )
            update_meeting_file_status(conn, video_id, "ready", transcript="speaker content")
            update_meeting_file_summary(
                conn,
                video_id,
                summary="Video summary",
                key_points_json='["v1"]',
            )

            image_id = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="image",
                file_name="board.png",
                file_path="/tmp/board.png",
            )
            update_meeting_file_status(conn, image_id, "ready", transcript="ocr content")
            update_meeting_file_summary(
                conn,
                image_id,
                summary="Image summary",
                key_points_json='["i1"]',
            )

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Composed summary"))
        with (
            patch("src.services.llm.get_llm", return_value=mock_llm),
            patch("src.services.tokenizer.count_tokens", return_value=12),
        ):
            async with client as c:
                resp = await c.post(
                    f"/api/v1/meetings/{mid}/summary",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "Composed summary"
        assert len(data["per_file_summaries"]) == 2
        assert {item["file_type"] for item in data["per_file_summaries"]} == {"video", "image"}


class TestReprocess:
    @pytest.mark.asyncio
    async def test_reprocess_success(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Reprocess Meeting",
                description="",
                file_type="pdf",
                file_name="test.pdf",
                file_path="/tmp/test.pdf",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="old")
        with (
            patch("src.services.rag.delete_meeting_chunks"),
            patch("src.services.processor.process_meeting"),
        ):
            async with client as c:
                resp = await c.post(
                    f"/api/v1/meetings/{mid}/reprocess",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Reprocessing started"
        assert data["meeting_id"] == mid

    @pytest.mark.asyncio
    async def test_reprocess_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/99999/reprocess",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reprocess_single_file_success(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Reprocess File Meeting",
                description="",
                file_type="pdf",
                file_name="a.pdf",
                file_path="/tmp/a.pdf",
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="b.pdf",
                file_path="/tmp/b.pdf",
            )
            update_meeting_file_status(conn, fid, "error", error_message="boom")
        with (
            patch("src.services.rag.delete_meeting_chunks"),
            patch("src.services.processor.process_meeting_file"),
        ):
            async with client as c:
                resp = await c.post(
                    f"/api/v1/meetings/{mid}/files/{fid}/reprocess",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "File reprocessing started"
        assert data["meeting_id"] == mid
        with get_write_connection() as conn:
            job = conn.execute(
                "SELECT payload_json FROM durable_jobs WHERE dedupe_key=?",
                (f"file:{fid}",),
            ).fetchone()
        assert json.loads(job["payload_json"])["force_native_reindex"] is True

    @pytest.mark.asyncio
    async def test_reprocess_single_file_not_found(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Reprocess File Meeting Missing",
                description="",
                file_type="pdf",
                file_name="x.pdf",
                file_path="/tmp/x.pdf",
                user_id="test",
            )
        async with client as c:
            resp = await c.post(
                f"/api/v1/meetings/{mid}/files/999999/reprocess",
                headers=auth_headers,
            )
        assert resp.status_code == 404


class TestGetSummaryStatus:
    @pytest.mark.asyncio
    async def test_get_summary_returns_failed_status(self, client, auth_headers):
        # Arrange: create meeting and mark summary as failed
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Failed Summary Meeting",
                description="",
                file_type="pdf",
                file_name="f.pdf",
                file_path="/tmp/f.pdf",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="hello")
        update_meeting_summary_status(mid, "failed")

        # Act
        async with client as c:
            resp = await c.get(f"/api/v1/meetings/{mid}/summary", headers=auth_headers)

        # Assert: 200 with status=failed, not 404
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["summary"] is None

    @pytest.mark.asyncio
    async def test_get_summary_returns_pending_status_when_not_yet_generated(
        self, client, auth_headers
    ):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Pending Summary Meeting",
                description="",
                file_type="pdf",
                file_name="p.pdf",
                file_path="/tmp/p.pdf",
                user_id="test",
            )
            update_meeting_status(conn, mid, "processing")
            update_meeting_status(conn, mid, "ready", transcript="hello")

        async with client as c:
            resp = await c.get(f"/api/v1/meetings/{mid}/summary", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["summary"] is None

    @pytest.mark.asyncio
    async def test_get_summary_returns_404_for_missing_meeting(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings/999999/summary", headers=auth_headers)
        assert resp.status_code == 404
