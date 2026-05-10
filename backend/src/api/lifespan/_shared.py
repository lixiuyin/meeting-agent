"""Shared helpers for lifespan modules."""

import asyncio
import logging

from ...core.audit import audit_log
from ...core.metrics import STARTUP_BEST_EFFORT_FAILURES_TOTAL

logger = logging.getLogger(__name__)


def record_best_effort_failure(task: str) -> None:
    """Record best-effort startup failures for audit and observability."""
    STARTUP_BEST_EFFORT_FAILURES_TOTAL.labels(task=task).inc()
    audit_log("best_effort_failed", "startup", task)


def suppress_generator_exit_errors(loop: asyncio.AbstractEventLoop) -> None:
    """Install a custom asyncio exception handler that suppresses benign
    ``RuntimeError: async generator ignored GeneratorExit`` logs.

    LangChain LCEL chains create nested async generators; when a client
    disconnects mid-stream the inner generators may be garbage-collected
    before the outer ``aclose()`` cascade reaches them. This is harmless
    but floods logs with ERROR-level asyncio messages. We downgrade them
    to debug and let everything else pass through unchanged.
    """
    _default = loop.get_exception_handler() or loop.default_exception_handler

    def _handler(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, RuntimeError) and str(exc) == "async generator ignored GeneratorExit":
            logger.debug("Suppressing known LangChain cleanup race: %s", exc)
            return
        try:
            _default(context)  # type: ignore[call-arg]
        except TypeError:
            _default(_loop, context)  # type: ignore[call-arg]

    loop.set_exception_handler(_handler)
