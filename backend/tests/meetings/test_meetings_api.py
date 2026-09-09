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
    async def test_file_semantics_are_editable_and_queue_reindex(self, client, auth_headers):
        with patch("src.services.processor.schedule_meeting_file_processing") as schedule:
            async with client as c:
                uploaded = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Reviewed decisions"},
                    files={"file": ("notes.txt", io.BytesIO(b"Approved Orbit."), "text/plain")},
                )
                assert uploaded.status_code == 200
                body = uploaded.json()
                response = await c.patch(
                    f"/api/v1/meetings/{body['meeting_id']}/files/{body['file_id']}/semantics",
                    headers=auth_headers,
                    json={
                        "material_role": "decision_log",
                        "approval_status": "approved",
                        "business_domain": "meeting",
                    },
                )

        assert response.status_code == 200
        assert response.json()["material_role"] == "decision_log"
        assert response.json()["approval_status"] == "approved"
        with db.get_connection() as conn:
            stored = db.get_meeting_file(conn, body["file_id"], user_id="default")
            queued = conn.execute(
                "SELECT payload_json, status FROM durable_jobs "
                "WHERE kind='file_processing' AND dedupe_key=?",
                (f"file:{body['file_id']}",),
            ).fetchone()
            history = db.list_meeting_file_semantic_events(conn, body["file_id"], user_id="default")
        assert stored is not None
        assert stored["material_role"] == "decision_log"
        assert stored["approval_status"] == "approved"
        assert stored["source_revision"] == 2
        assert queued is not None
        queued_payload = __import__("json").loads(queued["payload_json"])
        assert queued_payload["force_native_reindex"] is True
        assert queued_payload["source_revision"] == 2
        assert history[0]["approval_status"] == "approved"
        assert history[0]["business_domain"] == "meeting"
        assert response.json()["business_domain"] == "meeting"

    @pytest.mark.asyncio
    async def test_rejection_requires_reason_and_exposes_review_history(self, client, auth_headers):
        with patch("src.services.processor.schedule_meeting_file_processing"):
            async with client as c:
                uploaded = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Evidence review"},
                    files={"file": ("draft.txt", io.BytesIO(b"Draft proposal"), "text/plain")},
                )
                body = uploaded.json()
                missing_reason = await c.patch(
                    f"/api/v1/meetings/{body['meeting_id']}/files/{body['file_id']}/semantics",
                    headers=auth_headers,
                    json={"approval_status": "rejected"},
                )
                rejected = await c.patch(
                    f"/api/v1/meetings/{body['meeting_id']}/files/{body['file_id']}/semantics",
                    headers=auth_headers,
                    json={
                        "material_role": "agenda",
                        "approval_status": "rejected",
                        "approval_reason": "Proposal was superseded",
                    },
                )
                history = await c.get(
                    f"/api/v1/meetings/{body['meeting_id']}/files/{body['file_id']}"
                    "/semantics/history",
                    headers=auth_headers,
                )

        assert missing_reason.status_code == 422
        assert rejected.status_code == 200
        assert rejected.json()["approval_reason"] == "Proposal was superseded"
        assert history.status_code == 200
        assert history.json()[0]["approval_status"] == "rejected"
        assert history.json()[0]["approval_reason"] == "Proposal was superseded"

    @pytest.mark.asyncio
    async def test_multipart_idempotency_key_replays_without_stream_consumed(
        self, client, auth_headers
    ):
        headers = {**auth_headers, "Idempotency-Key": "upload-replay-1"}
        payload = b"%PDF-1.4 idempotent"
        with patch("src.services.processor.schedule_meeting_file_processing"):
            async with client as c:
                first = await c.post(
                    "/api/v1/meetings/upload",
                    headers=headers,
                    data={"title": "Idempotent upload"},
                    files={"file": ("same.pdf", io.BytesIO(payload), "application/pdf")},
                )
                second = await c.post(
                    "/api/v1/meetings/upload",
                    headers=headers,
                    data={"title": "Idempotent upload"},
                    files={"file": ("same.pdf", io.BytesIO(payload), "application/pdf")},
                )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == first.json()

    @pytest.mark.asyncio
    async def test_upload_failure_rolls_back_file_and_new_meeting(self, client, auth_headers):
        with (
            patch("src.services.processor.schedule_meeting_file_processing"),
            patch(
                "src.api.dependencies.IdempotencyGuard.save",
                side_effect=RuntimeError("idempotency store unavailable"),
            ),
        ):
            async with client as c:
                response = await c.post(
                    "/api/v1/meetings/upload",
                    headers={**auth_headers, "Idempotency-Key": "upload-rollback-1"},
                    data={"title": "Must roll back"},
                    files={"file": ("rollback.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                )

        assert response.status_code == 500
        with db.get_connection() as conn:
            assert db.count_meetings(conn, user_id="default") == 0
        from src.core.config import settings

        assert not list(settings.UPLOAD_DIR.glob(".upload-*"))
        assert not list(settings.UPLOAD_DIR.glob("*_rollback.pdf"))

    @pytest.mark.asyncio
    async def test_new_meeting_and_first_file_reservation_are_atomic(self, client, auth_headers):
        with patch(
            "src.api.routers.meetings._upload.db.create_meeting_file_if_absent",
            side_effect=RuntimeError("file reservation failed"),
        ):
            async with client as c:
                response = await c.post(
                    "/api/v1/meetings/upload",
                    headers={**auth_headers, "Idempotency-Key": "upload-atomic-meeting"},
                    data={"title": "Must not leave an empty meeting"},
                    files={"file": ("atomic.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                )

        assert response.status_code == 500
        with db.get_connection() as conn:
            assert db.count_meetings(conn, user_id="default") == 0

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
        """URL-encoded upload requests are rejected before unbounded form parsing."""
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test Meeting"},
            )
        assert resp.status_code == 415


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
