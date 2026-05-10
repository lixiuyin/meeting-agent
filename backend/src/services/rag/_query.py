"""Query rewriting and adaptive top-k logic."""

import asyncio
import logging
import re
import threading
from typing import Any

from cachetools import TTLCache
from langchain_core.prompts import ChatPromptTemplate

from ...core.config import settings
from ..llm import get_llm

logger = logging.getLogger(__name__)

_QUERY_REWRITE_PROMPT = """Rewrite the query to improve document retrieval quality.
- If the query is in Chinese, include relevant English technical terms.
- Expand abbreviations and acronyms.
- Add synonymous phrasings that might match the document language.
- Keep the core intent unchanged.
- Return ONLY the rewritten query, nothing else.

Original query: {query}{speaker_hint}"""

_COMPLEXITY_KEYWORDS = {
    "how many",
    "compare",
    "analyze",
    "list all",
    "summary of",
    "relationship between",
    "difference between",
    "why did",
    "what caused",
    "step by step",
    "explain",
    "all of the",
    "what is the",
    "what are the",
    "could you",
    "can you",
}
_SIMPLE_QUESTION_MIN_CHARS = 30
_SIMPLE_QUERY_TOP_K = 3  # QR-4: Extracted constant, was hardcoded 3
_MAX_TOP_K_HARD_LIMIT = 50  # M-14: Prevent unbounded retrieval

# Anaphora / pronouns that signal the query needs context rewriting
_ANAPHORA_PATTERN = re.compile(
    r"\b(it|that|this|they|them|these|those|the above|the previous|the last)\b",
    re.IGNORECASE,
)

_REWRITE_MAX_TOKENS = 6  # skip rewrite if query is this short or less

# Singleton for the lightweight rewrite model (thread-safe)
_rewrite_llm: Any = None
_rewrite_llm_lock = threading.Lock()
_cached_rewrite_model: str | None = None

# C-2: TTL cache for query rewrites to avoid repeated LLM calls for identical queries.
_REWRITE_CACHE: TTLCache = TTLCache(maxsize=2048, ttl=600)
_REWRITE_CACHE_LOCK = threading.Lock()


def _clear_rewrite_cache() -> None:
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE.clear()


from ...core.settings_epoch import register_epoch_cache  # noqa: E402

register_epoch_cache(_clear_rewrite_cache)


