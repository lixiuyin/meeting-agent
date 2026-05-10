"""Tests for security module"""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from src.core.security import _derive_user_id_from_api_key, verify_api_key

_TEST_PEPPER = SecretStr("test-pepper-for-unit-tests")


def _fake_request():
    """Create a minimal Request-like object for dependency injection tests."""
    from starlette.requests import Request

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/"}
    return Request(scope)


class TestApiKeyAuth:
    @pytest.mark.asyncio
    async def test_no_api_key_configured(self):
        """When API_KEY is empty, authentication is skipped"""
        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("")
            result = await verify_api_key(request=_fake_request(), x_api_key=None)
        assert result == {"user_id": "default"}

    @pytest.mark.asyncio
    async def test_valid_api_key(self):
        """Valid API key passes authentication"""
        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret123")
            mock_settings.PRINCIPAL_PEPPER = _TEST_PEPPER
            result = await verify_api_key(request=_fake_request(), x_api_key="secret123")
            expected_id = _derive_user_id_from_api_key("secret123")
        assert result == {"user_id": expected_id}

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        """Invalid API key raises 401"""
        from fastapi import HTTPException

        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret123")
            mock_settings.PRINCIPAL_PEPPER = _TEST_PEPPER
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request=_fake_request(), x_api_key="wrong")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        """Missing API key raises 401 when configured"""
        from fastapi import HTTPException

        with patch("src.core.security.settings") as mock_settings:
            mock_settings.API_KEY = SecretStr("secret123")
            mock_settings.PRINCIPAL_PEPPER = _TEST_PEPPER
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request=_fake_request(), x_api_key=None)
            assert exc_info.value.status_code == 401
