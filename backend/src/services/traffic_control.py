"""LLM traffic controller - concurrency semaphore + token-bucket rate limiter + circuit breaker.

Protects the LLM provider from concurrent request spikes and cascading failures.
Wraps individual LLM invoke calls, not the HTTP layer (slowapi handles that).

Usage:
    from .traffic_control import traffic_controller

    async with traffic_controller:
        result = await asyncio.to_thread(llm.invoke, prompt)

H-6 Note:
    The semaphore, circuit breaker, and rate-limiter state are **process-local**.
    When deploying with multiple uvicorn workers (``--workers=N``), effective
    concurrency is N * ``LLM_MAX_CONCURRENCY`` and effective RPM is N * ``LLM_RPM``.
    This may exceed the LLM provider's quota and trigger 429 responses.

    **Single-process deployment is strongly recommended.**  If multi-process is
    required, introduce an external coordination layer (e.g. Redis-backed
    semaphore via ``aioredis-lock`` or a sidecar rate-limiter).
"""

import asyncio
import logging
import os
import threading
import time
from collections import deque

from ..core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Thread-safe circuit breaker with half-open recovery.

    States: closed (normal) -> open (blocking) -> half-open (probing) -> closed.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        recovery_successes: int = 2,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._recovery_successes = recovery_successes
        self._failure_count = 0
        self._probe_success_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed | open | half-open | probing
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            # Expose "probing" as "half-open" to external observers so
            # dashboards / metrics don't need a new state.
            return "half-open" if self._state == "probing" else self._state

    def is_call_allowed(self) -> bool:
        """Check if a call is allowed under the current breaker state.

        In half-open state, uses compare-and-swap to atomically transition
        to ``probing`` so only a single caller gets the recovery probe slot
        (C-H4 check-then-act atomicity).
        """
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.monotonic() - self._last_failure_time > self._recovery_timeout:
                    self._state = "half-open"
                    logger.info("Circuit breaker entering half-open state")
                    # Fall through to half-open / probing CAS below.
                else:
                    return False
            if self._state == "half-open":
                # Atomically claim the single probe slot.
                self._state = "probing"
                return True
            # probing: another task already claimed the probe; reject.
            return False

    def record_success(self) -> None:
        """Record a successful call — resets breaker after N consecutive probe successes."""
        with self._lock:
            if self._state in ("half-open", "probing"):
                self._probe_success_count += 1
                if self._probe_success_count >= self._recovery_successes:
                    self._state = "closed"
                    self._probe_success_count = 0
                    logger.info(
                        "Circuit breaker closed after %d successful probes",
                        self._recovery_successes,
                    )
            else:
                self._probe_success_count = 0
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — trips breaker when threshold exceeded."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._state = "open"
                logger.warning(
                    "Circuit breaker opened after %d consecutive failures",
                    self._failure_count,
                )
            elif self._state == "probing":
                # Probing call failed but threshold not yet reached; revert to
                # half-open so another probe can be attempted after a delay.
                self._state = "open"


# ---------------------------------------------------------------------------
# Sliding-Window Error Rate Tracker
# ---------------------------------------------------------------------------