def _get_rewrite_llm() -> Any:
    """Get or create the singleton lightweight rewrite model (thread-safe).

    All reads/writes of ``_rewrite_llm`` and ``_cached_rewrite_model`` happen
    inside ``_rewrite_llm_lock`` to prevent data races under concurrent access.
    """
    global _rewrite_llm, _cached_rewrite_model
    rewrite_model = settings.QUERY_REWRITE_MODEL
    if not rewrite_model:
        with _rewrite_llm_lock:
            if _rewrite_llm is not None:
                _rewrite_llm = None
                _cached_rewrite_model = None
                logger.info("Query rewrite LLM singleton cleared (model unset)")
        return None
    with _rewrite_llm_lock:
        # If settings changed, invalidate the singleton
        if _cached_rewrite_model is not None and _cached_rewrite_model != rewrite_model:
            _rewrite_llm = None
            _cached_rewrite_model = None
        if _rewrite_llm is None:
            from langchain_openai import ChatOpenAI

            api_key = settings.LLM_API_KEY.get_secret_value()
            base_url = settings.LLM_BASE_URL or "https://api.openai.com/v1"
            kwargs: dict[str, Any] = {
                "model": rewrite_model,
                "temperature": 0.0,
                "max_tokens": 128,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            _rewrite_llm = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
            _cached_rewrite_model = rewrite_model
        return _rewrite_llm


def reset_rewrite_llm() -> None:
    """Reset the cached rewrite model singleton (call when settings change)."""
    global _rewrite_llm, _cached_rewrite_model
    with _rewrite_llm_lock:
        _rewrite_llm = None
        _cached_rewrite_model = None
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE.clear()


def _is_simple_query(question: str) -> bool:
    """Return True if the query is short and has no anaphora, making rewrite unnecessary."""
    words = question.split()
    return len(words) <= _REWRITE_MAX_TOKENS and not _ANAPHORA_PATTERN.search(question)


async def rewrite_query(
    question: str,
    *,
    speaker_names: list[str] | None = None,
) -> str:
    """Use LLM to rewrite query for better retrieval. Returns original on failure.

    When ``speaker_names`` are provided, they are injected into the prompt
    so the rewritten query preserves speaker identity (HIGH-6).
    """
    if _is_simple_query(question):
        logger.debug("Skipping rewrite for simple query: '%s'", question[:50])
        return question

    # C-2: Check TTL cache before calling LLM
    cache_key = (
        question,
        tuple(sorted(n.casefold() for n in speaker_names)) if speaker_names else (),
    )
    with _REWRITE_CACHE_LOCK:
        cached = _REWRITE_CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Query rewrite cache hit for: '%s'", question[:50])
        return cached

    rewrite_llm = _get_rewrite_llm()
    llm = rewrite_llm if rewrite_llm else get_llm()
    prompt = ChatPromptTemplate.from_messages([("human", _QUERY_REWRITE_PROMPT)])
    # HIGH-6: Preserve speaker names so rewritten queries match speaker-filtered
    # chunks. Inject via structured prompt variable to prevent prompt injection.
    speaker_hint = ""
    if speaker_names:
        # Sanitize: only allow letters, digits, spaces, hyphens, apostrophes, dots,
        # and CJK characters. Reject any speaker name that could inject prompt content.
        import unicodedata

        safe_names = []
        for name in speaker_names:
            if not name or len(name) > 80:
                continue
            # Allow letters (any script), digits, spaces, hyphens, apostrophes, periods
            if all(
                unicodedata.category(c).startswith(("L", "N")) or c in (" ", "-", "'", ".", "·")
                for c in name
            ):
                safe_names.append(name)
        if safe_names:
            speaker_hint = f"\nKnown speakers in this context: {', '.join(safe_names)}."
    formatted = prompt.format_messages(query=question, speaker_hint=speaker_hint)
    try:
        from ..llm import cached_retry_invoke

        response = await asyncio.wait_for(
            asyncio.to_thread(cached_retry_invoke, llm, formatted),
            timeout=settings.QUERY_REWRITE_TIMEOUT_SECONDS,
        )
        result = response.content if hasattr(response, "content") else str(response)
        # QR-2: Log token usage for cost observability
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            token_in = response.usage_metadata.get("input_tokens", 0)
            token_out = response.usage_metadata.get("output_tokens", 0)
            logger.info(
                "Query rewritten: '%s' -> '%s' (tokens: %d in, %d out)",
                question[:50],
                result[:50],
                token_in,
                token_out,
            )
        else:
            logger.info("Query rewritten: '%s' -> '%s'", question[:50], result[:50])
        rewritten = result.strip()
        with _REWRITE_CACHE_LOCK:
            _REWRITE_CACHE[cache_key] = rewritten
        return rewritten
    except Exception:
        logger.warning("Query rewrite failed, using original", exc_info=True)
        return question


_SUMMARY_INTENT_PATTERNS = (
    re.compile(
        r"\b(summari[sz]e|overview|list\s+.*topics|what\s+(was|were)\s+discussed|compare)\b",
        re.IGNORECASE,
    ),
    re.compile(r"总结|概述|梳理|都讨论了|讲了什么|主要内容|对比"),
)


def is_summary_intent(question: str) -> bool:
    """Detect if the question asks for a summary or broad overview."""
    return any(p.search(question) for p in _SUMMARY_INTENT_PATTERNS)


def determine_adaptive_top_k(
    question: str,
    user_requested_k: int | None,
    *,
    is_broad_recall: bool = False,
) -> int:
    """Decide top_k based on question complexity. User override always wins.

    When ``is_broad_recall`` is True (no file scope, with or without meeting
    scope), the floor is raised to ensure broad questions retrieve enough
    context. Summary intent questions get an even higher floor.
    """
    if user_requested_k is not None:
        return min(user_requested_k, _MAX_TOP_K_HARD_LIMIT)
    base = settings.TOP_K
    if is_broad_recall:
        if is_summary_intent(question):
            return min(max(base, settings.SUMMARY_INTENT_TOP_K), _MAX_TOP_K_HARD_LIMIT)
        return min(max(base, 8), _MAX_TOP_K_HARD_LIMIT)
    q = question.lower().strip()
    if len(q) < _SIMPLE_QUESTION_MIN_CHARS and not any(kw in q for kw in _COMPLEXITY_KEYWORDS):
        return _SIMPLE_QUERY_TOP_K
    return min(base, _MAX_TOP_K_HARD_LIMIT)
