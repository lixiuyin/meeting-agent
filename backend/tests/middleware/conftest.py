"""Shared fixtures for middleware/infrastructure tests."""

import pytest


@pytest.fixture
def auth_enabled(monkeypatch):
    """Enable API key authentication for the duration of the test."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "API_KEY", settings.API_KEY.__class__("test-api-key"))


@pytest.fixture
def production_env(monkeypatch):
    """Set environment to production mode."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
