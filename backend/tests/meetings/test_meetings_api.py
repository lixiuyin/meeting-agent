"""Tests for meetings API endpoints"""

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMeetingsUpload:
    @pytest.mark.asyncio
    async def test_upload_valid_video(self, client, auth_headers):
        """Upload a valid video file"""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test Video Meeting"},
                files={"file": ("test.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypisom"), "video/mp4")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "meeting_id" in data
        assert "message" in data

    @pytest.mark.asyncio
    async def test_upload_valid_pdf(self, client, auth_headers):
        """Upload a valid PDF file"""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test PDF Meeting", "description": "Test desc"},
                files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4 fcontent"), "application/pdf")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "meeting_id" in data

    @pytest.mark.asyncio
    async def test_upload_valid_image(self, client, auth_headers):
        """Upload a valid image file"""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test Image"},
                files={"file": ("test.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "meeting_id" in data

    @pytest.mark.asyncio
    async def test_upload_missing_title(self, client, auth_headers):
        """Upload without title should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                files={"file": ("test.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp4c"), "video/mp4")},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_missing_file(self, client, auth_headers):
        """Upload without file should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test Meeting"},
            )
        assert resp.status_code == 422


class TestMeetingsList:
    @pytest.mark.asyncio
    async def test_list_meetings_pagination(self, client, auth_headers):
        """Test meeting list pagination"""
        async with client as c:
            resp = await c.get("/api/v1/meetings?limit=5&offset=0", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "meetings" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_list_meetings_with_status_filter(self, client, auth_headers):
        """Test meeting list with status filter"""
        async with client as c:
            resp = await c.get("/api/v1/meetings?status=ready", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "meetings" in data
        # All returned meetings should have status "ready"
        for m in data["meetings"]:
            assert m["status"] == "ready"

    @pytest.mark.asyncio
    async def test_list_meetings_invalid_limit(self, client, auth_headers):
        """Test meeting list with invalid limit"""
        async with client as c:
            resp = await c.get("/api/v1/meetings?limit=200", headers=auth_headers)
        assert resp.status_code == 422


class TestMeetingsGet:
    @pytest.mark.asyncio
    async def test_get_meeting_success(self, client, auth_headers):
        """Get an existing meeting"""
        # First create a meeting
        async with client as c:
            upload_resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test Meeting"},
                files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )
            meeting_id = upload_resp.json()["meeting_id"]

            # Then get it
            resp = await c.get(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == meeting_id
        assert data["title"] == "Test Meeting"


class TestMeetingsDelete:
    @pytest.mark.asyncio
    async def test_delete_meeting_success(self, client, auth_headers):
        """Delete an existing meeting"""
        async with client as c:
            # First create a meeting
            upload_resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Meeting to Delete"},
                files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )
            meeting_id = upload_resp.json()["meeting_id"]

            # Then delete it
            resp = await c.delete(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 200

            # Verify it's gone (same client context)
            resp = await c.get(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_meeting_with_missing_file(self, client, auth_headers):
        async with client as c:
            upload_resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Meeting Missing File"},
                files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )
            meeting_id = upload_resp.json()["meeting_id"]

            with db.get_connection() as conn:
                files = db.list_meeting_files(conn, meeting_id)
            assert files
            Path(files[0]["file_path"]).unlink(missing_ok=True)

            resp = await c.delete(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 200

            resp = await c.get(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_meeting_with_chroma_error_keeps_db(self, client, auth_headers):
        async with client as c:
            upload_resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Meeting Chroma Error"},
                files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )
            meeting_id = upload_resp.json()["meeting_id"]

            with patch(
                "src.services.rag.delete_meeting_chunks",
                side_effect=RuntimeError("chroma down"),
            ):
                resp = await c.delete(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 200

            resp = await c.get(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
            assert resp.status_code == 404

            with db.get_connection() as conn:
                pending = conn.execute(
                    "SELECT collection, embedding_id FROM pending_vector_deletions "
                    "WHERE collection='meeting' AND embedding_id=?",
                    (str(meeting_id),),
                ).fetchall()
            assert pending
