"""Loop-aware shared httpx AsyncClient helper."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

import httpx


class LoopBoundAsyncClient:
    """Create one AsyncClient per running event loop."""

    def __init__(
        self,
        factory: Callable[[], httpx.AsyncClient],
        *,
        key_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._factory = factory
        self._key_factory = key_factory
        self._client: httpx.AsyncClient | None = None
        self._loop_id: int | None = None
        self._config_key: Any = None
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._lock = threading.Lock()

    def _schedule_close(self, client: httpx.AsyncClient) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(client.aclose())
        with self._lock:
            self._cleanup_tasks.add(task)

        def _discard(completed: asyncio.Task[None]) -> None:
            with self._lock:
                self._cleanup_tasks.discard(completed)

        task.add_done_callback(_discard)

    def get(self) -> httpx.AsyncClient:
        current_loop_id: int | None
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None

        config_key = self._key_factory() if self._key_factory is not None else None
        if (
            self._client is not None
            and self._loop_id == current_loop_id
            and self._config_key == config_key
        ):
            return self._client

        # Create outside the lock so the factory (which may create an
        # AsyncClient) doesn't hold the lock while doing I/O or event-loop
        # sensitive work (C-H8).
        new_client = self._factory()
        stale_client: httpx.AsyncClient | None = None
        redundant_client: httpx.AsyncClient | None = None
        with self._lock:
            if (
                self._client is None
                or self._loop_id != current_loop_id
                or self._config_key != config_key
            ):
                stale_client = self._client
                self._client = new_client
                self._loop_id = current_loop_id
                self._config_key = config_key
            else:
                redundant_client = new_client
        # Close displaced clients on their active loop instead of waiting for
        # garbage collection. This path runs only after a loop/config change.
        for client in (stale_client, redundant_client):
            if client is not None and client is not self._client:
                self._schedule_close(client)
        return self._client

    async def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._loop_id = None
            self._config_key = None
            cleanup_tasks = list(self._cleanup_tasks)
        if client is not None:
            await client.aclose()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    def reset(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._loop_id = None
            self._config_key = None
        if client is not None:
            self._schedule_close(client)
