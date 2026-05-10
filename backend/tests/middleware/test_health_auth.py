"""Tests for health endpoint authentication."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_with_pepper(monkeypatch):
    """Enable API key auth with a pepper so user_id derivation works."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "API_KEY", SecretStr("test-api-key"))
    monkeypatch.setattr(settings, "PRINCIPAL_PEPPER", SecretStr("test-pepper-for-health-tests"))


class TestHealthAuth:
    def test_index_consistency_requires_auth(self, auth_with_pepper, client):
        """index-consistency endpoint must require API key."""
        resp = client.get("/api/v1/health/index-consistency")
        assert resp.status_code == 401

    def test_index_consistency_with_auth(self, auth_with_pepper, client):
        """index-consistency returns data with valid API key."""
        resp = client.get(
            "/api/v1/health/index-consistency",
            headers={"X-API-Key": "test-api-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "raganything_enabled" in data

    def test_reset_memory_cb_requires_auth(self, auth_with_pepper, client):
        """reset-memory-cb endpoint must require API key."""
        resp = client.post("/api/v1/health/reset-memory-cb")
        assert resp.status_code == 401

    def test_reset_memory_cb_with_auth(self, auth_with_pepper, client):
        """reset-memory-cb resets circuit breaker with valid API key."""
        resp = client.post(
            "/api/v1/health/reset-memory-cb",
            headers={"X-API-Key": "test-api-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_liveness_no_auth_required(self, client):
        """Liveness endpoint should work without auth."""
        resp = client.get("/api/v1/health/live")
        assert resp.status_code == 200

    def test_readiness_no_auth_required(self, client):
        """Readiness endpoint should work without auth."""
        resp = client.get("/api/v1/health/ready")
        # May return 200 or 503 depending on service state
        assert resp.status_code in (200, 503)

    def test_health_no_auth_required(self, client):
        """Legacy health endpoint should work without auth."""
        resp = client.get("/api/v1/health")
        assert resp.status_code in (200, 503)
