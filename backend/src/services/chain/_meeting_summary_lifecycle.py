"""Centralised meeting summary lifecycle: invalidate + enqueue rebuild."""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from time import monotonic

logger = logging.getLogger(__name__)

# Module-level uuid identifies this process for stale-lock detection.
_LOCK_OWNER = uuid.uuid4().hex[:12]

# Fallback: how long before a 'generating' lock is considered stale (minutes).
_STALE_LOCK_TIMEOUT_MINUTES = 30

# Debounce: skip invalidation if the same meeting was invalidated within
# the last 30 seconds.  Prevents N rapid deletes from triggering N rebuilds.
_INVALIDATE_DEBOUNCE_S = 30
_invalidate_timestamps: dict[int, float] = {}
_invalidate_debounce_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Inflight guard — DB-backed lock for cross-worker / crash-safe dedup.
# ---------------------------------------------------------------------------


def acquire_summary_inflight(meeting_id: int) -> bool:
    """Try to acquire the inflight lock for *meeting_id*.

    Uses ``summary_status='generating'`` + ``summary_lock_owner`` in the DB
    so the lock survives process crashes and is visible across workers.

    Returns ``True`` if acquired, ``False`` if already held.
    """
    from ...core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            row = conn.execute(
                "UPDATE meetings "
                "SET summary_status='generating', summary_lock_owner=?, "
                "    updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND summary_status NOT IN ('generating') "
                "RETURNING summary_lock_owner",
                (_LOCK_OWNER, meeting_id),
            ).fetchone()
            if row is None:
                # Meeting does not exist or already locked — if it exists,
                # check whether we own the lock OR the lock is stale (M-3).
                existing = conn.execute(
                    "SELECT summary_lock_owner, updated_at FROM meetings WHERE id=?",
                    (meeting_id,),
                ).fetchone()
                if existing is None:
                    return True  # Meeting does not exist — let the caller 404
                if existing["summary_lock_owner"] == _LOCK_OWNER:
                    return True
                # M-3: Force-release stale locks held longer than TTL
                stale = conn.execute(
                    "SELECT 1 FROM meetings WHERE id=? AND summary_status='generating' "
                    "AND updated_at < datetime('now', ?)",
                    (meeting_id, f"-{_STALE_LOCK_TIMEOUT_MINUTES} minutes"),
                ).fetchone()
                if stale:
                    logger.warning(
                        "Force-releasing stale summary lock for meeting %d (held > %d min)",
                        meeting_id,
                        _STALE_LOCK_TIMEOUT_MINUTES,
                    )
                    conn.execute(
                        "UPDATE meetings SET summary_lock_owner=NULL, summary_status='pending', "
                        "    updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        (meeting_id,),
                    )
                    # Retry acquisition
                    retry = conn.execute(
                        "UPDATE meetings "
                        "SET summary_status='generating', summary_lock_owner=?, "
                        "    updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND summary_status NOT IN ('generating') "
                        "RETURNING summary_lock_owner",
                        (_LOCK_OWNER, meeting_id),
                    ).fetchone()
                    return retry is not None
                return False
            return True  # We acquired the lock (RETURNING confirmed our owner)
    except Exception:
        logger.warning(
            "DB lock acquire failed for meeting %d; falling back to deny",
            meeting_id,
            exc_info=True,
        )
        return False


def release_summary_inflight(meeting_id: int) -> None:
    """Release the inflight lock for *meeting_id*."""
    from ...core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            conn.execute(
                "UPDATE meetings SET summary_lock_owner=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND summary_lock_owner=?",
                (meeting_id, _LOCK_OWNER),
            )
    except Exception:
        logger.warning("DB lock release failed for meeting %d", meeting_id, exc_info=True)


def recover_stale_generating_summaries() -> int:
    """Reset meetings stuck in ``summary_status='generating'`` on startup.

    A row is stale when ``updated_at`` is older than
    ``_STALE_LOCK_TIMEOUT_MINUTES``.  Returns the count of reset rows.
    """
    from ...core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            conn.execute(
                "UPDATE meetings "
                "SET summary_status='pending', summary_lock_owner=NULL, "
                "    updated_at=CURRENT_TIMESTAMP "
                "WHERE summary_status='generating' "
                "    AND updated_at < datetime('now', ?)",
                (f"-{_STALE_LOCK_TIMEOUT_MINUTES} minutes",),
            )
            row = conn.execute("SELECT changes()").fetchone()
            count = row[0] if row else 0
            if count:
                logger.info("Recovered %d stale 'generating' meeting summaries on startup", count)
            return count
    except Exception:
        logger.warning("Stale summary recovery failed", exc_info=True)
        return 0


def invalidate_meeting_summary(meeting_id: int, *, force: bool = False) -> None:
    """Mark meeting summary as stale and clear persisted data + all vectors.

    Debounced: skips if the same meeting was invalidated within the last
    ``_INVALIDATE_DEBOUNCE_S`` seconds.  Pass ``force=True`` to bypass the
    debounce — required when the underlying content materially changed
    (speaker rename, transcript edit) and a stale summary would mislead.
    """
    if not force:
        now = monotonic()
        with _invalidate_debounce_lock:
            last = _invalidate_timestamps.get(meeting_id, 0)
            if now - last < _INVALIDATE_DEBOUNCE_S:
                logger.debug(
                    "Skipping invalidation for meeting %d (debounced, last %.1fs ago)",
                    meeting_id,
                    now - last,
                )
                return
            _invalidate_timestamps[meeting_id] = now
    else:
        # Reset the debounce window so a subsequent non-forced call within
        # the next window still respects the most recent invalidation time.
        with _invalidate_debounce_lock:
            _invalidate_timestamps[meeting_id] = monotonic()

    _invalidate_meeting_summary_force(meeting_id)


