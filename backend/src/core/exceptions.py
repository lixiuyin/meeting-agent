"""Unified LLM exception hierarchy and error mapping.

Maps provider-specific exceptions (OpenAI, Anthropic, etc.) into a consistent
set of typed exceptions so downstream code can handle errors by category
rather than parsing raw error messages.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base for all LLM-related errors."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class LLMConfigError(LLMError):
    """Invalid configuration (missing API key, bad base URL, etc.)."""


class LLMCircuitBreakerError(LLMError):
    """Circuit breaker is open — fast-fail without calling the provider."""


class LLMAPIError(LLMError):
    """Generic API error from the LLM provider."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        self.status_code = status_code


class LLMTimeoutError(LLMAPIError):
    """Request timed out."""

    def __init__(
        self,
        message: str,
        *,
        timeout: float | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        self.timeout = timeout


class LLMRateLimitError(LLMAPIError):
    """Provider rate limit exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message, status_code=429, provider=provider)
        self.retry_after = retry_after


class LLMAuthenticationError(LLMAPIError):
    """Invalid API key or credentials (HTTP 401/403)."""


class LLMContextWindowError(LLMAPIError):
    """Request exceeds the model's context window."""


class LLMTransientResponseError(LLMAPIError):
    """Provider returned a malformed/truncated response body (transient)."""


class LLMEmptyResponseError(LLMTransientResponseError):
    """Provider completed successfully but produced no user-visible answer."""


class ContinuationSnapshotError(Exception):
    """The user requested frozen evidence that cannot be restored safely."""


# ---------------------------------------------------------------------------
# Error mapping engine
# ---------------------------------------------------------------------------

ErrorClassifier = Callable[[Exception], bool]


@dataclass(frozen=True)
class _MappingRule:
    classifier: ErrorClassifier
    factory: Callable[[Exception, str | None], LLMError]


def _instance_of(*types: type[BaseException]) -> ErrorClassifier:
    return lambda exc: isinstance(exc, types)


def _message_contains(*needles: str) -> ErrorClassifier:
    def _classifier(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(n in msg for n in needles)

    return _classifier


def _has_status(*codes: int) -> ErrorClassifier:
    def _classifier(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None)
        return code in codes

    return _classifier


# Provider-specific rules (gracefully degrade if SDK not installed)
_RULES: list[_MappingRule] = []

try:
    import openai

    _RULES.extend(
        [
            _MappingRule(
                classifier=_instance_of(openai.AuthenticationError),
                factory=lambda exc, p: LLMAuthenticationError(str(exc), provider=p),
            ),
            _MappingRule(
                classifier=_instance_of(openai.RateLimitError),
                factory=lambda exc, p: LLMRateLimitError(
                    str(exc), retry_after=getattr(exc, "retry_after", None), provider=p
                ),
            ),
        ]
    )
except ImportError:
    pass

try:
    import anthropic

    _RULES.append(
        _MappingRule(
            classifier=_instance_of(anthropic.RateLimitError),
            factory=lambda exc, p: LLMRateLimitError(str(exc), provider=p),
        )
    )
except ImportError:
    pass

# Malformed response body — treat as transient (OpenRouter / proxies occasionally
# return SSE frames, HTML error pages, or truncated bodies that fail JSON parsing).
_RULES.append(
    _MappingRule(
        classifier=_instance_of(json.JSONDecodeError),
        factory=lambda exc, p: LLMTransientResponseError(str(exc), provider=p),
    )
)

# Keyword-based rules (catch-all for any provider)
_RULES.extend(
    [
        _MappingRule(
            classifier=_instance_of(TimeoutError, asyncio.TimeoutError),
            factory=lambda exc, p: LLMTimeoutError(str(exc), provider=p),
        ),
        _MappingRule(
            classifier=_message_contains("rate limit", "quota", "too many requests"),
            factory=lambda exc, p: LLMRateLimitError(str(exc), provider=p),
        ),
        _MappingRule(
            classifier=_message_contains("context length", "maximum context", "token limit"),
            factory=lambda exc, p: LLMContextWindowError(str(exc), provider=p),
        ),
        _MappingRule(
            classifier=_has_status(401, 403),
            factory=lambda exc, p: LLMAuthenticationError(str(exc), provider=p),
        ),
        _MappingRule(
            classifier=_has_status(429),
            factory=lambda exc, p: LLMRateLimitError(str(exc), provider=p),
        ),
        _MappingRule(
            classifier=_has_status(408, 504),
            factory=lambda exc, p: LLMTimeoutError(str(exc), provider=p),
        ),
    ]
)


def map_error(exc: Exception, provider: str | None = None) -> LLMError:
    """Map a raw exception to a typed LLMError.

    Falls back to ``LLMAPIError`` if no rule matches.
    """
    if isinstance(exc, LLMError):
        return exc

    for rule in _RULES:
        if rule.classifier(exc):
            return rule.factory(exc, provider)

    return LLMAPIError(str(exc), provider=provider)


def is_retryable(exc: Exception) -> bool:
    """Return True if the error is worth retrying (rate limit, timeout, 5xx, bad body)."""
    mapped = map_error(exc)
    if isinstance(mapped, (LLMRateLimitError, LLMTimeoutError, LLMTransientResponseError)):
        return True
    if isinstance(mapped, LLMAPIError) and mapped.status_code is not None:
        return mapped.status_code >= 500
    return False
