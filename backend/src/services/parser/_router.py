"""Parser routing — selects providers based on document profile.

Pure function, trivially unit-testable.  Returns an ordered tuple of parser
names (primary first) so the cascade can try them sequentially.

Local (PyMuPDF) is excluded from normal routing — it serves only as an
emergency fallback when all cloud APIs are unavailable.
"""

from __future__ import annotations

from typing import Literal, cast

from ._profile import DocumentProfile

ParserName = Literal["marker", "mineru", "paddle"]

# ── Routing thresholds ──────────────────────────────────────────────────────

TEXT_DENSITY_LOW = 50  # chars/page — likely scanned / handwritten


def select_parsers(
    profile: DocumentProfile,
    user_hint: str = "",
) -> tuple[ParserName, ...]:
    """Return an ordered list of parser names (primary → fallback).

    ``user_hint`` is the ``OCR_PROVIDER`` env var value — treated as a soft
    preference, not an override.  If the user explicitly chooses a provider
    that could work for this document, it's promoted to primary.
    """
    suffix = profile.suffix
    avg_chars = profile.avg_chars_per_page

    # ── Single image file → Paddle first (layout-parsing specialist) ─────
    # MinerU v4 extract requires PDF input — skip it for standalone images.
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"}:
        return _apply_hint(("paddle", "marker"), user_hint)

    # ── PDF routing ──────────────────────────────────────────────────────
    if suffix == ".pdf":
        if avg_chars < TEXT_DENSITY_LOW:
            # Scanned / handwritten — MinerU OCR is strongest
            return _apply_hint(("mineru", "marker", "paddle"), user_hint)
        # Normal text / mixed layout — Marker handles structure best
        return _apply_hint(("marker", "mineru", "paddle"), user_hint)

    # ── PPTX routing ─────────────────────────────────────────────────────
    # Marker handles mixed layouts; Paddle for image-heavy slides
    if suffix == ".pptx":
        return _apply_hint(("marker", "paddle", "mineru"), user_hint)

    # ── DOCX / XLSX / other document formats ─────────────────────────────
    return _apply_hint(("marker", "mineru", "paddle"), user_hint)


def _apply_hint(
    order: tuple[ParserName, ...],
    hint: str,
) -> tuple[ParserName, ...]:
    """Promote the hinted provider to front if it appears in the list."""
    if not hint or hint not in order:
        return order
    hint_typed = cast(ParserName, hint)
    remaining: tuple[ParserName, ...] = tuple(p for p in order if p != hint_typed)
    return (hint_typed, *remaining)
