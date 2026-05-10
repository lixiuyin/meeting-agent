"""Loop-aware shared httpx AsyncClient helper."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import httpx


class LoopBoundAsyncClient:
    """Create one AsyncClient per running event loop."""

    def __init__(self, factory: Callable[[], httpx.AsyncClient]) -> None:
        self._factory = factory
        self._client: httpx.AsyncClient | None = None
        self._loop_id: int | None = None
        self._lock = threading.Lock()

    def get(self) -> httpx.AsyncClient:
        current_loop_id: int | None
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None

        if self._client is not None and self._loop_id == current_loop_id:
            return self._client

        # Create outside the lock so the factory (which may create an
        # AsyncClient) doesn't hold the lock while doing I/O or event-loop
        # sensitive work (C-H8).
        new_client = self._factory()
        with self._lock:
            if self._client is None or self._loop_id != current_loop_id:
                self._client = new_client
                self._loop_id = current_loop_id
            else:
                # Another thread beat us; close the redundant client.
                new_client = None
        # A duplicate client was created (another thread won the race);
        # discard it.  The event loop will close it during GC.
        return self._client

    async def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._loop_id = None
        if client is not None:
            await client.aclose()

    def reset(self) -> None:
        with self._lock:
            self._client = None
            self._loop_id = None
