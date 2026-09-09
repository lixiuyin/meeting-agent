"""Process-local concurrency controls owned by the service layer."""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from ..core.config import settings

logger = logging.getLogger(__name__)
_stream_semaphore: asyncio.Semaphore | None = None
_stream_semaphore_lock = threading.Lock()
_SESSION_LOCK_STRIPES = 4096
_session_turn_locks: tuple[asyncio.Lock, ...] | None = None
_session_turn_locks_guard = threading.Lock()


def get_stream_semaphore() -> asyncio.Semaphore:
    global _stream_semaphore
    if _stream_semaphore is None:
        with _stream_semaphore_lock:
            if _stream_semaphore is None:
                _stream_semaphore = asyncio.Semaphore(settings.STREAM_CONCURRENT_LIMIT)
                workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
                if workers > 1:
                    logger.warning(
                        "WEB_CONCURRENCY=%d makes the effective stream limit %d",
                        workers,
                        workers * settings.STREAM_CONCURRENT_LIMIT,
                    )
    return _stream_semaphore


def set_stream_semaphore(semaphore: asyncio.Semaphore) -> None:
    global _stream_semaphore
    with _stream_semaphore_lock:
        _stream_semaphore = semaphore


def reset_stream_semaphore() -> None:
    set_stream_semaphore(asyncio.Semaphore(settings.STREAM_CONCURRENT_LIMIT))


def get_session_turn_lock(user_id: str, session_id: str) -> asyncio.Lock:
    """Return a stable lock that serializes turns for one existing session."""
    global _session_turn_locks
    if _session_turn_locks is None:
        with _session_turn_locks_guard:
            if _session_turn_locks is None:
                _session_turn_locks = tuple(asyncio.Lock() for _ in range(_SESSION_LOCK_STRIPES))
    return _session_turn_locks[hash((user_id, session_id)) % _SESSION_LOCK_STRIPES]
