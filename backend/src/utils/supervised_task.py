"""Supervised background task helpers with metrics and optional restart."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..core.metrics import BACKGROUND_TASK_FAILURES_TOTAL

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
        if name in self._tasks:
            logger.warning("Background task %r is being overwritten", name)
        self._tasks[name] = task

    def create(
        self,
        name: str,
        task_factory: Callable[[], Awaitable[None]],
        *,
        max_restarts: int = 0,
        base_backoff_seconds: float = 1.0,
    ) -> asyncio.Task:
        """Create a supervised task and register it."""
        task = create_supervised_task(
            name,
            task_factory,
            max_restarts=max_restarts,
            base_backoff_seconds=base_backoff_seconds,
        )
        self._tasks[name] = task
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
            except Exception:
                attempt += 1
                BACKGROUND_TASK_FAILURES_TOTAL.labels(name=name).inc()
                logger.error(
                    "Background task %s crashed (attempt=%d)",
                    name,
                    attempt,
                    exc_info=True,
                )
                if attempt > max_restarts:
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
