"""Tests for meeting file endpoints."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import settings
from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    save_segments_json,
    update_meeting_file_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMeetingFiles:
    @pytest.mark.asyncio
    async def test_get_file_success(self, client, auth_headers):
        upload_dir = settings.UPLOAD_DIR / "test_files"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / "report.pdf"
        file_path.write_text("fake pdf content")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="File Meeting",
                description="",
                file_type="pdf",
                file_name="report.pdf",
                file_path=str(file_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="report.pdf",
                file_path=str(file_path),
            )
            update_meeting_file_status(conn, fid, "ready")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_get_file_meeting_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings/99999/files/1", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_file_file_not_found(self, client, auth_headers, tmp_path):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="File Meeting",
                description="",
                file_type="pdf",
                file_name="x.pdf",
                file_path=str(tmp_path / "x.pdf"),
                user_id="test",
            )
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/99999",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_signed_url_token_is_bound_to_file(self, client, auth_headers, tmp_path):
        upload_dir = settings.UPLOAD_DIR / "test_files"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file1_path = upload_dir / "report-1.pdf"
        file2_path = upload_dir / "report-2.pdf"
        file1_path.write_text("fake pdf content 1")
        file2_path.write_text("fake pdf content 2")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Token Scope Meeting",
                description="",
                file_type="pdf",
                file_name="report-1.pdf",
                file_path=str(file1_path),
                user_id="test",
            )
            fid1 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="report-1.pdf",
                file_path=str(file1_path),
            )
            fid2 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="report-2.pdf",
                file_path=str(file2_path),
            )
            update_meeting_file_status(conn, fid1, "ready")
            update_meeting_file_status(conn, fid2, "ready")

        async with client as c:
            signed = await c.post(
                f"/api/v1/meetings/{mid}/files/{fid1}/signed-url",
                headers=auth_headers,
            )
            assert signed.status_code == 200
            token = signed.json()["token"]

            ok = await c.get(f"/api/v1/meetings/{mid}/files/{fid1}?token={token}")
            assert ok.status_code == 200

            denied = await c.get(f"/api/v1/meetings/{mid}/files/{fid2}?token={token}")
            assert denied.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_file_success(self, client, auth_headers, tmp_path):
        file_path = tmp_path / "delete.pdf"
        file_path.write_text("delete me")
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Delete Meeting",
                description="",
                file_type="pdf",
                file_name="delete.pdf",
                file_path=str(file_path),
                user_id="test",
            )
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="delete.pdf",
                file_path=str(file_path),
            )
            update_meeting_file_status(conn, fid, "ready")
        with patch("src.services.rag.delete_meeting_chunks"):
            async with client as c:
                resp = await c.delete(
                    f"/api/v1/meetings/{mid}/files/{fid}",
                    headers=auth_headers,
                )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_file_not_found(self, client, auth_headers):
        with get_write_connection() as conn:
            mid = create_meeting(
                conn,
                title="Delete Meeting",
                description="",
                file_type="pdf",
                file_name="x.pdf",
                file_path="/tmp/x.pdf",
                user_id="test",
            )
        async with client as c:
            resp = await c.delete(
                f"/api/v1/meetings/{mid}/files/99999",
                headers=auth_headers,
            )
        assert resp.status_code == 404


class TestFileTimeline:
    """Tests for GET /meetings/{id}/files/{fid}/timeline."""

    @pytest.mark.asyncio
    async def test_timeline_audio_segments(self, client, auth_headers, tmp_path):
        """Audio file returns segments timeline."""
        audio_path = tmp_path / "meeting.wav"
        audio_path.write_text("fake audio")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Audio", description="", user_id="test")
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="audio",
                file_name="meeting.wav",
                file_path=str(audio_path),
            )
            save_segments_json(
                conn,
                fid,
                '[{"start":0.0,"end":5.0,"text":"hello","speaker":"A"},'
                '{"start":5.0,"end":10.0,"text":"world","speaker":"B"}]',
            )
            update_meeting_file_status(conn, fid, "ready")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "segments"
        assert len(data["segments"]) == 2
        assert data["total_duration"] == 10.0
        assert data["speaker_count"] == 2

    @pytest.mark.asyncio
    async def test_timeline_pdf_pages(self, client, auth_headers, tmp_path):
        """PDF file returns pages timeline."""
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_text("fake pdf")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="PDF", description="", user_id="test")
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="report.pdf",
                file_path=str(pdf_path),
            )
            update_meeting_file_status(
                conn,
                fid,
                "ready",
                transcript="--- Page 1 ---\nIntro\n--- Page 2 ---\nContent",
            )
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "pages"
        assert len(data["pages"]) == 2
        assert data["page_count"] == 2
        assert data["pages"][0]["page_num"] == 1
        assert data["pages"][1]["page_num"] == 2

    @pytest.mark.asyncio
    async def test_timeline_image(self, client, auth_headers, tmp_path):
        """Image file returns captions timeline with OCR text."""
        img_path = tmp_path / "diagram.png"
        img_path.write_text("fake image")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Image", description="", user_id="test")
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="image",
                file_name="diagram.png",
                file_path=str(img_path),
            )
            update_meeting_file_status(conn, fid, "ready", transcript="OCR extracted text")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "captions"
        assert len(data["captions"]) == 1
        assert data["captions"][0]["ocr_text"] == "OCR extracted text"

    @pytest.mark.asyncio
    async def test_timeline_text_file(self, client, auth_headers, tmp_path):
        """Text file returns text timeline."""
        txt_path = tmp_path / "notes.txt"
        txt_path.write_text("some notes here")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Text", description="", user_id="test")
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="txt",
                file_name="notes.txt",
                file_path=str(txt_path),
            )
            update_meeting_file_status(conn, fid, "ready", transcript="some notes here")
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "text"
        assert data["text"] == "some notes here"
        assert data["word_count"] == 3

    @pytest.mark.asyncio
    async def test_timeline_file_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/99999/files/99999/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_timeline_file_not_ready(self, client, auth_headers, tmp_path):
        pdf_path = tmp_path / "processing.pdf"
        pdf_path.write_text("fake")
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Processing", description="", user_id="test")
            fid = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="processing.pdf",
                file_path=str(pdf_path),
            )
            # Status stays "processing" (default from create_meeting_file)
        async with client as c:
            resp = await c.get(
                f"/api/v1/meetings/{mid}/files/{fid}/timeline",
                headers=auth_headers,
            )
        assert resp.status_code == 400
