"""Tests for the parser cascade with mocked providers."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.parser._errors import (  # noqa: E402
    AllParsersFailedError,
    ParserProviderError,
)
from src.services.parser._profile import DocumentProfile  # noqa: E402
from src.services.parser.cascade import (  # noqa: E402
    _annotate_metadata,
    parse_structured,
)
from src.services.parser.types import PageContent, ParsedDocument  # noqa: E402


def _make_doc(text: str = "test content", parser_name: str = "test") -> ParsedDocument:
    return ParsedDocument(
        file_type="pdf",
        pages=[PageContent(page_num=1, text=text)],
        metadata={"parser": parser_name},
        total_pages=1,
    )


def _make_profile_scanned() -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=1,
        avg_chars_per_page=10.0,
        image_ratio=0.0,
        has_text_layer=False,
        is_likely_scanned=True,
        size_bytes=5000,
    )


class TestAnnotateMetadata:
    """Test the _annotate_metadata helper."""

    def test_adds_parser_name(self):
        doc = _make_doc()
        result = _annotate_metadata(doc, "marker", [])
        assert result.metadata["parser"] == "marker"

    def test_adds_fallback_from(self):
        doc = _make_doc()
        result = _annotate_metadata(doc, "mineru", ["marker"])
        assert result.metadata["fallback_from"] == ["marker"]

    def test_preserves_existing_metadata(self):
        doc = _make_doc()
        doc = ParsedDocument(
            file_type="pdf",
            pages=doc.pages,
            metadata={"existing": "value", "parser": "old"},
            total_pages=1,
        )
        result = _annotate_metadata(doc, "local", [])
        assert result.metadata["existing"] == "value"
        assert result.metadata["parser"] == "local"

    def test_immutable_original(self):
        doc = _make_doc()
        original_meta = dict(doc.metadata)
        _annotate_metadata(doc, "marker", ["local"])
        assert doc.metadata == original_meta

    def test_original_format_pptx(self):
        doc = _make_doc()
        result = _annotate_metadata(doc, "marker", [], original_suffix=".pptx")
        assert result.metadata["original_format"] == "pptx"

    def test_original_format_ppt(self):
        doc = _make_doc()
        result = _annotate_metadata(doc, "marker", [], original_suffix=".ppt")
        assert result.metadata["original_format"] == "ppt"

    def test_no_original_format_for_pdf(self):
        doc = _make_doc()
        result = _annotate_metadata(doc, "marker", [], original_suffix=".pdf")
        assert "original_format" not in result.metadata


class TestCascadeTextFiles:
    """Test that text files are handled locally without routing."""

    def test_txt_file_parsed_locally(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("Hello world", encoding="utf-8")
        result = parse_structured(txt)
        assert result.file_type == "txt"
        assert "Hello world" in result.to_text()

    def test_json_file_parsed_locally(self, tmp_path):
        j = tmp_path / "data.json"
        j.write_text('{"key": "value"}', encoding="utf-8")
        result = parse_structured(j)
        assert result.file_type == "json"


class TestCascadeUnsupportedFormat:
    """Test that unsupported formats raise ValueError."""

    def test_unsupported_suffix(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_bytes(b"data")
        with pytest.raises(ValueError, match="Unsupported"):
            parse_structured(f)


class TestCascadeWithProviders:
    """Test cascade routing with mocked providers."""

    @pytest.fixture
    def text_pdf(self, tmp_path):
        """Create a simple text-only PDF."""
        import pymupdf as fitz

        path = tmp_path / "text.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "A" * 500)
        doc.save(str(path))
        doc.close()
        return path

    def test_primary_succeeds(self, text_pdf):
        """When primary parser succeeds, returns immediately."""
        mock_result = _make_doc("local content", "local")

        async def _fake_cascade(parser_order, file_path, profile, **kwargs):
            return _annotate_metadata(mock_result, parser_order[0], [])

        with (
            patch(
                "src.services.parser.cascade._cascade_async",
                side_effect=_fake_cascade,
            ),
            patch(
                "src.services.parser.cascade.select_parsers",
                return_value=("local", "marker", "mineru", "paddle"),
            ),
            patch("src.core.config.settings") as mock_settings,
        ):
            mock_settings.OCR_PROVIDER = ""
            mock_settings.MAX_PARSE_PAGES = 1000
            mock_settings.PARSE_TIMEOUT_SECONDS = 120
            result = parse_structured(text_pdf)
            assert result.metadata["parser"] == "local"

    def test_all_providers_fail(self, text_pdf):
        """When all providers fail, raises AllParsersFailedError."""
        error = ParserProviderError(provider="local", cause=Exception("fail"))

        async def _failing_cascade(parser_order, file_path, profile, **kwargs):
            raise AllParsersFailedError([error])

        with (
            patch(
                "src.services.parser.cascade._cascade_async",
                side_effect=_failing_cascade,
            ),
            patch(
                "src.services.parser.cascade.select_parsers",
                return_value=("local",),
            ),
            patch("src.core.config.settings") as mock_settings,
        ):
            mock_settings.OCR_PROVIDER = ""
            mock_settings.MAX_PARSE_PAGES = 1000
            mock_settings.PARSE_TIMEOUT_SECONDS = 120
            with pytest.raises(AllParsersFailedError):
                parse_structured(text_pdf)


class TestParserProviderError:
    """Test error types."""

    def test_provider_error_message(self):
        err = ParserProviderError(provider="marker", cause=Exception("timeout"))
        assert "marker" in str(err)
        assert err.retryable is True

    def test_non_retryable_error(self):
        err = ParserProviderError(provider="marker", retryable=False)
        assert err.retryable is False

    def test_all_parsers_failed_aggregates(self):
        errors = [
            ParserProviderError(provider="local", cause=Exception("fail1")),
            ParserProviderError(provider="marker", cause=Exception("fail2"), retryable=False),
        ]
        all_err = AllParsersFailedError(errors)
        assert len(all_err.errors) == 2
        assert "local" in str(all_err)
        assert "marker" in str(all_err)
