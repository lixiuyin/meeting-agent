"""FileKind registry — single source of truth for file classification.

Replaces scattered string checks (``FILE_TYPE_MAP``, ``_VIDEO_EXTS``,
``if file_type in ("video", "audio")``) with a unified capability table.

Each entry maps a canonical *kind* (e.g. ``"video"``, ``"pdf"``) to a
:class:`FileKindSpec` that declares what the kind can do.

Usage::

    from src.services.files._kinds import resolve_kind, FILE_KINDS

    spec = resolve_kind("kickoff.mp4")
    assert spec.kind == "video"
    assert spec.has_timeline
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileKindSpec:
    """Capability descriptor for one file kind."""

    kind: str  # "video" | "audio" | "pdf" | "pptx" | "doc" | "xls" | "csv" | "txt" | "image"

    extensions: frozenset[str]

    has_timeline: bool  # segments / timestamps (video, audio)

    has_pages: bool  # page or slide numbering (pdf, pptx)

    has_images: bool  # embedded images or is an image (pdf, pptx, image)

    viewer_hint: str  # frontend hint: "video" | "audio" | "pdf" | "slides" | "image" | "text"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FILE_KINDS: dict[str, FileKindSpec] = {
    "video": FileKindSpec(
        kind="video",
        extensions=frozenset({".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".3gp"}),
        has_timeline=True,
        has_pages=False,
        has_images=False,
        viewer_hint="video",
    ),
    "audio": FileKindSpec(
        kind="audio",
        extensions=frozenset({".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".wma", ".opus"}),
        has_timeline=True,
        has_pages=False,
        has_images=False,
        viewer_hint="audio",
    ),
    "pdf": FileKindSpec(
        kind="pdf",
        extensions=frozenset({".pdf"}),
        has_timeline=False,
        has_pages=True,
        has_images=True,
        viewer_hint="pdf",
    ),
    "pptx": FileKindSpec(
        kind="pptx",
        extensions=frozenset({".ppt", ".pptx"}),
        has_timeline=False,
        has_pages=True,
        has_images=True,
        viewer_hint="slides",
    ),
    "doc": FileKindSpec(
        kind="doc",
        extensions=frozenset({".doc", ".docx"}),
        has_timeline=False,
        has_pages=False,
        has_images=False,
        viewer_hint="text",
    ),
    "xls": FileKindSpec(
        kind="xls",
        extensions=frozenset({".xls", ".xlsx"}),
        has_timeline=False,
        has_pages=False,
        has_images=False,
        viewer_hint="text",
    ),
    "csv": FileKindSpec(
        kind="csv",
        extensions=frozenset({".csv"}),
        has_timeline=False,
        has_pages=False,
        has_images=False,
        viewer_hint="text",
    ),
    "txt": FileKindSpec(
        kind="txt",
        extensions=frozenset(
            {".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml", ".rtf"}
        ),
        has_timeline=False,
        has_pages=False,
        has_images=False,
        viewer_hint="text",
    ),
    "image": FileKindSpec(
        kind="image",
        extensions=frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"}),
        has_timeline=False,
        has_pages=False,
        has_images=True,
        viewer_hint="image",
    ),
}


# ---------------------------------------------------------------------------
# Extension → kind lookup (built once at import)
# ---------------------------------------------------------------------------

_EXT_KIND_MAP: dict[str, str] = {
    ext: spec.kind for spec in FILE_KINDS.values() for ext in spec.extensions
}


def resolve_kind(filename: str) -> FileKindSpec | None:
    """Resolve a filename to its :class:`FileKindSpec`.

    Returns ``None`` if the extension is not recognised.
    """
    ext = _get_ext(filename)
    kind = _EXT_KIND_MAP.get(ext)
    return FILE_KINDS.get(kind) if kind else None


def resolve_kind_name(filename: str) -> str | None:
    """Return the kind string for *filename*, e.g. ``"video"``."""
    ext = _get_ext(filename)
    return _EXT_KIND_MAP.get(ext)


def kind_for_extension(ext: str) -> FileKindSpec | None:
    """Look up by raw extension string (e.g. ``".mp4"``)."""
    kind = _EXT_KIND_MAP.get(ext.lower())
    return FILE_KINDS.get(kind) if kind else None


def video_extensions() -> frozenset[str]:
    """All video extensions (replaces ``transcriber._VIDEO_EXTS``)."""
    return FILE_KINDS["video"].extensions


def timeline_kinds() -> frozenset[str]:
    """Kinds that have timeline/segment data."""
    return frozenset(k for k, s in FILE_KINDS.items() if s.has_timeline)


def paginated_kinds() -> frozenset[str]:
    """Kinds that have page/slide numbering."""
    return frozenset(k for k, s in FILE_KINDS.items() if s.has_pages)


def _get_ext(filename: str) -> str:
    """Extract and lower-case the file extension."""
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx >= 0 else ""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

ALL_EXTENSIONS: frozenset[str] = frozenset(_EXT_KIND_MAP.keys())
"""Every extension the registry knows about."""


def is_known_extension(ext: str) -> bool:
    """Check whether *ext* (e.g. ``".mp4"``) is in the registry."""
    return ext.lower() in _EXT_KIND_MAP
