"""Shared constants and helper functions for retrieval steps."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from ..rag._vector import _vector_score_lower_is_better  # noqa: F401 — re-exported

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MMR_LAMBDA = 0.7
_CONTENT_SIMILARITY_THRESHOLD = 0.85  # ngram overlap ratio to consider duplicates
_LOW_INFO_MAX_ALPHA_RATIO = 0.30
_LOW_INFO_MIN_WORDS = 5
_LOW_INFO_STRONG_PATTERNS = (
    re.compile(r"^\s*page\s+\d+\s*(?:/|of)\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
)
_LOW_INFO_WEAK_MARKERS = (
    "page ",
    "copyright",
    "all rights reserved",
    "confidential",
    "internal use only",
    "for discussion only",
)
_CONTENT_TYPES_BIAS_TABLE = {"table"}
_CONTENT_TYPES_BIAS_FIGURE = {"image_caption", "image_ocr", "image_combined", "image_asset"}
_TABLE_HINTS = (
    "table",
    "rows",
    "columns",
    "spreadsheet",
    "sheet",
    "表格",
    "表",
    "行",
    "列",
)
_FIGURE_HINTS = (
    "figure",
    "image",
    "diagram",
    "chart",
    "screenshot",
    "photo",
    "图片",
    "图",
    "图像",
    "截图",
    "照片",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ngrams(text: str, n: int = 4) -> set[str]:
    """Extract character ngrams from text for similarity comparison."""
    clean = text.lower().strip()
    if len(clean) < n:
        return {clean} if clean else set()
    return {clean[i : i + n] for i in range(len(clean) - n + 1)}


def _score_better(candidate: float, current: float, *, lower_is_better: bool) -> bool:
    """Return True when candidate score outranks current score."""
    return candidate < current if lower_is_better else candidate > current


def _sorted_by_score(docs: list[dict], *, lower_is_better: bool) -> list[dict]:
    """Return a **new** list of documents sorted by score (immutable)."""
    return sorted(docs, key=lambda d: float(d.get("score", 0.0)), reverse=not lower_is_better)


def _content_bias_boost(doc: dict) -> float:
    """Return content-type boost with quality guard for noisy chunks."""
    return 0.08 if not _is_low_information_chunk(doc) else 0.02


def _filter_low_information_chunks(docs: list[dict]) -> list[dict]:
    """Drop low-information chunks from the tail while preserving context coverage."""
    if len(docs) <= 2:
        return docs
    low_info_indexes = [i for i, d in enumerate(docs) if _is_low_information_chunk(d)]
    if not low_info_indexes:
        return docs
    keep = [True] * len(docs)
    for idx in reversed(low_info_indexes):
        if sum(keep) <= 2:
            break
        keep[idx] = False
    return [doc for idx, doc in enumerate(docs) if keep[idx]]


def _is_low_information_chunk(doc: dict) -> bool:
    """Heuristic detector for page markers/footers and other low-signal chunks."""
    text = str(doc.get("content", "") or "").strip()
    if not text:
        return True
    normalized = " ".join(text.lower().split())
    if any(pat.fullmatch(normalized) for pat in _LOW_INFO_STRONG_PATTERNS):
        return True

    alpha_count = sum(ch.isalpha() for ch in normalized)
    alpha_ratio = alpha_count / max(len(normalized), 1)
    words = [w for w in re.split(r"\W+", normalized) if w]
    has_weak_marker = any(marker in normalized for marker in _LOW_INFO_WEAK_MARKERS)
    mostly_short = len([w for w in words if len(w) > 2]) < _LOW_INFO_MIN_WORDS

    if alpha_ratio < _LOW_INFO_MAX_ALPHA_RATIO and mostly_short:
        return True
    return has_weak_marker and mostly_short


def _dedup_docs(all_results: list[list[dict]], *, lower_is_better: bool) -> list[dict]:
    """Merge and deduplicate retrieval results from multiple queries.

    Keeps the best score for each unique chunk.  Uses a stable content
    hash so that chunks sharing a long common prefix are not falsely
    collapsed.  Returns a **new** list with shallow-copied dicts.
    """
    seen: dict[str, dict] = {}
    for results in all_results:
        for doc in results:
            content = doc.get("content", "")
            key = hashlib.sha256(content.encode()).hexdigest()[:16]
            doc_score = float(doc.get("score", 0.0))
            if key not in seen or _score_better(
                candidate=doc_score,
                current=float(seen[key].get("score", 0.0)),
                lower_is_better=lower_is_better,
            ):
                seen[key] = dict(doc)
    return list(seen.values())


async def _generate_query_variants(
    question: str,
    n: int = 3,
    *,
    llm: BaseChatModel | None = None,
) -> list[str]:
    """Generate diverse query variants for multi-query retrieval.

    Uses LLM to produce alternative phrasings of the same question,
    improving recall by covering different semantic angles.
    """
    import asyncio as _asyncio

    from ..llm import cached_retry_invoke, get_llm

    prompt = (
        "Generate {n} alternative phrasings of the following question for search purposes. "
        "Each variant should capture the same intent but use different words or angles. "
        "Return ONLY a JSON array of strings, no explanation.\n\n"
        "Question: {question}"
    )
    try:
        llm = llm or get_llm()
        response = await _asyncio.to_thread(
            cached_retry_invoke, llm, prompt.format(n=n, question=question)
        )
        content = response.content if isinstance(response.content, str) else ""
        from ..llm import parse_llm_json

        data = parse_llm_json(content)
        if isinstance(data, list):
            return [v.strip() for v in data if isinstance(v, str) and v.strip()][:n]
    except Exception:
        from ._common import logger

        logger.warning("Multi-query generation failed", exc_info=True)
    return []
