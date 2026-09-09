"""Shared httpx AsyncClient for cloud parser providers.

Thread-safe, event-loop-aware.  One client per event loop so that
concurrent cascade dispatches (each running in its own ``asyncio.run``
loop inside a ThreadPoolExecutor thread) never steal each other's client.
"""

import asyncio
import inspect
import logging
import threading
import weakref

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

# One client per event loop — avoids cross-loop errors when multiple
# ThreadPoolExecutor threads each run their own asyncio.run loop concurrently.
_loop_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_lock = threading.Lock()
_cleanup_tasks: set[asyncio.Task[None]] = set()


def _track_cleanup_task(task: asyncio.Task[None]) -> None:
    with _lock:
        _cleanup_tasks.add(task)

    def _discard(completed: asyncio.Task[None]) -> None:
        with _lock:
            _cleanup_tasks.discard(completed)

    task.add_done_callback(_discard)


def _build_limits() -> httpx.Limits:
    """Build httpx limits with compatibility across httpx versions."""
    limits_kwargs: dict[str, int] = {
        "max_keepalive_connections": 10,
        "max_connections": 50,
    }
    per_host_kwargs = {
        "max_connections_per_host": 15,
        "keepalive_connections_per_host": 5,
    }
    supported_params = inspect.signature(httpx.Limits).parameters
    unsupported_args: list[str] = []
    for key, value in per_host_kwargs.items():
        if key in supported_params:
            limits_kwargs[key] = value
        else:
            unsupported_args.append(key)

    if unsupported_args:
        logger.debug(
            "httpx.Limits does not support %s; falling back to global connection caps only",
            ", ".join(sorted(unsupported_args)),
        )
    return httpx.Limits(**limits_kwargs)


def get_parser_http_client() -> httpx.AsyncClient:
    """Get or create an httpx AsyncClient bound to the current event loop.

    Each event loop gets its own client so that concurrent cascade dispatches
    (each with its own ``asyncio.run`` loop) never interfere with each other.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError(
            "get_parser_http_client must be called from within a running event loop"
        ) from exc

    timeout = getattr(settings, "PARSER_HTTP_TIMEOUT_SECONDS", 180.0)
    cached = _loop_clients.get(loop)
    if cached is not None and cached[0] == timeout:
        return cached[1]

    with _lock:
        cached = _loop_clients.get(loop)
        if cached is not None and cached[0] == timeout:
            return cached[1]
        try:
            # H-14: Per-host connection caps prevent one slow provider (e.g.
            # MinerU long-poll) from starving others that share the pool.
            client = httpx.AsyncClient(
                timeout=timeout,
                limits=_build_limits(),
            )
        except Exception:
            _loop_clients.pop(loop, None)
            raise
        _loop_clients[loop] = (timeout, client)
        logger.debug("Parser httpx client created for loop %s (timeout=%.0fs)", loop, timeout)
    if cached is not None and cached[1] is not client:
        _track_cleanup_task(loop.create_task(cached[1].aclose()))
    return client


async def close_parser_http_client() -> None:
    """Close all parser httpx clients — call from lifespan shutdown."""
    with _lock:
        items = list(_loop_clients.items())
        _loop_clients.clear()

    for loop, (_timeout, client) in items:
        if loop.is_closed():
            continue
        try:
            await client.aclose()
        except Exception:
            logger.warning("Failed to close parser httpx client", exc_info=True)


async def close_parser_http_client_for_current_loop() -> None:
    """Close the client bound to the currently-running loop, if any."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _lock:
        cached = _loop_clients.pop(loop, None)
    if cached is not None:
        _timeout, client = cached
        try:
            await client.aclose()
        except Exception:
            logger.debug("per-loop parser client close raised", exc_info=True)
