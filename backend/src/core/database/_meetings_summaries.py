"""Meeting and file summary persistence."""

import json
import logging
import sqlite3

from ._meetings_crud import update_meeting_status

logger = logging.getLogger(__name__)


def update_meeting_summary_status(meeting_id: int, status: str) -> None:
    """Set the summary_status for a meeting. Thread-safe (gets own connection)."""
    from . import get_write_connection

    with get_write_connection() as conn:
        conn.execute(
            "UPDATE meetings SET summary_status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, meeting_id),
        )


def update_file_summary_status(file_id: int, status: str) -> None:
    """Set the summary_status for a meeting file. Thread-safe (gets own connection)."""
    from . import get_write_connection

    with get_write_connection() as conn:
        conn.execute(
            "UPDATE meeting_files SET summary_status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, file_id),
        )


def save_meeting_summary(
    meeting_id: int,
    summary: str,
    contributing_file_ids: list[int],
) -> None:
    """Persist meeting-level summary, mark summary_status ready, flip status to ready."""
    from . import get_write_connection

    with get_write_connection() as conn:
        conn.execute(
            """INSERT INTO meeting_summaries (meeting_id, summary, contributing_file_ids)
               VALUES (?, ?, ?)
               ON CONFLICT(meeting_id) DO UPDATE SET
               summary=excluded.summary,
               contributing_file_ids=excluded.contributing_file_ids,
               updated_at=CURRENT_TIMESTAMP""",
            (meeting_id, summary, json.dumps(contributing_file_ids)),
        )
        conn.execute(
            "UPDATE meetings SET summary_status='ready', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (meeting_id,),
        )
        # Atomically bump lifecycle status (summarizing -> ready or ready -> ready)
        # within the same write connection so it can never be inconsistent.
        update_meeting_status(conn, meeting_id, "ready")


def get_meeting_summary(meeting_id: int) -> dict | None:
    """Return the persisted meeting-level summary for *meeting_id*, or None."""
    from . import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT summary, contributing_file_ids, created_at, updated_at "
            "FROM meeting_summaries WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "summary": row["summary"],
        "contributing_file_ids": json.loads(row["contributing_file_ids"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_meeting_summary_with_status(meeting_id: int) -> dict | None:
    """Return summary content + lifecycle status for *meeting_id*.

    Returns ``None`` when the meeting itself does not exist (caller should 404).
    Otherwise always returns a dict — callers do not need to check for a
    missing summary row separately.
    ``status`` is one of 'pending' | 'generating' | 'ready' | 'failed'.
    ``summary`` is None when status is not 'ready'.
    """
    from . import get_connection

    with get_connection() as conn:
        status_row = conn.execute(
            "SELECT summary_status FROM meetings WHERE id=?",
            (meeting_id,),
        ).fetchone()
        if status_row is None:
            return None
        summary_row = conn.execute(
            "SELECT summary, contributing_file_ids, created_at, updated_at "
            "FROM meeting_summaries WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()

    status = status_row["summary_status"]
    if summary_row:
        return {
            "status": status,
            "summary": summary_row["summary"],
            "contributing_file_ids": json.loads(summary_row["contributing_file_ids"]),
            "created_at": summary_row["created_at"],
            "updated_at": summary_row["updated_at"],
        }
    return {"status": status, "summary": None}


def clear_file_summary(conn: sqlite3.Connection, file_id: int) -> None:
    """Clear per-file summary and reset its summary_status to 'pending'."""
    conn.execute(
        "UPDATE meeting_files SET summary=NULL, key_points_json=NULL, "
        "summary_status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (file_id,),
    )
    # Also remove from vector + BM25 stores so stale entries don't linger.
    try:
        from ...services.rag._summary_vectorstore import delete_file_summary

        delete_file_summary(file_id)
    except Exception:
        logger.debug("File summary vector delete failed for file %s", file_id, exc_info=True)
    try:
        from .bm25 import delete_file_summary_bm25

        delete_file_summary_bm25(conn, file_id)
    except Exception:
        logger.debug("File summary BM25 delete failed for file %s", file_id, exc_info=True)


def clear_meeting_summary(conn: sqlite3.Connection, meeting_id: int) -> None:
    """Delete meeting-level summary for *meeting_id* from DB and reset status."""
    conn.execute("DELETE FROM meeting_summaries WHERE meeting_id=?", (meeting_id,))
    conn.execute(
        "UPDATE meetings SET summary_status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (meeting_id,),
    )
    # Also remove from meeting-summary vector store.
    try:
        from ...services.rag._meeting_summary_vectorstore import delete_meeting_summary

        delete_meeting_summary(meeting_id)
    except Exception:
        logger.debug(
            "Meeting summary vector delete failed for meeting %s",
            meeting_id,
            exc_info=True,
        )
