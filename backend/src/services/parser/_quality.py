"""Parser output quality assessment — heuristic checks for cascade fallback.

Runs after each provider returns a ``ParsedDocument``.  If the output fails
quality thresholds, the cascade logs a warning and tries the next provider
instead of accepting the result.

All checks are pure-heuristic (no LLM calls).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ._profile import DocumentProfile
from .types import ParsedDocument

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityResult:
    """Quality assessment outcome."""

    is_satisfactory: bool
    score: float  # 0.0-1.0
    reasons: list[str]  # reasons for rejection (empty = satisfactory)


def assess_quality(doc: ParsedDocument, profile: DocumentProfile) -> QualityResult:
    """Check if a parsed document meets minimum quality thresholds.

    Three heuristic checks — designed to catch extraction failures, not
    to evaluate RAG suitability.  Scanned/image-heavy documents (low
    ``avg_chars_per_page``) are exempt from volume-based checks.

    Returns ``QualityResult(is_satisfactory=True, …)`` if acceptable.
    """
    total_text = "".join(p.text for p in doc.pages)
    stripped = total_text.strip()

    # 1. Empty content — only flag when the document should have text
    if not stripped and not profile.is_likely_scanned:
        return _fail("empty_text", 0.0)

    # If document is scanned/image-heavy, empty or near-empty text is expected.
    if profile.is_likely_scanned:
        chars_per_page = len(stripped) / max(1, profile.page_count)
        if chars_per_page < _MIN_SCANNED_CHARS_PER_PAGE:
            return _fail(f"scanned_text_too_short(chars_per_page={chars_per_page:.1f})", 0.2)
        return QualityResult(is_satisfactory=True, score=0.8, reasons=[])

    reasons: list[str] = []
    score = 0.9  # start high, deduct per issue

    # 2. Text volume anomaly
    expected = profile.avg_chars_per_page * profile.page_count
    if expected > 100 and len(stripped) < expected * _MIN_TEXT_RATIO:
        reasons.append(f"text_too_short(ratio={len(stripped) / expected:.2f})")
        score -= 0.3

    # 3. Control character density
    ctrl_count = sum(1 for c in stripped if ord(c) < 0x20 and c not in "\n\r\t")
    if len(stripped) > 50 and ctrl_count / len(stripped) > _MAX_CONTROL_CHAR_RATIO:
        reasons.append(f"high_control_chars(ratio={ctrl_count / len(stripped):.3f})")
        score -= 0.2

    score = max(0.0, score)
    if reasons:
        return QualityResult(is_satisfactory=False, score=score, reasons=reasons)

    return QualityResult(is_satisfactory=True, score=score, reasons=[])


def _fail(reason: str, score: float) -> QualityResult:
    return QualityResult(is_satisfactory=False, score=score, reasons=[reason])


# ── Thresholds (mirrored from settings for module-level access) ────────────

_MIN_TEXT_RATIO = 0.05  # min text volume as fraction of expected
_MIN_SCANNED_CHARS_PER_PAGE = 100
_MAX_CONTROL_CHAR_RATIO = 0.03  # max control char density
