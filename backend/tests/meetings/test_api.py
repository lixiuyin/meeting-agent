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
    @pytest.mark.asyncio
    async def test_health(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
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
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "checks" in data
        assert "database" in data["checks"]

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
