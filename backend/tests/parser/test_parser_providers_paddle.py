"""Tests for PaddleOCR API parser provider."""

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
from src.services.parser.providers.paddle_api import (  # noqa: E402
    PaddleOCRAPIParser,
)


def _make_scanned_profile() -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=2,
        avg_chars_per_page=0.0,
        image_ratio=1.0,
        has_text_layer=False,
        is_likely_scanned=True,
        size_bytes=50000,
    )


@pytest.fixture
def parser():
    return PaddleOCRAPIParser()


@pytest.fixture
def sample_image(tmp_path):
    """Create a minimal PNG file (1x1 pixel)."""
    import struct
    import zlib

    path = tmp_path / "test.png"
    # Minimal valid PNG: 1x1 pixel, RGBA
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

    raw = zlib.compress(b"\x00\x00\x00\x00\x00")
    idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)

    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    path.write_bytes(signature + ihdr + idat + iend)
    return path


class TestPaddleMissingConfig:
    """Provider should fail fast when config is missing."""

    @patch("src.services.parser.providers.paddle_api.settings")
    async def test_raises_when_no_api_key(self, mock_settings, parser, sample_image):
        mock_settings.PADDLEOCR_API_KEY = MagicMock()
        mock_settings.PADDLEOCR_API_KEY.get_secret_value.return_value = ""
        mock_settings.PADDLEOCR_BASE_URL = "https://example.com"

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_image, profile)
        assert exc_info.value.provider == "paddle"
        assert not exc_info.value.retryable

    @patch("src.services.parser.providers.paddle_api.settings")
    async def test_raises_when_no_base_url(self, mock_settings, parser, sample_image):
        mock_settings.PADDLEOCR_API_KEY = MagicMock()
        mock_settings.PADDLEOCR_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.PADDLEOCR_BASE_URL = ""

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_image, profile)
        assert not exc_info.value.retryable


class TestPaddleUnsupportedFormat:
    """Provider should reject unsupported formats."""

    @patch("src.services.parser.providers.paddle_api.settings")
    async def test_unsupported_format(self, mock_settings, parser, tmp_path):
        mock_settings.PADDLEOCR_API_KEY = MagicMock()
        mock_settings.PADDLEOCR_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.PADDLEOCR_BASE_URL = "https://example.com"

        unsupported = tmp_path / "test.txt"
        unsupported.write_text("hello")

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(unsupported, profile)
        assert "Unsupported format" in str(exc_info.value)


class TestPaddleApiErrors:
    """Test API error handling."""

    @patch("src.services.parser.providers.paddle_api.get_parser_http_client")
    @patch("src.services.parser.providers.paddle_api.settings")
    async def test_401_unauthorized(self, mock_settings, mock_client_fn, parser, sample_image):
        mock_settings.PADDLEOCR_API_KEY = MagicMock()
        mock_settings.PADDLEOCR_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.PADDLEOCR_BASE_URL = "https://example.com"

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_image, profile)
        assert not exc_info.value.retryable

    @patch("src.services.parser.providers.paddle_api.get_parser_http_client")
    @patch("src.services.parser.providers.paddle_api.settings")
    async def test_500_server_error_retryable(
        self, mock_settings, mock_client_fn, parser, sample_image
    ):
        mock_settings.PADDLEOCR_API_KEY = MagicMock()
        mock_settings.PADDLEOCR_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.PADDLEOCR_BASE_URL = "https://example.com"

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_image, profile)
        assert exc_info.value.retryable


class TestPaddleBlocksToText:
    """Test _blocks_to_text helper."""

    def test_text_blocks(self, parser):
        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]
        text, tables = parser._blocks_to_text(blocks)
        assert "Hello" in text
        assert "World" in text
        assert tables == []

    def test_title_blocks(self, parser):
        blocks = [{"type": "title", "text": "Chapter 1"}]
        text, tables = parser._blocks_to_text(blocks)
        assert "# Chapter 1" in text

    def test_table_block_with_rows(self, parser):
        blocks = [
            {
                "type": "table",
                "text": "table data",
                "rows": [["A", "B"], ["C", "D"]],
            }
        ]
        text, tables = parser._blocks_to_text(blocks)
        assert "[TABLE]" in text
        assert len(tables) == 1
        assert tables[0] == [["A", "B"], ["C", "D"]]

    def test_table_block_with_dict_rows(self, parser):
        blocks = [
            {
                "type": "table",
                "text": "table data",
                "rows": [{"text": "cell1"}, {"text": "cell2"}],
            }
        ]
        text, tables = parser._blocks_to_text(blocks)
        assert "[TABLE]" in text

    def test_table_block_text_fallback(self, parser):
        blocks = [
            {
                "type": "table",
                "text": "Table content as plain text",
            }
        ]
        text, tables = parser._blocks_to_text(blocks)
        assert "[TABLE]" in text
        assert "Table content as plain text" in text

    def test_figure_block(self, parser):
        blocks = [{"type": "figure", "text": "Fig description"}]
        text, tables = parser._blocks_to_text(blocks)
        assert "[FIGURE]" in text

    def test_empty_blocks(self, parser):
        blocks = []
        text, tables = parser._blocks_to_text(blocks)
        assert text == ""
        assert tables == []

    def test_empty_content_skipped(self, parser):
        blocks = [{"type": "text", "text": ""}]
        text, tables = parser._blocks_to_text(blocks)
        assert text == ""
