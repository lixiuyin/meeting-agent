"""Tests for WebSocket token authentication."""

import time

from src.api.routers.websocket import (
    _generate_ws_token,
    _validate_ws_token,
    _verify_ws_auth,
)


class TestWsTokenAuth:
    def test_generate_token_contains_expiry(self):
        """Generated token should contain a dot-separated expiry timestamp."""
        token = _generate_ws_token(user_id="test")
        assert "." in token
        parts = token.rsplit(".", 1)
        expiry_str = parts[1]
        expiry = int(expiry_str)
        assert expiry > int(time.time())

    def test_validate_valid_token(self):
        """A freshly generated token should validate successfully."""
        token = _generate_ws_token(user_id="default")
        assert _validate_ws_token(token, user_id="default") is True

    def test_validate_expired_token(self, monkeypatch):
        """An expired token should fail validation."""
        token = _generate_ws_token(user_id="default")
        # Advance time past TTL (save real time before patching)
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 600)
        assert _validate_ws_token(token, user_id="default") is False

    def test_validate_tampered_token(self):
        """A tampered token should fail validation."""
        token = _generate_ws_token(user_id="default")
        tampered = "AAAA" + token[4:]
        assert _validate_ws_token(tampered, user_id="default") is False

    def test_validate_empty_token(self):
        """Empty or None token should fail."""
        assert _validate_ws_token("", user_id="default") is False

    def test_validate_token_no_dot(self):
        """Token without dot separator should fail."""
        assert _validate_ws_token("nodot", user_id="default") is False

    def test_verify_ws_auth_dev_mode(self, monkeypatch):
        """Dev mode should allow access without credentials."""
        from pydantic import SecretStr

        from src.core.config import settings

        monkeypatch.setattr(settings, "API_KEY", SecretStr(""))
        valid, uid = _verify_ws_auth(api_key=None, token=None)
        assert valid is True
        assert uid == "default"

    def test_verify_ws_auth_invalid_in_prod(self, monkeypatch):
        """Production mode should reject invalid credentials."""
        from pydantic import SecretStr

        from src.core.config import settings

        monkeypatch.setattr(settings, "API_KEY", SecretStr("prod-secret"))
        valid, uid = _verify_ws_auth(api_key="wrong", token=None)
        assert valid is False
