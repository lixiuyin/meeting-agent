"""LLM response cache, retry, and combined cache+retry wrappers."""

import hashlib
import json
import logging
import threading
from typing import Any

from cachetools import TTLCache
from langchain_core.language_models.chat_models import BaseChatModel

from ...core.config import settings

logger = logging.getLogger(__name__)

_llm_cache: TTLCache | None = None
_llm_cache_key: tuple[int, int] | None = None
_llm_cache_lock = threading.Lock()


def _get_llm_cache() -> TTLCache | None:
    """Get or create the LLM response cache (singleton, respects settings)."""
    global _llm_cache, _llm_cache_key
    if not settings.LLM_CACHE_ENABLED:
        return None
    cache_key = (settings.LLM_CACHE_MAX_SIZE, settings.LLM_CACHE_TTL_SECONDS)
    if _llm_cache is None or _llm_cache_key != cache_key:
        with _llm_cache_lock:
            if _llm_cache is None or _llm_cache_key != cache_key:
                _llm_cache = TTLCache(
                    maxsize=settings.LLM_CACHE_MAX_SIZE,
                    ttl=settings.LLM_CACHE_TTL_SECONDS,
                )
                _llm_cache_key = cache_key
                logger.info(
                    "LLM cache initialized (ttl=%ds, max=%d)",
                    settings.LLM_CACHE_TTL_SECONDS,
                    settings.LLM_CACHE_MAX_SIZE,
                )
    return _llm_cache


def _cache_key(llm: BaseChatModel, prompt: Any) -> str:
    """Deterministic cache key from model identity + prompt content."""
    parts = [
        settings.LLM_BINDING,
        settings.LLM_MODEL,
        str(getattr(llm, "temperature", "")),
    ]
    if isinstance(prompt, str):
        parts.append(prompt)
    elif isinstance(prompt, list):
        parts.append(
            json.dumps(
                [(type(m).__name__, str(m)) for m in prompt],
                ensure_ascii=False,
            )
        )
    else:
        parts.append(str(prompt))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_invoke(llm: BaseChatModel, prompt: Any) -> Any:
    """Invoke the LLM with optional caching. Thread-safe (TTLCache is thread-safe)."""
    cache = _get_llm_cache()
    if cache is None:
        return llm.invoke(prompt)

    key = _cache_key(llm, prompt)
    hit = cache.get(key)
    if hit is not None:
        logger.debug("LLM cache hit: %s...", key[:12])
        return hit

    result = llm.invoke(prompt)
    cache[key] = result
    return result


def retry_invoke(llm: BaseChatModel, prompt: Any) -> Any:
    """Invoke the LLM with automatic retry on transient failures."""
    from tenacity import (
        before_sleep_log,
        retry,
        retry_if_exception,
        stop_after_attempt,
        wait_exponential,
    )

    def _is_retryable(exc: BaseException) -> bool:
        from ...core.exceptions import is_retryable

        if not isinstance(exc, Exception):
            return False
        return is_retryable(exc)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(settings.LLM_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1.5, min=1, max=60),
        before_sleep=before_sleep_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _invoke() -> Any:
        try:
            return llm.invoke(prompt)
        except Exception as exc:
            from ...core.exceptions import map_error

            mapped = map_error(exc, provider=settings.LLM_BINDING)
            if isinstance(mapped, (type(exc),)) or mapped.__class__ in type(exc).__mro__:
                raise
            raise mapped from exc

    return _invoke()


def cached_retry_invoke(llm: BaseChatModel, prompt: Any) -> Any:
    """Combined cache + retry: check cache first, then invoke with retry."""
    cache = _get_llm_cache()
    if cache is not None:
        key = _cache_key(llm, prompt)
        hit = cache.get(key)
        if hit is not None:
            logger.debug("LLM cache hit: %s...", key[:12])
            return hit

        result = retry_invoke(llm, prompt)
        cache[key] = result
        return result

    return retry_invoke(llm, prompt)
