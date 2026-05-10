"""Tests for the MinerU v4 batch-upload API parser provider."""

import io
import json
import os
import tempfile
import zipfile
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
from src.services.parser.providers.mineru_api import (  # noqa: E402
    MinerUAPIParser,
)


def _make_scanned_profile() -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=3,
        avg_chars_per_page=0.0,
        image_ratio=1.0,
        has_text_layer=False,
        is_likely_scanned=True,
        size_bytes=50000,
    )


def _make_text_profile() -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=5,
        avg_chars_per_page=500.0,
        image_ratio=0.0,
        has_text_layer=True,
        is_likely_scanned=False,
        size_bytes=10000,
    )


@pytest.fixture
def parser():
    return MinerUAPIParser()


@pytest.fixture
def sample_pdf(tmp_path):
    import fitz

    path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Test content")
    doc.save(str(path))
    doc.close()
    return path


def _ok(json_payload: dict, status: int = 200) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_payload
    resp.text = json.dumps(json_payload)
    return resp


def _binary_response(content: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.text = "<binary>"
    return resp


def _zip_with_results(markdown: str, content_list: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("full.md", markdown)
        zf.writestr(
            "content_list.json",
            json.dumps(content_list, ensure_ascii=False),
        )
    return buf.getvalue()


# ── Configuration / preconditions ────────────────────────────────────────


class TestApiRoot:
    """``_api_root`` accepts the historical paths users may have configured."""

    @patch("src.services.parser.providers.mineru_api.settings")
    def test_strips_legacy_extract_task_suffix(self, mock_settings, parser):
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4/extract/task"
        assert parser._api_root() == "https://mineru.net/api/v4"

    @patch("src.services.parser.providers.mineru_api.settings")
    def test_returns_root_unchanged(self, mock_settings, parser):
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"
        assert parser._api_root() == "https://mineru.net/api/v4"

    @patch("src.services.parser.providers.mineru_api.settings")
    def test_strips_file_urls_batch_suffix(self, mock_settings, parser):
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4/file-urls/batch"
        assert parser._api_root() == "https://mineru.net/api/v4"


class TestMinerUMissingApiKey:
    """Provider should fail fast when API key is missing."""

    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_raises_when_no_api_key(self, mock_settings, parser, sample_pdf):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = ""
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"

        profile = _make_scanned_profile()
        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, profile)
        assert exc_info.value.provider == "mineru"
        assert not exc_info.value.retryable
        assert exc_info.value.reason == FailureReason.AUTH_FAILED


# ── Auth + transport failures during batch creation ──────────────────────


class TestBatchCreateFailure:
    """``/file-urls/batch`` failures map to typed parser errors."""

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_401_unauthorized(self, mock_settings, mock_client_fn, parser, sample_pdf):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"

        mock_client = AsyncMock()
        mock_client.post.return_value = _ok({}, status=401)
        mock_client_fn.return_value = mock_client

        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, _make_scanned_profile())
        assert not exc_info.value.retryable
        assert exc_info.value.reason == FailureReason.AUTH_FAILED

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_500_server_error_is_retryable(
        self, mock_settings, mock_client_fn, parser, sample_pdf
    ):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"

        mock_client = AsyncMock()
        mock_client.post.return_value = _ok({}, status=500)
        mock_client_fn.return_value = mock_client

        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, _make_scanned_profile())
        assert exc_info.value.retryable

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_api_error_envelope_fails(
        self, mock_settings, mock_client_fn, parser, sample_pdf
    ):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"

        mock_client = AsyncMock()
        mock_client.post.return_value = _ok({"code": -10001, "msg": "quota exceeded"})
        mock_client_fn.return_value = mock_client

        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, _make_scanned_profile())
        assert "code=-10001" in str(exc_info.value.cause)
        assert not exc_info.value.retryable

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_missing_batch_id_fails(self, mock_settings, mock_client_fn, parser, sample_pdf):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"

        mock_client = AsyncMock()
        mock_client.post.return_value = _ok({"code": 0, "data": {}})
        mock_client_fn.return_value = mock_client

        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, _make_scanned_profile())
        assert "missing batch_id" in str(exc_info.value.cause)


# ── Happy path: full v4 batch flow ───────────────────────────────────────


