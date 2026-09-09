"""Data retention service — periodic cleanup of old records.

Removes stale chat messages and decay state entries based on configurable
retention windows.  Called from the lifespan background task loop.
"""

import logging
import sqlite3

from ..core.config import settings
from ..core.database import get_write_connection

logger = logging.getLogger(__name__)


def purge_old_chat_messages() -> int:
    """Delete chat messages older than ``CHAT_MESSAGE_RETENTION_DAYS``.

    Cascade deletes will also remove matching FTS5 rows.
    Returns the number of deleted messages.
    """
    cutoff_days = settings.CHAT_MESSAGE_RETENTION_DAYS
    if cutoff_days <= 0:
        return 0

    try:
        with get_write_connection() as conn:
            summary_rows = conn.execute(
                """
                SELECT ss.embedding_id
                FROM session_summaries ss
                JOIN chat_sessions cs ON cs.id = ss.session_id
                WHERE cs.updated_at < datetime('now', ?)
                  AND ss.embedding_id IS NOT NULL
                """,
                (f"-{cutoff_days} days",),
            ).fetchall()
            conn.executemany(
                "INSERT OR IGNORE INTO pending_vector_deletions (collection, embedding_id) "
                "VALUES ('session_summary', ?)",
                [(row["embedding_id"],) for row in summary_rows],
            )

            # Delete messages from sessions whose last activity is beyond the cutoff
            result = conn.execute(
                """
                DELETE FROM chat_messages
                WHERE session_id IN (
                    SELECT s.id FROM chat_sessions s
                    WHERE s.updated_at < datetime('now', ?)
                )
                """,
                (f"-{cutoff_days} days",),
            )
            deleted = result.rowcount

            # Also delete the empty sessions themselves
            conn.execute(
                """
                DELETE FROM chat_sessions
                WHERE updated_at < datetime('now', ?)
                AND id NOT IN (SELECT DISTINCT session_id FROM chat_messages)
                """,
                (f"-{cutoff_days} days",),
            )

        if deleted:
            logger.info("Purged %d chat messages older than %d days", deleted, cutoff_days)
        return deleted
    except (sqlite3.DatabaseError, OSError):
        logger.warning("Failed to purge old chat messages", exc_info=True)
        raise


def purge_old_decay_state() -> int:
    """Delete fully-decayed memory entries older than ``DECAY_STATE_RETENTION_DAYS``.

    Only removes memories whose importance has decayed below 0.1 (effectively
    dead) and whose last access is beyond the retention window.
    Returns the number of deleted rows.
    """
    cutoff_days = settings.DECAY_STATE_RETENTION_DAYS
    if cutoff_days <= 0:
        return 0

    try:
        with get_write_connection() as conn:
            result = conn.execute(
                """
                DELETE FROM memory_decay_state
                WHERE current_score < 0.1
                AND last_accessed < datetime('now', ?)
                """,
                (f"-{cutoff_days} days",),
            )
            deleted = result.rowcount

        if deleted:
            logger.info(
                "Purged %d fully-decayed memory states older than %d days",
                deleted,
                cutoff_days,
            )
        return deleted
    except (sqlite3.DatabaseError, OSError):
        logger.warning("Failed to purge old decay states", exc_info=True)
        raise


def purge_stale_low_importance_memories() -> int:
    """Archive recall for old, low-importance memories that were never accessed.

    These are memories that have fallen below the decay floor (importance <
    0.5), haven't been accessed in 90+ days, and are not profile memories.
    Their fact versions remain available for business history.

    Returns the number of recall-archived memories; fact history is preserved.
    """
    try:
        with get_write_connection() as conn:
            # Record vector cleanup before retiring active recall.
            rows = conn.execute(
                """
                SELECT id, key, embedding_id FROM user_memories
                WHERE importance < 0.5
                  AND (last_accessed IS NULL OR last_accessed < datetime('now', '-90 days'))
                  AND superseded_by IS NULL
                  AND expires_at IS NULL
                  AND key != '__profile__' AND archived_at IS NULL
                """
            ).fetchall()

            if not rows:
                return 0

            from ..core.database.memory_lifecycle import archive_memories

            deleted = archive_memories(conn, rows, reason="retention")

        if deleted:
            logger.info("Global retention: archived %d stale low-importance memories", deleted)
        return deleted
    except Exception:
        logger.warning("Failed to purge stale low-importance memories", exc_info=True)
        raise
