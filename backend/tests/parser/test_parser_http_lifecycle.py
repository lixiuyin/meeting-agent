"""Tests for parser httpx client lifecycle — per-loop cleanup and dead-loop shutdown.

Reproduces the ThreadPoolExecutor + asyncio.run pattern and verifies that
shutdown is clean (no ``Event loop is closed`` warnings).
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.services.parser._http import (
    _loop_clients,
    close_parser_http_client,
    close_parser_http_client_for_current_loop,
    get_parser_http_client,
)


@pytest.mark.anyio
async def test_close_parser_http_client_skips_dead_loops(caplog):
    """Clients bound to closed loops must not surface 'Event loop is closed' warnings."""

    def worker() -> None:
        """Simulate a cascade dispatch thread that creates a client but never cleans up."""

        async def inner() -> None:
            # Force client creation in this loop
            get_parser_http_client()

        asyncio.run(inner())
        # Loop is now closed — client lingers in _loop_clients until GC

    # Run the worker in a ThreadPoolExecutor and wait for its loop to close
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        await loop.run_in_executor(ex, worker)

    caplog.set_level(logging.WARNING, logger="src.services.parser._http")
    await close_parser_http_client()

    assert not any("Event loop is closed" in r.message for r in caplog.records), (
        f"Unexpected warnings: {[r.message for r in caplog.records]}"
    )


@pytest.mark.anyio
async def test_close_parser_http_client_for_current_loop_clears_entry():
    """Per-loop cleanup removes the client from _loop_clients and closes it."""
    client = get_parser_http_client()
    loop = asyncio.get_running_loop()
    assert loop in _loop_clients

    await close_parser_http_client_for_current_loop()

    assert loop not in _loop_clients
    assert client.is_closed


@pytest.mark.anyio
async def test_close_parser_http_client_for_current_loop_idempotent():
    """Calling per-loop cleanup when no client exists is safe."""
    loop = asyncio.get_running_loop()
    # Ensure no client for this loop
    if loop in _loop_clients:
        await close_parser_http_client_for_current_loop()

    await close_parser_http_client_for_current_loop()  # should not raise


@pytest.mark.anyio
async def test_worker_thread_cleans_up_its_client():
    """When a worker thread calls close_parser_http_client_for_current_loop,
    the client is removed before the thread's loop closes."""

    saved_count = len(_loop_clients)

    def worker() -> None:
        async def inner() -> None:
            get_parser_http_client()
            await close_parser_http_client_for_current_loop()

        asyncio.run(inner())

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        await loop.run_in_executor(ex, worker)

    # The worker's loop is closed, but its client should have been removed
    # by close_parser_http_client_for_current_loop — so _loop_clients
    # should not have grown.
    assert len(_loop_clients) <= saved_count
