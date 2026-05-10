"""RAG chain service - pipeline-based orchestration of retrieval, memory, search, and generation."""

import asyncio
import time

_MAX_BG_TASKS = 64
# Maximum wall-clock duration for a background task before it is considered
# stuck and eligible for eviction (C-C3).
_MAX_BG_TASK_AGE_SECONDS = 300

# Track fire-and-forget tasks so they can be awaited/cancelled on shutdown
_background_tasks: set[asyncio.Task] = set()
# Track per-task creation time for timeout-driven eviction (C-C3).
_task_creation_times: dict[asyncio.Task, float] = {}


def _register_background_task(task: asyncio.Task) -> None:
    """Add a task to the background set, evicting a stuck task if at capacity.

    Uses timeout-driven eviction: when the set is full, cancels the
    *longest-running* task that exceeds ``_MAX_BG_TASK_AGE_SECONDS``
    instead of blindly evicting the oldest (C-C3).
    """
    from ._common import logger

    if len(_background_tasks) >= _MAX_BG_TASKS:
        now = time.monotonic()
        # Find a stuck task to evict (oldest task that exceeded timeout).
        evicted = False
        for t in sorted(_background_tasks, key=lambda t: _task_creation_times.get(t, 0)):
            age = now - _task_creation_times.get(t, now)
            if age > _MAX_BG_TASK_AGE_SECONDS:
                t.cancel()
                _background_tasks.discard(t)
                _task_creation_times.pop(t, None)
                logger.warning(
                    "Background task set full (%d), evicted stuck task (age=%.0fs > max=%ds)",
                    _MAX_BG_TASKS,
                    age,
                    _MAX_BG_TASK_AGE_SECONDS,
                )
                evicted = True
                break
        if not evicted:
            logger.warning(
                "Background task set full (%d) but no tasks exceed max age (%ds); "
                "new task will still be added",
                _MAX_BG_TASKS,
                _MAX_BG_TASK_AGE_SECONDS,
            )
    _background_tasks.add(task)
    _task_creation_times[task] = time.monotonic()


def _update_bg_task_age_gauge() -> None:
    """Push the age of the oldest in-flight background task to Prometheus."""
    try:
        from ...core.metrics import BG_TASK_AGE_SECONDS

        if _background_tasks and _task_creation_times:
            now = time.monotonic()
            oldest_age = max(now - _task_creation_times.get(t, now) for t in _background_tasks)
            BG_TASK_AGE_SECONDS.labels(kind="any").set(oldest_age)
    except Exception:
        pass  # metrics are optional; never fail the caller


def safe_create_task(coro, *, name: str = "bg") -> asyncio.Task:
    """Create a background task with automatic done-callback error logging (HIGH-22).

    Unlike bare ``asyncio.create_task()``, exceptions are surfaced via
    ``logger.error`` and the Prometheus ``BACKGROUND_TASK_FAILURES_TOTAL``
    counter instead of being silently swallowed by the event loop.
    """
    from ._common import logger as _chain_logger

    task = asyncio.create_task(coro, name=name)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        _task_creation_times.pop(t, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            try:
                from ...core.metrics import BACKGROUND_TASK_FAILURES_TOTAL

                if isinstance(exc, asyncio.TimeoutError):
                    error_type = "timeout"
                elif isinstance(exc, asyncio.CancelledError):
                    error_type = "cancelled"
                else:
                    error_type = "exception"
                BACKGROUND_TASK_FAILURES_TOTAL.labels(name=name, error_type=error_type).inc()
            except Exception:
                pass  # metrics are optional; never suppress the actual error log below
            _chain_logger.error(
                "Background task %s failed: %s",
                name,
                exc,
                exc_info=exc,
            )

    task.add_done_callback(_on_done)
    _background_tasks.add(task)
    _task_creation_times[task] = time.monotonic()
    return task


def cancel_background_tasks() -> None:
    """Cancel all tracked background tasks (call during shutdown)."""
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    _background_tasks.clear()
    _task_creation_times.clear()
    from ._common import logger

    logger.info("Cancelled %d background tasks", len(tasks))


from ._api import _run_pipeline, ask, ask_stream  # noqa: E402
from ._context import PipelineContext, PipelineResult  # noqa: E402
from ._formatting import _build_system_context, _extract_sources, _format_docs  # noqa: E402
from ._routing import _casual_response, _classify_intent, _is_trivially_short  # noqa: E402
from ._steps_context import _format_memory_context  # noqa: E402

__all__ = [
    "PipelineContext",
    "PipelineResult",
    "_background_tasks",
    "_build_system_context",
    "_casual_response",
    "_classify_intent",
    "_extract_sources",
    "_format_docs",
    "_format_memory_context",
    "_is_trivially_short",
    "_run_pipeline",
    "ask",
    "ask_stream",
    "cancel_background_tasks",
]
