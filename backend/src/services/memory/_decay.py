import asyncio
import calendar
import math
import time

from ...core import database as db
from ...core.database import get_write_connection
from . import settings
from ._common import logger

# ---- Background decay loop ----
_decay_loop_task: "asyncio.Task[None] | None" = None
_decay_stop_event: asyncio.Event = asyncio.Event()


def _compute_decay_score(
    importance: float,
    last_accessed: str | None,
    decay_rate: float | None = None,
    created_at: str | None = None,
    expires_at: str | None = None,
) -> float:
    """Compute importance decay score. Higher = more important.

    When ``last_accessed`` is NULL the memory has never been touched;
    fall back to ``created_at`` so it still decays instead of staying
    at its initial importance forever (M-H1).

    HIGH-7: If ``expires_at`` is provided and is in the past, returns 0.0
    immediately so expired memories are short-circuited without further
    computation.
    """
    if decay_rate is None:
        decay_rate = float(settings.MEMORY_DECAY_RATE_PER_DAY)
    if expires_at is not None:
        try:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S+00:00"):
                try:
                    exp_ts = calendar.timegm(time.strptime(expires_at[:25], fmt))
                    break
                except ValueError:
                    continue
            else:
                exp_ts = None
            if exp_ts is not None and exp_ts < time.time():
                return 0.0
        except (ValueError, TypeError):
            pass
    ts_str = last_accessed or created_at
    if ts_str is None:
        # HIGH-10: Treat missing timestamps as 365 days old so the memory
        # decays naturally instead of living forever.
        days_elapsed = 365.0
        return importance * math.exp(-decay_rate * days_elapsed)
    try:
        # DB timestamps are UTC; use timegm to avoid local-time drift.
        # Accept both SQL format "YYYY-MM-DD HH:MM:SS" and ISO format.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S+00:00"):
            try:
                last_ts = calendar.timegm(time.strptime(ts_str[:25], fmt))
                break
            except ValueError:
                continue
        else:
            return float(importance)
    except (ValueError, TypeError):
        return float(importance)
    days_elapsed = (time.time() - last_ts) / 86400
    if days_elapsed > settings.MEMORY_TTL_DAYS:
        return 0.0
    return importance * math.exp(-decay_rate * days_elapsed)


def _get_last_decay_time(user_id: str) -> str | None:
    """Get the last decay time for a user from the decay state table."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT last_decay_time FROM memory_decay_state WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return row["last_decay_time"] if row else None


def _set_last_decay_time(user_id: str) -> None:
    """Set the last decay time for a user."""
    with get_write_connection() as conn:
        conn.execute(
            """INSERT INTO memory_decay_state (user_id, last_decay_time)
               VALUES (?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET last_decay_time=CURRENT_TIMESTAMP""",
            (user_id,),
        )


def _get_active_user_ids() -> list[str]:
    """Get all user IDs that have memories in the database."""
    with db.get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT user_id FROM user_memories").fetchall()
        return [r["user_id"] for r in rows]


async def start_memory_decay_loop() -> None:
    """Background loop that runs decay every decay_interval_hours."""
    global _decay_loop_task, _decay_stop_event
    if _decay_stop_event.is_set():
        # An ASGI lifespan may be started more than once in the same process
        # (embedded servers and TestClient both do this). A shutdown signal from
        # the previous lifespan must not permanently disable decay processing.
        _decay_stop_event = asyncio.Event()
    logger.info(
        "Memory decay loop started (interval: %d hours)",
        settings.MEMORY_DECAY_INTERVAL_HOURS,
    )
    consecutive_failures = 0
    while not _decay_stop_event.is_set():
        try:
            user_ids = await asyncio.to_thread(_get_active_user_ids)
            # Import once before loop to reuse singleton
            from . import memory_service as _mem_svc

            # H-MEM-1: Concurrent user processing with bounded parallelism.
            _sem = asyncio.Semaphore(3)

            async def _process_user(uid: str, *, __sem: asyncio.Semaphore = _sem) -> None:
                async with __sem:
                    if settings.MEMORY_DECAY_ENABLED:
                        await asyncio.to_thread(_mem_svc.decay_memories_if_needed, uid)
                        await asyncio.to_thread(_mem_svc.purge_stale_memories, uid)
                    if settings.MEMORY_CONSOLIDATION_ENABLED:
                        try:
                            await asyncio.wait_for(
                                _mem_svc.consolidate_memories(uid),
                                timeout=120.0,
                            )
                        except TimeoutError:
                            logger.warning(
                                "Consolidation timed out for user %s after 120s",
                                uid,
                            )
                        except Exception:
                            logger.warning(
                                "Consolidation failed for user %s",
                                uid,
                                exc_info=True,
                            )

            results = await asyncio.gather(
                *[_process_user(uid) for uid in user_ids],
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                for failure in failures:
                    logger.error("Memory decay user task failed: %s", failure)
                raise RuntimeError(f"{len(failures)} memory decay user task(s) failed")
            consecutive_failures = 0
        except Exception:
            logger.exception("Decay loop error")
            consecutive_failures += 1
            if consecutive_failures >= 5:
                logger.warning(
                    "Decay loop has failed %d consecutive times; "
                    "pausing for 1 hour before retry (M-8)",
                    consecutive_failures,
                )
        # Wait for next interval or stop signal, with backoff on persistent
        # failures to prevent tight-looping on DB lock / Chroma errors.
        backoff_s = min(60 * 2**consecutive_failures, 3600)
        normal_s = settings.MEMORY_DECAY_INTERVAL_HOURS * 3600
        delay_s = max(normal_s, backoff_s)
        try:
            await asyncio.wait_for(
                _decay_stop_event.wait(),
                timeout=delay_s,
            )
            break
        except TimeoutError:
            pass
    logger.info("Memory decay loop stopped")


def stop_memory_decay_loop() -> None:
    """Signal the background decay loop to stop."""
    global _decay_stop_event
    _decay_stop_event.set()
