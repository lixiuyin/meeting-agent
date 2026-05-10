"""Error types for the cloud-native parser cascade."""

import enum

_MAX_RETRIES = 2  # max attempts per provider (1 initial + 1 retry)


class FailureReason(enum.StrEnum):
    """Categorised reason for a parser failure."""

    QUALITY_GATE = "quality_gate"
    NETWORK_TIMEOUT = "network_timeout"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    CONVERSION_FAILED = "conversion_failed"
    UNKNOWN = "unknown"


class ParserProviderError(Exception):
    """Raised when a single parser provider fails.

    ``retryable=True`` errors trigger an immediate retry of the same
    provider before falling back to the next one.
    """

    def __init__(
        self,
        provider: str,
        cause: Exception | None = None,
        retryable: bool = True,
        reason: FailureReason = FailureReason.UNKNOWN,
    ) -> None:
        self.provider = provider
        self.cause = cause
        self.retryable = retryable
        self.reason = reason
        msg = f"{provider} failed ({reason.value})"
        if cause:
            msg += f": {cause}"
        super().__init__(msg)


class AllParsersFailedError(Exception):
    """Raised when every parser in the cascade has failed."""

    def __init__(self, errors: list[ParserProviderError]) -> None:
        self.errors = errors
        details = "; ".join(
            f"{e.provider} ({'retryable' if e.retryable else 'permanent'}, {e.reason.value})"
            for e in errors
        )
        super().__init__(f"All parsers failed: {details}")