def _invalidate_meeting_summary_force(meeting_id: int) -> None:
    """Unconditional invalidation — used by debounce wrapper and startup recovery."""
    from ...core.database import get_write_connection
    from ...core.database.meetings import clear_meeting_summary

    with get_write_connection() as conn:
        clear_meeting_summary(conn, meeting_id)

    # Best-effort vector cleanup — dedicated meeting-summary collection
    try:
        from ..rag._meeting_summary_vectorstore import delete_meeting_summary

        delete_meeting_summary(meeting_id)
    except Exception:
        logger.debug("Failed to delete meeting summary vector for %d", meeting_id, exc_info=True)

    # Legacy: remove from main chunk vectorstore if present
    try:
        from ..rag._summary_vectorstore import delete_meeting_summaries

        delete_meeting_summaries(meeting_id)
    except Exception:
        logger.debug(
            "Failed to delete legacy meeting summary vector for %d",
            meeting_id,
            exc_info=True,
        )


def persist_meeting_summary(
    meeting_id: int,
    title: str,
    summary: str,
    contributing_file_ids: list[int],
    contributing_file_names: list[str] | None = None,
) -> None:
    """Persist meeting-level summary to DB, BM25, and vector store.

    Wraps the three independent write channels (SQLite, BM25, Chroma) behind
    a single call.  Each channel failure is logged independently — one
    failure does not prevent the others from being attempted.

    Idempotent: computes ``sha256(summary)`` and stores it in vector
    metadata.  On re-summary with identical text the embedding + vector
    upsert is skipped, saving API cost.
    """
    summary = (summary or "").strip()
    if not summary:
        logger.warning("Empty summary received for meeting %d, skipping persist", meeting_id)
        return

    content_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

    # 1. SQLite (save_meeting_summary also transitions meeting -> ready atomically)
    try:
        from ...core.database.meetings import save_meeting_summary

        save_meeting_summary(meeting_id, summary, contributing_file_ids)
    except Exception:
        logger.error(
            "Failed to persist meeting summary to DB for meeting %d",
            meeting_id,
            exc_info=True,
        )

    # 2. Vector store (with idempotent skip)
    try:
        from ..rag._meeting_summary_vectorstore import upsert_meeting_summary

        upsert_meeting_summary(
            meeting_id=meeting_id,
            title=title,
            summary=summary,
            contributing_file_ids=contributing_file_ids,
            contributing_file_names=contributing_file_names,
            content_hash=content_hash,
        )
    except Exception:
        logger.error(
            "Failed to persist meeting summary vector for meeting %d",
            meeting_id,
            exc_info=True,
        )


def reconcile_meeting_summaries(limit: int = 200) -> dict[str, int]:
    """Startup reconcile: verify vector existence for summaries in DB.

    Returns counts: ``{"checked": N, "repaired": M}``.
    """
    from ...core.database import get_connection

    checked = 0
    repaired = 0
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT ms.meeting_id, m.title, ms.summary, ms.contributing_file_ids "
                "FROM meeting_summaries ms "
                "JOIN meetings m ON m.id = ms.meeting_id "
                "WHERE m.summary_status = 'ready' AND ms.summary IS NOT NULL "
                "LIMIT ?",
                (limit,),
            ).fetchall()

        from ..rag._meeting_summary_vectorstore import get_meeting_summary_vectorstore

        store = get_meeting_summary_vectorstore()

        for row in rows:
            checked += 1
            doc_id = f"ms_{row['meeting_id']}"
            try:
                existing = store._collection.get(ids=[doc_id], include=["metadatas"])
                if existing and existing.get("ids") and existing["ids"][0] == doc_id:
                    continue
            except Exception:
                logger.debug("Vector existence check failed for %s", doc_id, exc_info=True)

            # Missing — re-upsert
            summary_text = (row["summary"] or "").strip()
            if not summary_text:
                continue
            try:
                import json as _json

                cids = (
                    _json.loads(row["contributing_file_ids"])
                    if row["contributing_file_ids"]
                    else []
                )
                persist_meeting_summary(
                    meeting_id=row["meeting_id"],
                    title=row["title"],
                    summary=summary_text,
                    contributing_file_ids=cids,
                )
                repaired += 1
            except Exception:
                logger.warning(
                    "Reconcile: failed to re-persist meeting %d",
                    row["meeting_id"],
                    exc_info=True,
                )

        if repaired:
            logger.info("Meeting summary reconcile: checked=%d, repaired=%d", checked, repaired)
    except Exception:
        logger.warning("Meeting summary reconcile failed", exc_info=True)

    return {"checked": checked, "repaired": repaired}


async def enqueue_meeting_summary_rebuild(meeting_id: int) -> None:
    """Fire-and-forget rebuild of meeting-level summary (if meeting is ready).

    Must be called from an async context so ``asyncio.create_task`` can
    schedule the background generation.
    """
    from ..processor._pipeline_summary import _maybe_trigger_meeting_summary

    try:
        _maybe_trigger_meeting_summary(meeting_id)
    except Exception:
        logger.debug(
            "Failed to enqueue meeting summary rebuild for %d",
            meeting_id,
            exc_info=True,
        )
