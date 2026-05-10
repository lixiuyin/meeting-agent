"""Tests for generic exception handler in main.py.

Covers the fix: settings.DEBUG → settings.ENVIRONMENT == "dev"
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.config import settings


class TestGenericExceptionHandler:
    """Verify the generic exception handler uses settings.ENVIRONMENT, not settings.DEBUG."""

    def test_no_settings_debug_attribute(self):
        """settings object should not have a DEBUG attribute (the old code path)."""
        assert not hasattr(settings, "DEBUG"), (
            "Settings should not have DEBUG attribute; use ENVIRONMENT instead"
        )

    @pytest.mark.asyncio
    async def test_dev_env_includes_details(self):
        """In dev environment, unhandled exceptions include error type details."""
        from src.main import generic_exception_handler

        request = MagicMock()
        request.state.request_id = "test-req-123"

        with patch.object(settings, "ENVIRONMENT", "dev"):
            response = await generic_exception_handler(request, RuntimeError("boom"))

        assert response.status_code == 500
        import json

        data = json.loads(response.body)
        assert data["code"] == "INTERNAL_ERROR"
        assert data["request_id"] == "test-req-123"
        assert data.get("details", {}).get("type") == "RuntimeError"

    @pytest.mark.asyncio
    async def test_prod_env_hides_details(self):
        """In prod environment, unhandled exceptions hide error type details."""
        from src.main import generic_exception_handler

        request = MagicMock()
        request.state.request_id = "test-req-456"

        with patch.object(settings, "ENVIRONMENT", "prod"):
            response = await generic_exception_handler(request, ValueError("secret"))

        import json

        data = json.loads(response.body)
        assert data["code"] == "INTERNAL_ERROR"
        assert data["request_id"] == "test-req-456"
        # In prod mode, details should be None
        assert data.get("details") is None

    @pytest.mark.asyncio
    async def test_staging_env_hides_details(self):
        """In staging environment, details should also be hidden."""
        from src.main import generic_exception_handler

        request = MagicMock()
        request.state.request_id = "test-req-789"

        with patch.object(settings, "ENVIRONMENT", "staging"):
            response = await generic_exception_handler(request, TypeError("oops"))

        import json

        data = json.loads(response.body)
        assert data.get("details") is None

    @pytest.mark.asyncio
    async def test_response_has_detail_field(self):
        """Response includes backward-compatible detail field."""
        from src.main import generic_exception_handler

        request = MagicMock()
        request.state.request_id = "test"

        response = await generic_exception_handler(request, Exception("err"))

        import json

        data = json.loads(response.body)
        assert data["detail"] == data["message"]
