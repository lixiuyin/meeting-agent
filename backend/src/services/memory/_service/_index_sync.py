"""Versioned memory-index publication and bounded durable reconciliation."""

import asyncio
import contextlib
import logging

from ....core import database as db
from ....core.memory_policy import is_active_memory
from .._vectorstore import get_memory_vectorstore

logger = logging.getLogger(__name__)


def index_current_memory(user_id: str, key: str) -> bool:
    from ._search import _decode_scope_ids

    with db.get_connection() as conn:
        row = db.get_memory_full(conn, user_id=user_id, key=key)
    if not row or not is_active_memory(row):
        return False
    identity = (row["id"], row["revision"])
    try:
        embedding_id = get_memory_vectorstore().upsert(
            user_id,
            key,
            row["value"],
            row["importance"],
            row["category"],
            meeting_ids=_decode_scope_ids(row.get("meeting_ids")),
            file_ids=_decode_scope_ids(row.get("file_ids")),
            generation=f"{identity[0]}:{identity[1]}",
        )
        if not embedding_id:
            raise RuntimeError("Memory embedding unavailable")
        with db.get_write_connection() as conn:
            published = (
                conn.execute(
                    "UPDATE user_memories SET embedding_id=?, vector_state='synced', "
                    "vector_attempts=0, vector_retry_at=NULL "
                    "WHERE id=? AND revision=? AND superseded_by IS NULL "
                    "AND archived_at IS NULL",
                    (embedding_id, *identity),
                ).rowcount
                == 1
            )
            obsolete = row.get("embedding_id") if published else embedding_id
            if obsolete and (not published or obsolete != embedding_id):
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions(collection, embedding_id) "
                    "VALUES ('memory', ?)",
                    (obsolete,),
                )
        return published
    except Exception:
        logger.warning("Memory index publication failed for %s/%s", user_id, key, exc_info=True)
        with db.get_write_connection() as conn:
            conn.execute(
                "UPDATE user_memories SET vector_state='failed', "
                "vector_attempts=vector_attempts+1, "
                "vector_retry_at=datetime('now', '+' || min(3600, 30 * (1 << "
                "min(vector_attempts, 7))) || ' seconds') "
                "WHERE id=? AND revision=? AND archived_at IS NULL",
                identity,
            )
        return False


def reconcile_memory_vectors(limit: int = 100) -> int:
    # The pending state is written in the fact transaction. Retries never
    # rewrite the fact, its revision, timestamp, evidence, or scope.
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, key FROM user_memories WHERE superseded_by IS NULL "
            "AND assertion_status='confirmed' AND archived_at IS NULL "
            "AND (expires_at IS NULL OR julianday(expires_at)>julianday('now')) "
            "AND (valid_to IS NULL OR julianday(valid_to)>julianday('now')) "
            "AND (valid_from IS NULL OR julianday(valid_from)<=julianday('now')) "
            "AND (embedding_id IS NULL OR vector_state IN ('pending', 'failed')) "
            "AND vector_attempts < 8 "
            "AND (vector_retry_at IS NULL OR vector_retry_at <= CURRENT_TIMESTAMP) "
            "ORDER BY updated_at, id LIMIT ?",
            (limit,),
        ).fetchall()
    return sum(index_current_memory(row["user_id"], row["key"]) for row in rows)


_index_wake_event: asyncio.Event | None = None


def wake_memory_index_reconcile() -> None:
    """Wake the single consumer; SQL pending state survives absent consumers."""
    if _index_wake_event is not None:
        _index_wake_event.set()


async def memory_index_reconcile_loop() -> None:
    global _index_wake_event
    event = asyncio.Event()
    _index_wake_event = event
    try:
        while True:
            # Clear before processing so writes during publication cause the
            # next pass immediately; polling also recovers lost wakeups/crashes.
            event.clear()
            try:
                await asyncio.to_thread(reconcile_memory_vectors)
            except Exception:
                logger.warning("Periodic memory index reconciliation failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=60)
    finally:
        if _index_wake_event is event:
            _index_wake_event = None
