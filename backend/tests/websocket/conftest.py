"""Shared fixtures for WebSocket tests."""

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def ws_client():
    """Synchronous test client for WebSocket connections."""
    return TestClient(app)


@pytest.fixture
def auth_enabled(monkeypatch):
    """Enable API key authentication for WebSocket tests."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "API_KEY", settings.API_KEY.__class__("test-api-key"))