class TestBatchHappyPath:
    """End-to-end: request URL → upload → poll → download → parse."""

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_full_flow_extracts_markdown_and_content_list(
        self, mock_settings, mock_client_fn, parser, sample_pdf
    ):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"
        mock_settings.MINERU_MAX_WAIT_SECONDS = 5
        mock_settings.PARSER_POLL_INTERVAL_SECONDS = 0.0
        mock_settings.RAG_INDEX_IMAGE_CAPTIONS = False

        batch_resp = _ok(
            {
                "code": 0,
                "data": {
                    "batch_id": "batch-xyz",
                    "file_urls": ["https://upload.example/sig"],
                },
            }
        )
        upload_resp = _ok({}, status=200)
        # First poll: still running. Second poll: done.
        running_resp = _ok(
            {
                "code": 0,
                "data": {"extract_result": [{"file_name": sample_pdf.name, "state": "running"}]},
            }
        )
        done_resp = _ok(
            {
                "code": 0,
                "data": {
                    "extract_result": [
                        {
                            "file_name": sample_pdf.name,
                            "state": "done",
                            "full_zip_url": "https://cdn.example/result.zip",
                            "data_id": "test-pdf",
                        }
                    ]
                },
            }
        )
        zip_resp = _binary_response(
            _zip_with_results(
                markdown="# Title\n\nBody",
                content_list=[
                    {"page_no": 0, "type": "text", "text": "Body"},
                ],
            )
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = batch_resp
        mock_client.put.return_value = upload_resp
        mock_client.get.side_effect = [running_resp, done_resp, zip_resp]
        mock_client_fn.return_value = mock_client

        result = await parser.parse(sample_pdf, _make_scanned_profile())

        assert result.metadata["parser"] == "mineru"
        assert result.metadata["batch_state"] == "done"
        assert result.metadata["data_id"] == "test-pdf"
        # content_list won — markdown is ignored when content_list is present
        assert "Body" in result.pages[0].text
        # Verified order: post (batch) → put (upload) → get (poll x2 + zip)
        assert mock_client.post.await_count == 1
        assert mock_client.put.await_count == 1
        assert mock_client.get.await_count == 3
        # PUT must suppress Content-Type per docs.
        put_kwargs = mock_client.put.await_args.kwargs
        assert put_kwargs["headers"]["Content-Type"] == ""

    @patch("src.services.parser.providers.mineru_api.get_parser_http_client")
    @patch("src.services.parser.providers.mineru_api.settings")
    async def test_batch_failed_is_not_retryable(
        self, mock_settings, mock_client_fn, parser, sample_pdf
    ):
        mock_settings.MINERU_API_KEY = MagicMock()
        mock_settings.MINERU_API_KEY.get_secret_value.return_value = "test-key"
        mock_settings.MINERU_BASE_URL = "https://mineru.net/api/v4"
        mock_settings.MINERU_MAX_WAIT_SECONDS = 5
        mock_settings.PARSER_POLL_INTERVAL_SECONDS = 0.0
        mock_settings.RAG_INDEX_IMAGE_CAPTIONS = False

        batch_resp = _ok(
            {
                "code": 0,
                "data": {
                    "batch_id": "batch-xyz",
                    "file_urls": ["https://upload.example/sig"],
                },
            }
        )
        upload_resp = _ok({}, status=200)
        failed_resp = _ok(
            {
                "code": 0,
                "data": {
                    "extract_result": [
                        {
                            "file_name": sample_pdf.name,
                            "state": "failed",
                            "err_msg": "OCR engine crashed",
                        }
                    ]
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = batch_resp
        mock_client.put.return_value = upload_resp
        mock_client.get.return_value = failed_resp
        mock_client_fn.return_value = mock_client

        with pytest.raises(ParserProviderError) as exc_info:
            await parser.parse(sample_pdf, _make_scanned_profile())
        assert not exc_info.value.retryable
        assert "OCR engine crashed" in str(exc_info.value.cause)


# ── Result decoding helpers ──────────────────────────────────────────────


class TestZipUnpacking:
    def test_extracts_markdown_and_content_list(self, parser):
        zb = _zip_with_results(
            markdown="# Heading\n\nBody",
            content_list=[
                {"page_no": 0, "type": "text", "text": "Body"},
            ],
        )
        markdown, content_list = parser._unpack_zip(zb)
        assert "Heading" in markdown
        assert content_list[0]["text"] == "Body"

    def test_handles_zip_without_content_list(self, parser):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("full.md", "# Just markdown")
        markdown, content_list = parser._unpack_zip(buf.getvalue())
        assert "Just markdown" in markdown
        assert content_list == []


class TestContentMapping:
    def test_extract_with_content_list(self, parser, sample_pdf):
        content_list = [
            {"page_no": 0, "type": "text", "text": "Page 1 text"},
            {"page_no": 1, "type": "text", "text": "Page 2 text"},
            {"page_no": 1, "type": "title", "text": "Section Header"},
        ]
        with patch("src.services.parser.providers.mineru_api.settings") as mock_settings:
            mock_settings.RAG_INDEX_IMAGE_CAPTIONS = False
            doc = parser._build_parsed_doc("", content_list, {"state": "done"}, sample_pdf)
        assert doc.total_pages == 2
        assert "Page 1 text" in doc.pages[0].text
        assert "Section Header" in doc.pages[1].text

    def test_extract_with_table(self, parser, sample_pdf):
        content_list = [
            {
                "page_no": 0,
                "type": "table",
                "text": "",
                "rows": [
                    {"cells": [{"text": "A"}, {"text": "B"}]},
                    {"cells": [{"text": "C"}, {"text": "D"}]},
                ],
            },
        ]
        with patch("src.services.parser.providers.mineru_api.settings") as mock_settings:
            mock_settings.RAG_INDEX_IMAGE_CAPTIONS = False
            doc = parser._build_parsed_doc("", content_list, {"state": "done"}, sample_pdf)
        assert "[TABLE]" in doc.pages[0].text
        assert doc.pages[0].tables is not None

    def test_split_markdown_with_form_feed(self, parser):
        text = "Page 1\fPage 2\fPage 3"
        pages = parser._split_markdown(text)
        assert len(pages) == 3
        assert pages[0].text == "Page 1"
        assert pages[2].text == "Page 3"

    def test_split_markdown_single_page(self, parser):
        text = "Just one page of text"
        pages = parser._split_markdown(text)
        assert len(pages) == 1
        assert pages[0].text == "Just one page of text"
