"""Monotonic epoch for runtime settings changes.

Long-running background jobs can capture the epoch at start and stop when the
epoch changes to avoid mixing old/new runtime configuration.

Modules that maintain caches keyed on epoch should register them via
``register_epoch_cache()`` so they are cleared when the epoch bumps.
"""

import logging
import threading
from collections.abc import Callable

_logger = logging.getLogger(__name__)
_settings_epoch = 0
_lock = threading.Lock()
_epoch_caches: list[Callable[[], None]] = []


def get_settings_epoch() -> int:
    """Return current runtime settings epoch."""
    with _lock:
        return _settings_epoch


def register_epoch_cache(clear_fn: Callable[[], None]) -> None:
    """Register a cache-clear callback invoked when the settings epoch bumps.

    Pass a zero-argument callable that clears the cache (e.g. ``dict.clear``).
    """
    with _lock:
        _epoch_caches.append(clear_fn)


def bump_settings_epoch() -> int:
    """Increment the runtime settings epoch and clear all registered caches."""
    global _settings_epoch
    with _lock:
        _settings_epoch += 1
        epoch = _settings_epoch
    # Clear caches outside the lock to avoid deadlocks with cache locks.
    for clear_fn in _epoch_caches:
        try:
            clear_fn()
        except Exception:
            _logger.warning("Failed to clear epoch cache %r", clear_fn, exc_info=True)
    return epoch
