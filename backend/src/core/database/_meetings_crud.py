"""Meeting CRUD operations."""

import logging
import sqlite3
from datetime import date, datetime
from typing import Any

from ...models.meeting_status import assert_transition

logger = logging.getLogger(__name__)

_UNSET: Any = object()
_MEETING_LIST_COLUMNS = (
    "id, title, description, status, meeting_date, error_message, "
    "content_hash, created_at, updated_at"
)


def create_meeting(
    conn: sqlite3.Connection,
    *,
    title: str,
    description: str | None = None,
    meeting_date: str | date | datetime | None = None,
    user_id: str,
    # Deprecated: file fields are now stored in meeting_files table
    file_type: str | None = None,
    file_name: str | None = None,
    file_path: str | None = None,
    content_hash: str | None = None,
) -> int:
    if isinstance(meeting_date, (date, datetime)):
        meeting_date = meeting_date.isoformat()
    cursor = conn.execute(
        """INSERT INTO meetings
           (title, description, file_type, file_name, file_path,
            meeting_date, content_hash, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, description, file_type, file_name, file_path, meeting_date, content_hash, user_id),
    )
    rowid = cursor.lastrowid
    if rowid is None:
        raise RuntimeError("Failed to create meeting: lastrowid is None")
    return rowid


def update_meeting_status(
    conn: sqlite3.Connection,
    meeting_id: int,
    status: str,
    transcript: str | None = None,
    error_message: str | None = None,
    *,
    user_id: str | None = None,
) -> None:
    # Fetch current status to validate transition
    if user_id is not None:
        row = conn.execute(
            "SELECT status FROM meetings WHERE id=? AND user_id=?",
            (meeting_id, user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT status FROM meetings WHERE id=?",
            (meeting_id,),
        ).fetchone()

    if row is None:
        return

    normalized = assert_transition(str(row["status"]), status).value
    was_processing = row["status"] == "processing"
    is_entering_processing = normalized == "processing"

    # Dispatch to a helper that uses fully static SQL strings with
    # parameterised values -- no f-string SQL construction.
    if transcript is not None:
        _update_with_transcript(
            conn,
            meeting_id,
            user_id,
            normalized,
            transcript,
            is_entering_processing,
            was_processing,
        )
    elif error_message is not None:
        _update_with_error(
            conn,
            meeting_id,
            user_id,
            normalized,
            error_message,
            is_entering_processing,
            was_processing,
        )
    else:
        _update_status_only(
            conn,
            meeting_id,
            user_id,
            normalized,
            is_entering_processing,
            was_processing,
        )


def _update_with_transcript(
    conn: sqlite3.Connection,
    meeting_id: int,
    user_id: str | None,
    status: str,
    transcript: str,
    entering_processing: bool,
    was_processing: bool,
) -> None:
    """Execute UPDATE meeting SET status, transcript [+ processing_started_at]."""
    if entering_processing:
        sql = (
            "UPDATE meetings SET status=?, transcript=?,"
            " processing_started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params: tuple[Any, ...] = (status, transcript, meeting_id)
    elif was_processing:
        sql = (
            "UPDATE meetings SET status=?, transcript=?,"
            " processing_started_at=NULL, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params = (status, transcript, meeting_id)
    else:
        sql = "UPDATE meetings SET status=?, transcript=?, updated_at=CURRENT_TIMESTAMP WHERE id=?"
        params = (status, transcript, meeting_id)

    if user_id is not None:
        sql += " AND user_id=?"
        params = (*params, user_id)

    conn.execute(sql, params)


def _update_with_error(
    conn: sqlite3.Connection,
    meeting_id: int,
    user_id: str | None,
    status: str,
    error_message: str,
    entering_processing: bool,
    was_processing: bool,
) -> None:
    """Execute UPDATE meeting SET status, error_message [+ processing_started_at]."""
    if entering_processing:
        sql = (
            "UPDATE meetings SET status=?, error_message=?,"
            " processing_started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params: tuple[Any, ...] = (status, error_message, meeting_id)
    elif was_processing:
        sql = (
            "UPDATE meetings SET status=?, error_message=?,"
            " processing_started_at=NULL, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params = (status, error_message, meeting_id)
    else:
        sql = (
            "UPDATE meetings SET status=?, error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?"
        )
        params = (status, error_message, meeting_id)

    if user_id is not None:
        sql += " AND user_id=?"
        params = (*params, user_id)

    conn.execute(sql, params)


def _update_status_only(
    conn: sqlite3.Connection,
    meeting_id: int,
    user_id: str | None,
    status: str,
    entering_processing: bool,
    was_processing: bool,
) -> None:
    """Execute UPDATE meeting SET status [+ processing_started_at]."""
    if entering_processing:
        sql = (
            "UPDATE meetings SET status=?,"
            " processing_started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params: tuple[Any, ...] = (status, meeting_id)
    elif was_processing:
        sql = (
            "UPDATE meetings SET status=?,"
            " processing_started_at=NULL, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?"
        )
        params = (status, meeting_id)
    else:
        sql = "UPDATE meetings SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?"
        params = (status, meeting_id)

    if user_id is not None:
        sql += " AND user_id=?"
        params = (*params, user_id)

    conn.execute(sql, params)


def get_meeting(
    conn: sqlite3.Connection, meeting_id: int, *, user_id: str | None = None
) -> dict | None:
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM meetings WHERE id=? AND user_id=?", (meeting_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    return dict(row) if row else None


def update_meeting(
    conn: sqlite3.Connection,
    meeting_id: int,
    *,
    user_id: str | None = None,
    **kwargs: str | date | datetime | None,
) -> None:
    """Update meeting metadata fields (title, description, meeting_date)."""
    allowed_fields = {"title", "description", "meeting_date"}
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    meeting_date = updates.get("meeting_date")
    if isinstance(meeting_date, (date, datetime)):
        updates["meeting_date"] = meeting_date.isoformat()

    if not updates:
        return

    # Column names are already validated by the allowed_fields whitelist above.
    set_clause = ", ".join(k + "=?" for k in updates)
    values = list(updates.values())
    if user_id is not None:
        conn.execute(
            "UPDATE meetings SET "
            + set_clause
            + ", updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
            [*values, meeting_id, user_id],
        )
    else:
        conn.execute(
            "UPDATE meetings SET " + set_clause + ", updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*values, meeting_id],
        )


def list_meetings(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
) -> list[dict]:
    if status and user_id:
        rows = conn.execute(
            "SELECT " + _MEETING_LIST_COLUMNS + " FROM meetings "
            "WHERE status=? AND user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, user_id, limit, offset),
        ).fetchall()
    elif status:
        rows = conn.execute(
            "SELECT " + _MEETING_LIST_COLUMNS + " FROM meetings "
            "WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    elif user_id:
        rows = conn.execute(
            "SELECT " + _MEETING_LIST_COLUMNS + " FROM meetings "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT " + _MEETING_LIST_COLUMNS + " FROM meetings "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_meetings(
    conn: sqlite3.Connection, status: str | None = None, user_id: str | None = None
) -> int:
    if status and user_id:
        row = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE status=? AND user_id=?",
            (status, user_id),
        ).fetchone()
    elif status:
        row = conn.execute("SELECT COUNT(*) FROM meetings WHERE status=?", (status,)).fetchone()
    elif user_id:
        row = conn.execute("SELECT COUNT(*) FROM meetings WHERE user_id=?", (user_id,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()
    return row[0]


def delete_meeting(
    conn: sqlite3.Connection, meeting_id: int, *, user_id: str | None = None
) -> None:
    if user_id is not None:
        conn.execute("DELETE FROM meetings WHERE id=? AND user_id=?", (meeting_id, user_id))
    else:
        conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
