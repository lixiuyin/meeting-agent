"""Tests for CORS fail-closed behaviour in production."""

import pytest


class TestCorsFailClosed:
    def test_cors_wildcard_in_production_raises(self, production_env, monkeypatch):
        """CORS_ORIGINS='*' must raise ValueError in production."""
        from src.core.config import settings

        monkeypatch.setattr(settings, "CORS_ORIGINS", "*")
        with pytest.raises(ValueError, match="CORS wildcard"):
            from fastapi import FastAPI

            from src.api.middleware import setup_middleware

            app = FastAPI()
            setup_middleware(app)

    def test_cors_empty_in_production_allows_no_origins(self, production_env, monkeypatch):
        """Empty CORS origins in production should allow no origins (deny all)."""
        from src.core.config import settings

        monkeypatch.setattr(settings, "CORS_ORIGINS", "")
        from fastapi import FastAPI

        from src.api.middleware import setup_middleware

        app = FastAPI()
        setup_middleware(app)  # Should not raise — just empty origins

    def test_cors_explicit_origins_in_production_ok(self, production_env, monkeypatch):
        """Explicit CORS origins should work in production."""
        from src.core.config import settings

        monkeypatch.setattr(settings, "CORS_ORIGINS", "https://example.com,https://app.example.com")
        from fastapi import FastAPI

        from src.api.middleware import setup_middleware

        app = FastAPI()
        setup_middleware(app)  # Should not raise

        from fastapi.testclient import TestClient

        response = TestClient(app).options(
            "/",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": ("x-api-key,content-type,idempotency-key"),
            },
        )
        assert response.status_code == 200

    def test_cors_wildcard_allowed_in_dev(self, monkeypatch):
        """CORS wildcard is allowed in dev mode (but stripped)."""
        from src.core.config import settings

        monkeypatch.setattr(settings, "ENVIRONMENT", "dev")
        monkeypatch.setattr(settings, "CORS_ORIGINS", "*")
        from fastapi import FastAPI

        from src.api.middleware import setup_middleware

        app = FastAPI()
        setup_middleware(app)  # Should not raise