class ErrorRateTracker:
    """Tracks LLM call success/failure rates over a sliding time window."""

    def __init__(self, window_seconds: float = 60.0, alert_threshold: float = 0.5) -> None:
        self._window = window_seconds
        self._threshold = alert_threshold
        self._events: deque[tuple[float, bool]] = deque()  # (timestamp, is_error)
        self._lock = threading.Lock()

    def record(self, is_error: bool) -> None:
        """Record a call outcome."""
        now = time.monotonic()
        with self._lock:
            self._events.append((now, is_error))
            cutoff = now - self._window
            while self._events and self._events[0][0] <= cutoff:
                self._events.popleft()

    @property
    def error_rate(self) -> float:
        """Current error rate (0.0-1.0) over the sliding window."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            recent = [(t, e) for t, e in self._events if t > cutoff]
            if not recent:
                return 0.0
            errors = sum(1 for _, e in recent if e)
            return errors / len(recent)

    @property
    def is_alert(self) -> bool:
        """True if the error rate exceeds the alert threshold."""
        return self.error_rate >= self._threshold


# ---------------------------------------------------------------------------
# Traffic Controller (concurrency + rate limiting + circuit breaker)
# ---------------------------------------------------------------------------


class TrafficController:
    """Async context manager that limits concurrent LLM calls and RPM.

    Integrates a circuit breaker and error rate tracker.
    """

    def __init__(
        self,
        max_concurrency: int = 10,
        rpm: int = 60,
        timeout: float = 30.0,
        circuit_breaker: CircuitBreaker | None = None,
        error_tracker: ErrorRateTracker | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._rpm = rpm
        self._timeout = timeout
        # Token-bucket state with asyncio.Condition so concurrent waiters do not
        # race each other on wake-up (H-5: lock+sleep released the lock during
        # sleep, letting multiple waiters refill and acquire simultaneously).
        self._tokens = float(rpm)
        self._last_refill = time.monotonic()
        self._condition = asyncio.Condition()
        # Circuit breaker & error tracker
        self._breaker = circuit_breaker or CircuitBreaker()
        self._tracker = error_tracker or ErrorRateTracker()

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def tracker(self) -> ErrorRateTracker:
        return self._tracker

    async def _acquire_token(self) -> None:
        """Wait until a rate-limit token is available (token-bucket algorithm).

        Uses ``asyncio.Condition`` so only one waiter proceeds after a refill,
        preventing concurrent waiters from each claiming the same refilled token.
        """
        while True:
            async with self._condition:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(
                    float(self._rpm),
                    self._tokens + elapsed * (self._rpm / 60.0),
                )
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._condition.notify(1)
                    return
                wait_time = (1.0 - self._tokens) / (self._rpm / 60.0)
                import contextlib

                logger.debug("Traffic controller: waiting %.2fs for rate limit token", wait_time)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._condition.wait(), timeout=wait_time)

    async def __aenter__(self) -> "TrafficController":
        # Check circuit breaker first
        if not self._breaker.is_call_allowed():
            from ..core.exceptions import LLMCircuitBreakerError

            raise LLMCircuitBreakerError(
                "LLM service temporarily unavailable (circuit breaker open)"
            )
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._timeout,
            )
        except TimeoutError:
            logger.warning(
                "Traffic controller: timeout waiting for semaphore (concurrency=%d, timeout=%.1fs)",
                self._max_concurrency,
                self._timeout,
            )
            raise
        await self._acquire_token()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._semaphore.release()
        self._update_inflight_gauge()
        if exc_type is None:
            return
        # CancelledError is a BaseException (not Exception).  It should not
        # count as a provider failure — it means the *caller* cancelled, not
        # that the LLM provider is broken.  Skip breaker/tracker recording.
        if issubclass(exc_type, asyncio.CancelledError):
            return
        self.record_failure()

    def _update_inflight_gauge(self) -> None:
        """Push current in-flight count to Prometheus gauge."""
        try:
            from ..core.metrics import TRAFFIC_INFLIGHT

            inflight = self._max_concurrency - getattr(
                self._semaphore, "_value", self._max_concurrency
            )
            TRAFFIC_INFLIGHT.set(inflight)
        except ImportError:
            pass

    def record_success(self) -> None:
        """Record a successful LLM call."""
        self._breaker.record_success()
        self._tracker.record(is_error=False)
        self._update_breaker_gauge()

    def record_failure(self) -> None:
        """Record a failed LLM call."""
        self._breaker.record_failure()
        self._tracker.record(is_error=True)
        self._update_breaker_gauge()

    def _update_breaker_gauge(self) -> None:
        """Push circuit breaker state to Prometheus gauge."""
        try:
            from ..core.metrics import BREAKER_STATE

            state_value = {"closed": 1.0, "half-open": 0.5, "open": 0.0}
            BREAKER_STATE.set(state_value.get(self._breaker.state, 0.0))
        except ImportError:
            pass  # prometheus_client not installed


# Module-level singleton
traffic_controller: TrafficController | None = None


def init_traffic_controller() -> TrafficController:
    """Create the singleton traffic controller from settings."""
    global traffic_controller
    traffic_controller = TrafficController(
        max_concurrency=settings.LLM_MAX_CONCURRENCY,
        rpm=settings.LLM_RPM,
        timeout=30.0,
    )
    logger.info(
        "Traffic controller initialized (concurrency=%d, rpm=%d)",
        settings.LLM_MAX_CONCURRENCY,
        settings.LLM_RPM,
    )

    workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    if workers > 1:
        logger.warning(
            "Traffic controller state is process-local; effective concurrency "
            "and RPM limits are multiplied by %d workers. Single-process "
            "deployment is strongly recommended.",
            workers,
        )

    return traffic_controller
