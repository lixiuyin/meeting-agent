"""Tests for API endpoints"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestHealthEndpoint:
    def test_job_health_exposes_ambiguous_idempotency_commits(self, monkeypatch):
        from src.api.routers import health as health_router
        from src.core import database as db

        monkeypatch.setattr(health_router.settings, "DURABLE_JOB_EXECUTION_MODE", "embedded")
        with db.get_write_connection() as conn:
            conn.execute(
                "INSERT INTO idempotency_keys "
                "(key,method,path,user_id,response_body,expires_at,lifecycle_state) "
                "VALUES ('ambiguous','POST','/x','alice','opaque',"
                "datetime('now','+1 hour'),'effects_committed')"
            )

        result = health_router._check_durable_jobs()

        assert result.status == "degraded"
        assert result.counts["idempotency_effects_committed"] == 1
        assert result.counts["idempotency_effects_committed_expired"] == 0

    def test_job_health_degrades_for_legacy_unknown_idempotency(self, monkeypatch):
        from src.api.routers import health as health_router
        from src.core import database as db

        monkeypatch.setattr(health_router.settings, "DURABLE_JOB_EXECUTION_MODE", "embedded")
        with db.get_write_connection() as conn:
            conn.execute(
                "INSERT INTO idempotency_keys "
                "(key,method,path,user_id,response_body,expires_at,lifecycle_state) "
                "VALUES ('legacy','POST','/x','alice','opaque',"
                "datetime('now','-1 hour'),'legacy_unknown')"
            )

        result = health_router._check_durable_jobs()

        assert result.status == "degraded"
        assert result.counts["idempotency_legacy_unknown"] == 1
        assert result.counts["idempotency_legacy_unknown_expired"] == 1

    def test_dead_letters_do_not_make_job_execution_unready(self, monkeypatch):
        from src.api.routers import health as health_router

        monkeypatch.setattr(
            health_router,
            "_check_durable_jobs",
            lambda: health_router.JobHealthResponse(
                status="degraded",
                execution_mode="embedded",
                workers_online=False,
                counts={"pending": 0, "dead_letter": 2},
            ),
        )

        assert health_router._check_job_execution() == ("ok", "")

    def test_pending_jobs_without_executor_make_readiness_fail(self, monkeypatch):
        from src.api.routers import health as health_router

        monkeypatch.setattr(
            health_router,
            "_check_durable_jobs",
            lambda: health_router.JobHealthResponse(
                status="degraded",
                execution_mode="embedded",
                workers_online=False,
                counts={"pending": 1, "dead_letter": 0},
            ),
        )

        status, detail = health_router._check_job_execution()
        assert status == "error"
        assert "workers_online=False" in detail

    def test_expired_running_job_without_executor_make_readiness_fail(self, monkeypatch):
        from src.api.routers import health as health_router

        monkeypatch.setattr(
            health_router,
            "_check_durable_jobs",
            lambda: health_router.JobHealthResponse(
                status="degraded",
                execution_mode="embedded",
                workers_online=False,
                counts={"pending": 0, "expired_running": 1},
            ),
        )

        status, detail = health_router._check_job_execution()
        assert status == "error"
        assert "expired_running" in detail

    @pytest.mark.asyncio
    async def test_health(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health")
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert resp.status_code == (200 if data["status"] == "ok" else 503)
        assert "checks" in data
        assert data["checks"]["database"] == "ok"

    @pytest.mark.asyncio
    async def test_metrics(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/metrics", headers=auth_headers)
        assert resp.status_code == 200
        # Prometheus text exposition format
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "# HELP" in body or "# TYPE" in body

    @pytest.mark.asyncio
    async def test_liveness(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"

    @pytest.mark.asyncio
    async def test_readiness(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health/ready")
        data = resp.json()
        assert data["status"] == "ok"
        assert resp.status_code == 200
        assert "checks" in data
        assert "database" in data["checks"]
        assert set(data["checks"]) == {
            "startup",
            "database",
            "fts5",
            "job_queue",
            "job_execution",
            "native_index",
            "storage",
        }
        assert set(data["checks"].values()) == {"ok"}

    @pytest.mark.asyncio
    async def test_degraded_readiness_returns_503(self, client, monkeypatch):
        from src.api.routers import health as health_router

        async def degraded():
            return health_router.HealthResponse(
                status="degraded",
                checks={"database": "error"},
                details={"database": "unavailable"},
            )

        monkeypatch.setattr(health_router, "_check_readiness", degraded)
        async with client as c:
            resp = await c.get("/api/v1/health/ready")

        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "5"

    @pytest.mark.asyncio
    async def test_traffic_health(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/health/traffic", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "breaker_state" in data

    @pytest.mark.asyncio
    async def test_index_consistency_health(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health/index-consistency")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "raganything_enabled" in data
        assert "total_ready_files" in data
        assert "missing_chroma_indexed" in data
        assert "missing_raganything_doc_id" in data
        assert "stale_raganything_doc_id" in data

    def test_native_index_consistency_is_checked_when_raganything_disabled(self, monkeypatch):
        from src.api.routers import health as health_router
        from src.core import database as db

        monkeypatch.setattr(health_router.settings, "RAGANYTHING_ENABLED", False)
        with db.get_write_connection() as conn:
            meeting_id = db.create_meeting(conn, title="Native index check", user_id="test-user")
            file_id = db.create_meeting_file(
                conn,
                meeting_id=meeting_id,
                file_type="txt",
                file_name="notes.txt",
                file_path="/tmp/notes.txt",
                user_id="test-user",
            )
            db.update_meeting_file_status(conn, file_id, "ready")

        result = health_router._check_index_consistency()
        assert result.status == "degraded"
        assert result.raganything_enabled is False
        assert result.total_ready_files == 1
        assert result.missing_chroma_indexed == 1
        assert result.missing_raganything_doc_id == 0
        assert result.stale_raganything_doc_id == 0

        status, detail = health_router._check_native_index_readiness()
        assert status == "error"
        assert "config_mismatch=1" in detail

    def test_failed_source_file_does_not_make_the_service_unready(self, monkeypatch):
        from src.api.routers import health as health_router
        from src.core import database as db

        monkeypatch.setattr(health_router.settings, "RAGANYTHING_ENABLED", False)
        with db.get_write_connection() as conn:
            meeting_id = db.create_meeting(conn, title="Failed source", user_id="test-user")
            file_id = db.create_meeting_file(
                conn,
                meeting_id=meeting_id,
                file_type="txt",
                file_name="missing.txt",
                file_path="/tmp/missing.txt",
                user_id="test-user",
            )
            db.update_meeting_file_status(conn, file_id, "error", error_message="missing")
            db.mark_native_index_failed(
                conn,
                file_id=file_id,
                meeting_id=meeting_id,
                error="source missing",
            )

        result = health_router._check_index_consistency()
        assert result.failed_native_indexes == 0
        assert result.repair_pending_indexes == 0
        assert health_router._check_native_index_readiness() == ("ok", "")


class TestMeetingsEndpoint:
    @pytest.mark.asyncio
    async def test_list_meetings_empty(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["meetings"] == []

    @pytest.mark.asyncio
    async def test_upload_unsupported_format(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/meetings/upload",
                headers=auth_headers,
                data={"title": "Test"},
                files={"file": ("test.exe", b"binary", "application/octet-stream")},
            )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_missing_api_key(self, client):
        from unittest.mock import patch

        from pydantic import SecretStr

        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret")
            async with client as c:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    data={"title": "Test"},
                    files={"file": ("test.pdf", b"content", "application/pdf")},
                )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_meeting_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/meetings/999", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_meeting_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.delete("/api/v1/meetings/999", headers=auth_headers)
        assert resp.status_code == 404


class TestSessionsEndpoint:
    @pytest.mark.asyncio
    async def test_list_sessions(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.delete("/api/v1/sessions/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


class TestMemoryEndpoint:
    @pytest.mark.asyncio
    async def test_list_memories_empty(self, client, auth_headers):
        async with client as c:
            # Clear any leftover memories from previous tests
            resp = await c.get("/api/v1/memory", headers=auth_headers)
            for m in resp.json().get("memories", []):
                await c.delete(
                    f"/api/v1/memory?key={m['key']}&user_id=default", headers=auth_headers
                )
            # Now check empty
            resp = await c.get("/api/v1/memory", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["memories"] == []

    @pytest.mark.asyncio
    async def test_set_and_get_memory(self, client, auth_headers):
        async with client as c:
            # Set
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "test_key", "value": "test_value"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["key"] == "test_key"
            assert data["value"] == "test_value"

            # Verify it shows in list (same client session)
            resp = await c.get("/api/v1/memory", headers=auth_headers)
            memories = resp.json()["memories"]
            assert any(m["key"] == "test_key" for m in memories)

    @pytest.mark.asyncio
    async def test_set_memory_missing_fields(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "test_key"},  # missing value
            )
        assert resp.status_code == 422  # validation error
