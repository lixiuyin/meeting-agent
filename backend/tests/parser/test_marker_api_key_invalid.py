"""Test Marker API returns clear error on invalid/revoked API key (401/403)."""

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

from src.services.parser._errors import ParserProviderError  # noqa: E402
from src.services.parser._profile import DocumentProfile  # noqa: E402
from src.services.parser.providers.marker_api import MarkerAPIParser  # noqa: E402


def _make_profile() -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=1,
        avg_chars_per_page=100.0,
        image_ratio=0.0,
        has_text_layer=True,
        is_likely_scanned=False,
        size_bytes=5000,
    )


@pytest.fixture
def pdf_file(tmp_path):
    import pymupdf as fitz

    path = tmp_path / "test.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


class TestMarkerAPIKeyInvalid:
    """401/403 responses should be non-retryable and log a clear message."""

    @pytest.mark.asyncio
    async def test_403_api_key_revoked(self, pdf_file, caplog):
        mock_resp = MagicMock()
        mock_resp.status_code = 403

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
            mock_settings.MARKER_API_KEY.get_secret_value.return_value = "revoked-key"
            mock_settings.MARKER_BASE_URL = "https://www.datalab.to/api/v1/marker"

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert not exc_info.value.retryable
            assert "API key invalid" in str(exc_info.value.cause)

    @pytest.mark.asyncio
    async def test_401_api_key_invalid(self, pdf_file, caplog):
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

            with pytest.raises(ParserProviderError) as exc_info:
                await parser.parse(pdf_file, _make_profile())
            assert not exc_info.value.retryable
            assert "API key invalid" in str(exc_info.value.cause)
