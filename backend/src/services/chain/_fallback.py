"""Fallback circuit breaker for LLM error handling.

Uses a two-tier approach:
1. In-process state (fast, zero-latency for the common case).
2. DB-backed state (survives process restarts, visible to all workers).

For single-worker deployments (recommended with SQLite), the in-process tier
is sufficient and the DB tier adds minimal overhead. Multi-worker deployments
coordinate through the shared DB state.
"""

import logging
import threading
import time

from ...core.config import settings

logger = logging.getLogger(__name__)

# --- In-process tier (fast path) ---
_process_state_lock = threading.Lock()
_process_failure_count = 0
_process_open_until = 0.0

# --- DB-backed tier (cross-worker coordination) ---
_DB_BREAKER_KEY = "fallback_breaker_open_until"
_DB_BREAKER_FAILURES_KEY = "fallback_breaker_failures"


def _read_db_breaker_state() -> tuple[int, float]:
    """Read breaker state from index_state table. Returns (failures, open_until)."""
    try:
        from ...core.database import get_connection

        with get_connection() as conn:
            open_row = conn.execute(
                "SELECT value FROM index_state WHERE key=?", (_DB_BREAKER_KEY,)
            ).fetchone()
            failures_row = conn.execute(
                "SELECT value FROM index_state WHERE key=?", (_DB_BREAKER_FAILURES_KEY,)
            ).fetchone()

        open_until = float(open_row["value"]) if open_row else 0.0
        failures = int(failures_row["value"]) if failures_row else 0
        return failures, open_until
    except Exception:
        logger.debug("DB breaker state unavailable, using in-process only", exc_info=True)
        return 0, 0.0


def _write_db_breaker_state(failures: int, open_until: float) -> None:
    """Persist breaker state to index_state table."""
    try:
        from ...core.database import get_write_connection

        with get_write_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO index_state (key, value) VALUES (?, ?)",
                (_DB_BREAKER_KEY, str(open_until)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO index_state (key, value) VALUES (?, ?)",
                (_DB_BREAKER_FAILURES_KEY, str(failures)),
            )
    except Exception:
        logger.debug("Failed to persist breaker state to DB", exc_info=True)


def is_fallback_circuit_open() -> bool:
    """Check if the fallback circuit breaker is open (calls should be blocked)."""
    global _process_open_until

    # Fast path: check in-process state first
    with _process_state_lock:
        if time.monotonic() < _process_open_until:
            return True
        if _process_open_until == 0.0:
            return False

    # Slow path: check DB state (for multi-worker coordination)
    _, db_open_until = _read_db_breaker_state()
    if db_open_until > time.time():
        with _process_state_lock:
            _process_open_until = time.monotonic() + max(0, db_open_until - time.time())
        return True

    return False


def record_fallback_success() -> None:
    """Record a successful fallback call, resetting the breaker."""
    global _process_failure_count, _process_open_until

    with _process_state_lock:
        _process_failure_count = 0
        _process_open_until = 0.0

    _write_db_breaker_state(0, 0.0)


def record_fallback_failure() -> None:
    """Record a failed fallback call, potentially tripping the breaker."""
    global _process_failure_count, _process_open_until

    threshold = settings.FALLBACK_BREAKER_THRESHOLD
    cooldown = settings.FALLBACK_BREAKER_COOLDOWN_SECONDS

    with _process_state_lock:
        _process_failure_count += 1
        if _process_failure_count >= threshold:
            _process_open_until = time.monotonic() + cooldown
            logger.warning(
                "Fallback circuit breaker tripped after %d failures (cooldown: %ds)",
                _process_failure_count,
                cooldown,
            )

    # Sync to DB for cross-worker visibility
    with _process_state_lock:
        db_open_time = time.time() + cooldown if _process_failure_count >= threshold else 0.0
        db_failures = _process_failure_count
    _write_db_breaker_state(db_failures, db_open_time)
