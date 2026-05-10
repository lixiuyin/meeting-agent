"""Recovery tasks for meetings stuck in 'processing' state."""

import logging
import sqlite3

from ...core.database import get_write_connection

logger = logging.getLogger(__name__)

# Only recover records stuck longer than this grace period (minutes).
# This prevents racing with in-flight tasks during graceful shutdown.
_GRACE_PERIOD_MINUTES = 30


def recover_stale_meetings() -> int:
    """Reset meetings and files stuck in 'processing' state (e.g. after crash).

    Only recovers records that have been stuck for longer than the grace period
    to avoid racing with in-flight tasks during graceful shutdown.

    Uses the app's connection pool (``get_write_connection``) instead of a
    separate ``BEGIN IMMEDIATE`` connection so we don't fight the pool for
    database locks during startup.

    Returns the number of meetings reset.
    """
    grace_expr = f"-{_GRACE_PERIOD_MINUTES} minutes"

    try:
        with get_write_connection() as conn:
            return _do_recover(conn, grace_expr)
    except Exception as exc:
        logger.warning("Recovery failed: %s", exc, exc_info=True)
        return 0


def _do_recover(conn: sqlite3.Connection, grace_expr: str) -> int:
    # Reset stuck files (only those stuck longer than grace period)
    cursor = conn.execute(
        "UPDATE meeting_files SET status='error', error_message='Processing interrupted', "
        "processing_started_at=NULL, updated_at=CURRENT_TIMESTAMP "
        "WHERE status='processing' "
        "AND COALESCE(processing_started_at, updated_at) < datetime('now', ?)",
        (grace_expr,),
    )
    file_count = cursor.rowcount
    if file_count:
        logger.warning("Recovered %d files stuck in 'processing' state", file_count)

    # Reset stuck meetings (only those stuck longer than grace period)
    cursor = conn.execute(
        "UPDATE meetings SET status='failed', processing_started_at=NULL, "
        "updated_at=CURRENT_TIMESTAMP "
        "WHERE status='processing' "
        "AND COALESCE(processing_started_at, updated_at) < datetime('now', ?)",
        (grace_expr,),
    )
    meeting_count = cursor.rowcount
    if meeting_count:
        from ...core.metrics import RECOVERY_KILLED_ACTIVE_SUSPECT_TOTAL

        RECOVERY_KILLED_ACTIVE_SUSPECT_TOTAL.inc(meeting_count)
        logger.warning("Recovered %d meetings stuck in 'processing' state", meeting_count)

    # Recover files stuck in 'summarizing' or 'generating' (>2x grace period).
    summarizing_grace = f"-{_GRACE_PERIOD_MINUTES * 2} minutes"
    cursor = conn.execute(
        "UPDATE meeting_files SET status='ready', updated_at=CURRENT_TIMESTAMP "
        "WHERE status IN ('summarizing', 'generating') "
        "AND COALESCE(processing_started_at, updated_at) < datetime('now', ?)",
        (summarizing_grace,),
    )
    summarizing_file_count = cursor.rowcount
    if summarizing_file_count:
        logger.warning(
            "Recovered %d files stuck in 'summarizing'/'generating' state",
            summarizing_file_count,
        )

    # Reset summary_status stuck on 'pending'.
    # Use double the grace period (60 min) because summary generation is a
    # post-processing step that only starts after the main processing completes.
    double_grace = f"-{_GRACE_PERIOD_MINUTES * 2} minutes"
    cursor = conn.execute(
        "UPDATE meetings SET summary_status='failed', "
        "updated_at=CURRENT_TIMESTAMP "
        "WHERE summary_status='pending' "
        "AND COALESCE(processing_started_at, updated_at) < datetime('now', ?)",
        (double_grace,),
    )
    pending_count = cursor.rowcount
    if pending_count:
        logger.warning(
            "Reset summary_status to 'failed' for %d meetings stuck on 'pending'",
            pending_count,
        )

    cursor = conn.execute(
        "UPDATE meeting_files SET summary_status='failed', "
        "updated_at=CURRENT_TIMESTAMP "
        "WHERE summary_status='pending' "
        "AND COALESCE(processing_started_at, updated_at) < datetime('now', ?)",
        (double_grace,),
    )
    file_pending_count = cursor.rowcount
    if file_pending_count:
        logger.warning(
            "Reset summary_status to 'failed' for %d files stuck on 'pending'",
            file_pending_count,
        )

    return meeting_count
