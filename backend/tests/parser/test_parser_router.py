"""Tests for parser routing logic (_router.py)."""

import os
import tempfile
from pathlib import Path

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.parser._profile import DocumentProfile  # noqa: E402
from src.services.parser._router import (  # noqa: E402
    TEXT_DENSITY_LOW,
    select_parsers,
)


def _make_profile(
    suffix: str = ".pdf",
    page_count: int = 10,
    avg_chars_per_page: float = 200.0,
    image_ratio: float = 0.0,
    has_text_layer: bool = True,
    is_likely_scanned: bool = False,
    size_bytes: int = 5000,
) -> DocumentProfile:
    return DocumentProfile(
        suffix=suffix,
        page_count=page_count,
        avg_chars_per_page=avg_chars_per_page,
        image_ratio=image_ratio,
        has_text_layer=has_text_layer,
        is_likely_scanned=is_likely_scanned,
        size_bytes=size_bytes,
    )


class TestRoutingTable:
    """Verify select_parsers matches the cloud-only routing table."""

    def test_text_heavy_pdf_marker_primary(self):
        """PDF, normal text density → Marker first (local routing removed)."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=300, image_ratio=0.05)
        result = select_parsers(profile)
        assert result[0] == "marker"
        assert len(result) == 3

    def test_text_pdf_with_images_marker_primary(self):
        """PDF, text + images → Marker first."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=250, image_ratio=0.25)
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_scanned_pdf_mineru_primary(self):
        """PDF, text < 50 chars/page → MinerU first."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=20, is_likely_scanned=True)
        result = select_parsers(profile)
        assert result[0] == "mineru"

    def test_mixed_layout_pdf_marker_primary(self):
        """PDF, mixed / irregular layout → Marker first."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=120, image_ratio=0.3)
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_text_pptx_marker_primary(self):
        """PPTX → Marker first (handles mixed layouts best)."""
        profile = _make_profile(suffix=".pptx", avg_chars_per_page=400, image_ratio=0.05)
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_image_heavy_pptx_marker_primary(self):
        """PPTX, image-heavy slides → Marker first."""
        profile = _make_profile(suffix=".pptx", avg_chars_per_page=30, image_ratio=0.6)
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_image_file_paddle_primary(self):
        """Single image file → Paddle first."""
        profile = _make_profile(suffix=".png", avg_chars_per_page=0, image_ratio=1.0)
        result = select_parsers(profile)
        assert result[0] == "paddle"

    def test_docx_marker_primary(self):
        """DOCX / other formats → Marker first."""
        profile = _make_profile(suffix=".docx")
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_xlsx_marker_primary(self):
        profile = _make_profile(suffix=".xlsx")
        result = select_parsers(profile)
        assert result[0] == "marker"

    def test_local_not_in_routing(self):
        """``local`` must never appear in the cloud-only routing output."""
        for suffix in (".pdf", ".pptx", ".docx", ".xlsx", ".png"):
            profile = _make_profile(suffix=suffix, avg_chars_per_page=200)
            assert "local" not in select_parsers(profile)


class TestUserHint:
    """Verify user hint (OCR_PROVIDER env) biases routing."""

    def test_mineru_hint_promotes_mineru(self):
        """User hints mineru → it becomes primary for text-heavy PDF."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=300)
        result = select_parsers(profile, user_hint="mineru")
        assert result[0] == "mineru"

    def test_paddle_hint_promotes_paddle(self):
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=300)
        result = select_parsers(profile, user_hint="paddle")
        assert result[0] == "paddle"

    def test_invalid_hint_ignored(self):
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=300)
        result = select_parsers(profile, user_hint="nonexistent")
        assert result[0] == "marker"  # Default for text-density PDF

    def test_empty_hint_ignored(self):
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=300)
        result = select_parsers(profile, user_hint="")
        assert result[0] == "marker"


class TestEdgeCases:
    """Edge cases for routing."""

    def test_zero_page_pdf(self):
        profile = _make_profile(suffix=".pdf", page_count=0, avg_chars_per_page=0)
        result = select_parsers(profile)
        # No text → routes as scanned (mineru first)
        assert len(result) > 0
        assert result[0] == "mineru"

    def test_boundary_text_density_low(self):
        """Just below TEXT_DENSITY_LOW threshold."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=TEXT_DENSITY_LOW - 1)
        result = select_parsers(profile)
        assert result[0] == "mineru"

    def test_boundary_text_density_low_inclusive(self):
        """At exactly TEXT_DENSITY_LOW threshold → marker (not below)."""
        profile = _make_profile(suffix=".pdf", avg_chars_per_page=TEXT_DENSITY_LOW)
        result = select_parsers(profile)
        assert result[0] == "marker"
