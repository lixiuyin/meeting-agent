"""Tests for the FileKind registry."""

from typing import ClassVar

from src.services.files._kinds import (
    ALL_EXTENSIONS,
    FILE_KINDS,
    is_known_extension,
    kind_for_extension,
    paginated_kinds,
    resolve_kind,
    resolve_kind_name,
    timeline_kinds,
    video_extensions,
)

# ---------------------------------------------------------------------------
# Every extension maps to exactly one kind
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """Ensure every extension the pipeline accepts maps to exactly one kind."""

    # Extensions currently accepted by FILE_TYPE_MAP in _common.py
    _EXPECTED_EXTENSIONS: ClassVar[set[str]] = {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".m4v",
        ".3gp",
        ".mp3",
        ".wav",
        ".aac",
        ".flac",
        ".m4a",
        ".ogg",
        ".wma",
        ".opus",
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".csv",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".json",
        ".xml",
        ".rtf",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".gif",
    }

    def test_all_pipeline_extensions_registered(self):
        """Every extension in the old FILE_TYPE_MAP is in the registry."""
        missing = self._EXPECTED_EXTENSIONS - ALL_EXTENSIONS
        assert not missing, f"Missing from registry: {missing}"

    def test_no_extra_extensions(self):
        """Registry has no extensions the pipeline doesn't accept."""
        extra = ALL_EXTENSIONS - self._EXPECTED_EXTENSIONS
        assert not extra, f"Unexpected extensions: {extra}"

    def test_every_extension_resolves(self):
        """resolve_kind returns a spec for every expected extension."""
        for ext in self._EXPECTED_EXTENSIONS:
            spec = resolve_kind(f"test{ext}")
            assert spec is not None, f"resolve_kind failed for {ext}"
            assert spec.kind in FILE_KINDS

    def test_no_unknown_extensions_resolve(self):
        """resolve_kind returns None for unknown extensions."""
        assert resolve_kind("test.xyz") is None
        assert resolve_kind("test") is None
        assert resolve_kind("") is None


# ---------------------------------------------------------------------------
# Kind-specific capability flags
# ---------------------------------------------------------------------------


class TestKindCapabilities:
    def test_video_has_timeline(self):
        assert FILE_KINDS["video"].has_timeline is True
        assert FILE_KINDS["video"].has_pages is False

    def test_audio_has_timeline(self):
        assert FILE_KINDS["audio"].has_timeline is True
        assert FILE_KINDS["audio"].has_pages is False

    def test_pdf_has_pages(self):
        assert FILE_KINDS["pdf"].has_pages is True
        assert FILE_KINDS["pdf"].has_timeline is False

    def test_pptx_has_pages(self):
        assert FILE_KINDS["pptx"].has_pages is True
        assert FILE_KINDS["pptx"].has_timeline is False

    def test_image_has_images_flag(self):
        assert FILE_KINDS["image"].has_images is True

    def test_txt_no_special_capabilities(self):
        assert FILE_KINDS["txt"].has_timeline is False
        assert FILE_KINDS["txt"].has_pages is False
        assert FILE_KINDS["txt"].has_images is False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_resolve_kind_name(self):
        assert resolve_kind_name("meeting.mp4") == "video"
        assert resolve_kind_name("slides.pptx") == "pptx"
        assert resolve_kind_name("photo.jpg") == "image"
        assert resolve_kind_name("notes.txt") == "txt"

    def test_kind_for_extension(self):
        assert kind_for_extension(".mp4").kind == "video"
        assert kind_for_extension(".PDF").kind == "pdf"  # case-insensitive

    def test_video_extensions_matches_transcriber(self):
        """video_extensions() matches transcriber._VIDEO_EXTS."""
        from src.services.transcriber import _VIDEO_EXTS

        assert video_extensions() == _VIDEO_EXTS

    def test_timeline_kinds(self):
        tk = timeline_kinds()
        assert "video" in tk
        assert "audio" in tk
        assert "pdf" not in tk

    def test_paginated_kinds(self):
        pk = paginated_kinds()
        assert "pdf" in pk
        assert "pptx" in pk
        assert "video" not in pk

    def test_is_known_extension(self):
        assert is_known_extension(".mp4") is True
        assert is_known_extension(".MP4") is True
        assert is_known_extension(".xyz") is False

    def test_viewer_hints(self):
        assert FILE_KINDS["video"].viewer_hint == "video"
        assert FILE_KINDS["audio"].viewer_hint == "audio"
        assert FILE_KINDS["pdf"].viewer_hint == "pdf"
        assert FILE_KINDS["pptx"].viewer_hint == "slides"
        assert FILE_KINDS["image"].viewer_hint == "image"
        assert FILE_KINDS["txt"].viewer_hint == "text"
