"""Tests for Marker API provider (mocked HTTP)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.parser._errors import FailureReason, ParserProviderError  # noqa: E402
from src.services.parser._profile import DocumentProfile  # noqa: E402
from src.services.parser.providers.marker_api import MarkerAPIParser  # noqa: E402


def _make_profile(**kwargs) -> DocumentProfile:
    defaults = {
        "suffix": ".pdf",
        "page_count": 1,
        "avg_chars_per_page": 100.0,
        "image_ratio": 0.0,
        "has_text_layer": True,
        "is_likely_scanned": False,
        "size_bytes": 5000,
    }
    defaults.update(kwargs)
    return DocumentProfile(**defaults)


@pytest.fixture
def pdf_file(tmp_path):
    import fitz

    path = tmp_path / "test.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


class TestMarkerAPISuccess:
    """Test successful Marker API flows."""

    @pytest.mark.asyncio
    async def test_direct_response(self, pdf_file):
        """Small file returns result directly (no polling)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "markdown": "Test content from Marker",
            "metadata": {"page_count": 1},
            "images": {},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        parser = MarkerAPIParser()
        with patch.object(parser, "_map_response") as mock_map:
            mock_map.return_value = MagicMock()
            with (
                patch(
                    "src.services.parser.providers.marker_api.get_parser_http_client",
                    return_value=mock_client,
                ),
                patch("src.services.parser.providers.marker_api.settings") as mock_settings,
            ):
                mock_settings.MARKER_API_KEY.get_secret_value.return_value = "test-key"
                mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"
                mock_settings.OCR_LANGUAGE = "en"
                mock_settings.MARKER_MAX_WAIT_SECONDS = 60

                profile = _make_profile()
                await parser.parse(pdf_file, profile)
                mock_map.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_based_flow(self, pdf_file):
        """File with check_url triggers polling."""
        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.json.return_value = {
            "request_id": "test-123",
            "request_check_url": "https://www.datalab.to/api/v1/check/test-123",
        }

        poll_resp_1 = MagicMock()
        poll_resp_1.status_code = 200
        poll_resp_1.json.return_value = {"status": "processing"}

        poll_resp_2 = MagicMock()
        poll_resp_2.status_code = 200
        poll_resp_2.json.return_value = {
            "status": "complete",
            "markdown": "Final content",
            "metadata": {"page_count": 1},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.side_effect = [poll_resp_1, poll_resp_2]

        parser = MarkerAPIParser()
        with (
            patch(
                "src.services.parser.providers.marker_api.get_parser_http_client",
                return_value=mock_client,
            ),
            patch("src.services.parser.providers.marker_api.settings") as mock_settings,
        ):
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = "test-key"
            mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"
            mock_settings.OCR_LANGUAGE = "en"
            mock_settings.MARKER_MAX_WAIT_SECONDS = 60
            mock_settings.PARSER_POLL_INTERVAL_SECONDS = 0.01

            profile = _make_profile()
            result = await parser.parse(pdf_file, profile)
            assert result is not None
            assert mock_client.get.await_count == 2


class TestMarkerAPIErrors:
    """Test error handling in Marker API provider."""

    @pytest.mark.asyncio
    async def test_no_api_key(self, pdf_file):
        """Missing API key raises non-retryable error."""
        parser = MarkerAPIParser()
        with patch("src.services.parser.providers.marker_api.settings") as mock_settings:
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = ""

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert not exc_info.value.retryable
            assert exc_info.value.reason == FailureReason.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_rate_limited(self, pdf_file):
        """429 response raises non-retryable rate-limited error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = '{"detail":"rate limited"}'

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        parser = MarkerAPIParser()
        with (
            patch(
                "src.services.parser.providers.marker_api.get_parser_http_client",
                return_value=mock_client,
            ),
            patch("src.services.parser.providers.marker_api.settings") as mock_settings,
        ):
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = "test-key"
            mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"
            mock_settings.OCR_LANGUAGE = "en"

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert not exc_info.value.retryable
            assert exc_info.value.reason == FailureReason.RATE_LIMITED

    @pytest.mark.asyncio
    async def test_unauthorized(self, pdf_file):
        """401 response raises non-retryable error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        parser = MarkerAPIParser()
        with (
            patch(
                "src.services.parser.providers.marker_api.get_parser_http_client",
                return_value=mock_client,
            ),
            patch("src.services.parser.providers.marker_api.settings") as mock_settings,
        ):
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = "bad-key"
            mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"
            mock_settings.OCR_LANGUAGE = "en"

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_server_error_retryable(self, pdf_file):
        """500 response raises retryable error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 502

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        parser = MarkerAPIParser()
        with (
            patch(
                "src.services.parser.providers.marker_api.get_parser_http_client",
                return_value=mock_client,
            ),
            patch("src.services.parser.providers.marker_api.settings") as mock_settings,
        ):
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = "test-key"
            mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"
            mock_settings.OCR_LANGUAGE = "en"

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert exc_info.value.retryable
