"""Supervised background task helpers with metrics and optional restart."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from ..core.metrics import BACKGROUND_TASK_EXHAUSTED_TOTAL, BACKGROUND_TASK_FAILURES_TOTAL

logger = logging.getLogger(__name__)


class BackgroundTaskRegistry:
    """Central registry for supervised background tasks (L-1).

    Provides a single point to register, cancel, and introspect all long-running
    background tasks.  Replaces ad-hoc local-variable task tracking scattered
    across the lifespan module.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, name: str, task: asyncio.Task) -> None:
        if name in self._tasks and not self._tasks[name].done():
            logger.warning("Background task %r is being overwritten", name)
        self._tasks[name] = task

        def _remove_completed(completed: asyncio.Task) -> None:
            if self._tasks.get(name) is completed:
                self._tasks.pop(name, None)
            if completed.cancelled():
                return
            # Retrieve the exception even for supervised tasks.  The runner
            # already records/logs it, while retrieving it here prevents the
            # event loop from emitting a late "exception was never retrieved"
            # warning during shutdown.
            with contextlib.suppress(asyncio.CancelledError):
                completed.exception()

        task.add_done_callback(_remove_completed)

    def create(
        self,
        name: str,
        task_factory: Callable[[], Awaitable[None]],
        *,
        max_restarts: int = 0,
        base_backoff_seconds: float = 1.0,
    ) -> asyncio.Task:
        """Create a supervised task and register it."""
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            logger.info("Background task %r is already active; reusing it", name)
            return existing
        task = create_supervised_task(
            name,
            task_factory,
            max_restarts=max_restarts,
            base_backoff_seconds=base_backoff_seconds,
        )
        self.register(name, task)
        return task

    def cancel_all(self) -> list[tuple[str, asyncio.Task]]:
        """Cancel all registered tasks. Returns list of (name, task) pairs."""
        cancelled: list[tuple[str, asyncio.Task]] = []
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                cancelled.append((name, task))
        return cancelled

    async def wait_all(self, timeout: float = 5.0) -> None:
        """Wait for all registered tasks to complete (after cancellation)."""
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            _done, _pending = await asyncio.wait(pending, timeout=timeout)
            for t in _pending:
                logger.warning(
                    "Background task %r did not finish within %.0fs",
                    t.get_name(),
                    timeout,
                )

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    @property
    def task_names(self) -> list[str]:
        return list(self._tasks.keys())

    def is_active(self, name: str) -> bool:
        task = self._tasks.get(name)
        return bool(task and not task.done())

    async def wait_for(self, name: str, timeout: float | None = None) -> bool:
        """Wait for one named task, primarily for coordinated callers/tests.

        Returns ``False`` when the task already finished and left the registry.
        ``shield`` ensures a caller timeout does not cancel durable work.
        """
        task = self._tasks.get(name)
        if task is None:
            return False
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True


# Module-level singleton
_background_tasks = BackgroundTaskRegistry()


def get_background_tasks() -> BackgroundTaskRegistry:
    return _background_tasks


def create_supervised_task(
    name: str,
    task_factory: Callable[[], Awaitable[None]],
    *,
    max_restarts: int = 0,
    base_backoff_seconds: float = 1.0,
) -> asyncio.Task:
    """Create a supervised task that tracks failures and optionally restarts."""

    async def _runner() -> None:
        attempt = 0
        while True:
            try:
                await task_factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                error_type = type(exc).__name__
                try:
                    BACKGROUND_TASK_FAILURES_TOTAL.labels(
                        name=name,
                        error_type=error_type,
                    ).inc()
                except Exception:
                    logger.debug("Failed to record background task failure metric", exc_info=True)
                logger.error(
                    "Background task %s crashed (attempt=%d)",
                    name,
                    attempt,
                    exc_info=True,
                )
                if attempt > max_restarts:
                    try:
                        BACKGROUND_TASK_EXHAUSTED_TOTAL.labels(name=name).inc()
                    except Exception:
                        logger.debug(
                            "Failed to record background task exhaustion metric",
                            exc_info=True,
                        )
                    logger.critical(
                        "Background task %s exhausted max_restarts=%d — "
                        "this task will NOT be restarted and the associated "
                        "functionality is now permanently disabled until the "
                        "next process restart.",
                        name,
                        max_restarts,
                    )
                    raise
                backoff = base_backoff_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

    return asyncio.create_task(_runner(), name=f"supervised:{name}")
