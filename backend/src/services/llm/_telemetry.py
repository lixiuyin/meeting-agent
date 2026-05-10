"""LLM call telemetry / tracing decorators."""

import asyncio
import functools
import logging

logger = logging.getLogger(__name__)


def track_llm_call(func_name: str | None = None):
    """Decorator that logs entry, success, and failure of LLM call functions.

    Usage::

        @track_llm_call("fact_extraction")
        async def extract_facts(...):
            ...
    """
    label = func_name or "llm_call"

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                logger.debug("[%s] starting", label)
                try:
                    result = await fn(*args, **kwargs)
                    logger.debug("[%s] succeeded", label)
                    return result
                except Exception as exc:
                    logger.warning("[%s] failed: %s", label, exc)
                    raise

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                logger.debug("[%s] starting", label)
                try:
                    result = fn(*args, **kwargs)
                    logger.debug("[%s] succeeded", label)
                    return result
                except Exception as exc:
                    logger.warning("[%s] failed: %s", label, exc)
                    raise

            return sync_wrapper

    return decorator
