"""Meeting file CRUD operations."""

import logging
import sqlite3
from typing import Any

from ...models.meeting_status import assert_transition

logger = logging.getLogger(__name__)

_UNSET: Any = object()
_FILE_INDEX_STATE_COLUMNS = (
    "(SELECT i.native_status FROM index_state i WHERE i.file_id=mf.id) AS native_index_status, "
    "(SELECT i.native_last_error FROM index_state i WHERE i.file_id=mf.id) "
    "AS native_index_error"
)
_MEETING_FILE_LIST_COLUMNS = (
    "mf.id, mf.meeting_id, mf.file_type, mf.file_name, mf.file_path, mf.content_hash, "
    "mf.status, mf.transcript, mf.error_message, "
    "mf.created_at, mf.updated_at, mf.summary, mf.summary_status, "
    "mf.duration_seconds, mf.page_count, mf.word_count, mf.language, "
    "mf.raganything_doc_id, mf.raganything_indexed_at, "
    "mf.material_role, mf.business_domain, mf.approval_status, mf.approval_reason, "
    "mf.source_revision, mf.content_recorded_at, mf.semantic_updated_at, "
    "(SELECT json_extract(b.metadata, '$.index_generation') FROM bm25_index b "
    "WHERE b.meeting_id=mf.meeting_id "
    "AND CAST(json_extract(b.metadata, '$.file_id') AS INTEGER)=mf.id "
    "AND json_extract(b.metadata, '$.index_generation') IS NOT NULL "
    "ORDER BY b.rowid DESC LIMIT 1) AS active_index_generation, " + _FILE_INDEX_STATE_COLUMNS
)


def create_meeting_file(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    file_type: str,
    file_name: str,
    file_path: str,
    content_hash: str | None = None,
    user_id: str = "default",
) -> int:
    """Create a new meeting file record. Returns the file ID."""
    cursor = conn.execute(
        """INSERT INTO meeting_files
           (
               meeting_id, file_type, file_name, file_path, content_hash, status,
               processing_started_at, user_id, content_recorded_at
           )
           VALUES (?, ?, ?, ?, ?, 'processing', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)""",
        (meeting_id, file_type, file_name, file_path, content_hash, user_id),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("Failed to insert meeting file record")
    return cursor.lastrowid


def create_meeting_file_if_absent(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    file_type: str,
    file_name: str,
    file_path: str,
    content_hash: str | None = None,
    user_id: str = "default",
) -> int | None:
    """Insert meeting file record iff active duplicate is not present.

    If a duplicate exists only in terminal status (``failed``/``error``),
    reuse that row as a retry by resetting it to ``processing``.
    """
    if content_hash:
        reusable = conn.execute(
            """SELECT id FROM meeting_files
               WHERE meeting_id=? AND content_hash=? AND status IN ('failed', 'error')
               ORDER BY id DESC LIMIT 1""",
            (meeting_id, content_hash),
        ).fetchone()
        if reusable:
            conn.execute(
                """UPDATE meeting_files
                   SET file_type=?,
                       file_name=?,
                       file_path=?,
                       status='processing',
                       processing_started_at=CURRENT_TIMESTAMP,
                       transcript=NULL,
                       error_message=NULL,
                       updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (file_type, file_name, file_path, reusable["id"]),
            )
            return int(reusable["id"])

    cursor = conn.execute(
        """INSERT OR IGNORE INTO meeting_files
           (
               meeting_id, file_type, file_name, file_path, content_hash, status,
               processing_started_at, user_id, content_recorded_at
           )
           VALUES (?, ?, ?, ?, ?, 'processing', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)""",
        (meeting_id, file_type, file_name, file_path, content_hash, user_id),
    )
    if cursor.lastrowid is None or cursor.rowcount == 0:
        return None
    return cursor.lastrowid


def get_meeting_file(
    conn: sqlite3.Connection, file_id: int, *, user_id: str | None = None
) -> dict | None:
    """Get a single meeting file by ID.

    When *user_id* is provided the query joins with ``meetings`` to enforce
    ownership, returning ``None`` for files that belong to another user.
    """
    if user_id is not None:
        row = conn.execute(
            "SELECT mf.*, " + _FILE_INDEX_STATE_COLUMNS + " FROM meeting_files mf "
            "JOIN meetings m ON mf.meeting_id = m.id "
            "WHERE mf.id=? AND m.user_id=?",
            (file_id, user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT mf.*, " + _FILE_INDEX_STATE_COLUMNS + " FROM meeting_files mf WHERE mf.id=?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


def update_meeting_file_semantics(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    material_role: object = _UNSET,
    business_domain: object = _UNSET,
    approval_status: object = _UNSET,
    approval_reason: object = _UNSET,
    user_id: str = "default",
) -> int | None:
    """Update user-reviewed meeting semantics without changing file content."""
    updates: list[str] = []
    params: list[Any] = []
    if business_domain is not _UNSET:
        updates.append("business_domain=?")
        params.append(business_domain)
    if material_role is not _UNSET:
        updates.append("material_role=?")
        params.append(material_role)
    if approval_status is not _UNSET:
        updates.append("approval_status=?")
        params.append(approval_status)
    if approval_reason is not _UNSET:
        updates.append("approval_reason=?")
        params.append(approval_reason)
    if not updates:
        return None
    updates.extend(
        (
            "updated_at=CURRENT_TIMESTAMP",
            "semantic_updated_at=CURRENT_TIMESTAMP",
            "source_revision=source_revision+1",
        )
    )
    params.append(file_id)
    cursor = conn.execute(
        f"UPDATE meeting_files SET {', '.join(updates)} WHERE id=?",
        params,
    )
    if cursor.rowcount <= 0:
        return None
    row = conn.execute(
        "SELECT source_revision, COALESCE(material_role, 'attachment') AS material_role, "
        "COALESCE(approval_status, 'unreviewed') AS approval_status, "
        "approval_reason, business_domain "
        "FROM meeting_files WHERE id=?",
        (file_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "INSERT INTO meeting_file_semantic_events "
        "(file_id, user_id, source_revision, material_role, approval_status, "
        "approval_reason, business_domain) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            file_id,
            user_id,
            int(row["source_revision"]),
            str(row["material_role"]),
            str(row["approval_status"]),
            row["approval_reason"],
            row["business_domain"],
        ),
    )
    return int(row["source_revision"])


def list_meeting_file_semantic_events(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    user_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return the immutable review trail for one meeting file."""
    params: list[Any] = [file_id]
    ownership = ""
    if user_id is not None:
        ownership = " AND e.user_id=?"
        params.append(user_id)
    params.append(max(1, min(limit, 500)))
    rows = conn.execute(
        "SELECT e.source_revision, e.material_role, e.approval_status, "
        "e.approval_reason, e.changed_at, e.business_domain FROM meeting_file_semantic_events e "
        "WHERE e.file_id=?" + ownership + " ORDER BY e.source_revision DESC, e.id DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def list_meeting_files(
    conn: sqlite3.Connection, meeting_id: int, *, user_id: str | None = None
) -> list[dict]:
    """List all files for a meeting."""
    if user_id is not None:
        rows = conn.execute(
            f"SELECT {_MEETING_FILE_LIST_COLUMNS} FROM meeting_files mf "
            "JOIN meetings m ON mf.meeting_id = m.id "
            "WHERE mf.meeting_id=? AND m.user_id=? ORDER BY mf.created_at",
            (meeting_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_MEETING_FILE_LIST_COLUMNS} FROM meeting_files mf "
            "WHERE mf.meeting_id=? ORDER BY mf.created_at",
            (meeting_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_distinct_file_types_bulk(
    conn: sqlite3.Connection, meeting_ids: list[int]
) -> dict[int, list[str]]:
    """Return a map of meeting_id → ordered list of distinct file_types.

    Used by list endpoints to surface mixed-modality meetings without an
    N+1 query pattern.  Order is deterministic (alphabetical) so frontend
    rendering is stable across requests.
    """
    if not meeting_ids:
        return {}
    placeholders = ",".join("?" for _ in meeting_ids)
    rows = conn.execute(
        f"SELECT meeting_id, file_type FROM meeting_files "
        f"WHERE meeting_id IN ({placeholders}) AND file_type IS NOT NULL",
        tuple(meeting_ids),
    ).fetchall()
    result: dict[int, set[str]] = {mid: set() for mid in meeting_ids}
    for row in rows:
        result[row["meeting_id"]].add(row["file_type"])
    return {mid: sorted(types) for mid, types in result.items()}


def update_meeting_file_status(
    conn: sqlite3.Connection,
    file_id: int,
    status: str,
    transcript: str | None = _UNSET,
    error_message: str | None = _UNSET,
    content_hash: str | None = _UNSET,
) -> None:
    """Update file processing status and transcript."""
    current = conn.execute(
        "SELECT status, transcript, content_hash FROM meeting_files WHERE id=?", (file_id,)
    ).fetchone()
    if current is None:
        return
    normalized = assert_transition(str(current["status"]), status).value

    # Build SET clause dynamically based on provided fields
    set_parts = ["status=?", "updated_at=CURRENT_TIMESTAMP"]
    params: list[Any] = [normalized]

    if normalized == "processing":
        set_parts.append("processing_started_at=CURRENT_TIMESTAMP")
    elif current["status"] == "processing":
        set_parts.append("processing_started_at=NULL")

    if transcript is not _UNSET:
        set_parts.append("transcript=?")
        params.append(transcript)
    if error_message is not _UNSET:
        set_parts.append("error_message=?")
        params.append(error_message)
    if content_hash is not _UNSET:
        set_parts.append("content_hash=?")
        params.append(content_hash)

    source_changed = (transcript is not _UNSET and transcript != current["transcript"]) or (
        content_hash is not _UNSET and content_hash != current["content_hash"]
    )
    if source_changed:
        set_parts.extend(
            (
                "source_revision=source_revision+1",
                "content_recorded_at=CURRENT_TIMESTAMP",
            )
        )

    params.append(file_id)
    # Column names in set_parts are hardcoded string literals from conditionals above.
    conn.execute(
        f"UPDATE meeting_files SET {', '.join(set_parts)} WHERE id=?",
        params,
    )


def update_meeting_file_artefact(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    structured_json: str | None = None,
    structured_kind: str | None = None,
    metrics_json: str | None = None,
    duration_seconds: float | None = None,
    page_count: int | None = None,
    word_count: int | None = None,
    language: str | None = None,
) -> None:
    """Persist typed processing artefact fields for a meeting file."""
    set_parts = ["updated_at=CURRENT_TIMESTAMP"]
    params: list[Any] = []

    if structured_json is not None:
        set_parts.append("structured_json=?")
        params.append(structured_json)
    if structured_kind is not None:
        set_parts.append("structured_kind=?")
        params.append(structured_kind)
    if metrics_json is not None:
        set_parts.append("metrics_json=?")
        params.append(metrics_json)
    if duration_seconds is not None:
        set_parts.append("duration_seconds=?")
        params.append(duration_seconds)
    if page_count is not None:
        set_parts.append("page_count=?")
        params.append(page_count)
    if word_count is not None:
        set_parts.append("word_count=?")
        params.append(word_count)
    if language is not None:
        set_parts.append("language=?")
        params.append(language)

    if len(set_parts) == 1:
        return

    params.append(file_id)
    conn.execute(f"UPDATE meeting_files SET {', '.join(set_parts)} WHERE id=?", params)


def update_meeting_file_summary(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    summary: str,
    key_points_json: str | None = None,
) -> None:
    """Persist generated per-file summary and extracted key points."""
    conn.execute(
        """UPDATE meeting_files
           SET summary=?, key_points_json=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (summary, key_points_json, file_id),
    )
    # Sync to file-level BM25 index
    row = conn.execute("SELECT meeting_id FROM meeting_files WHERE id=?", (file_id,)).fetchone()
    if row:
        try:
            from .bm25 import upsert_file_summary_bm25

            upsert_file_summary_bm25(conn, file_id, row["meeting_id"], summary)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to upsert BM25 for file %d; continuing", file_id, exc_info=True
            )


def list_ready_file_ids_for_meetings(
    conn: sqlite3.Connection,
    meeting_ids: list[int],
    *,
    user_id: str | None = None,
) -> list[int]:
    """Return file IDs in 'ready' status for the given meeting IDs."""
    if not meeting_ids:
        return []
    placeholders = ",".join("?" for _ in meeting_ids)
    sql = (
        "SELECT mf.id FROM meeting_files mf "
        "JOIN meetings m ON m.id=mf.meeting_id "
        f"WHERE mf.meeting_id IN ({placeholders}) AND mf.status='ready'"
    )
    params: list[Any] = list(meeting_ids)
    if user_id is not None:
        sql += " AND m.user_id=?"
        params.append(user_id)
    sql += " ORDER BY mf.meeting_id, mf.created_at"
    rows = conn.execute(sql, params).fetchall()
    return [row["id"] for row in rows]


def list_recent_ready_file_ids(
    conn: sqlite3.Connection,
    limit: int = 50,
    *,
    user_id: str | None = None,
) -> list[int]:
    """Return file IDs across all meetings, ordered by most recent first."""
    sql = (
        "SELECT mf.id FROM meeting_files mf "
        "JOIN meetings m ON m.id=mf.meeting_id "
        "WHERE mf.status='ready'"
    )
    params: list[Any] = []
    if user_id is not None:
        sql += " AND m.user_id=?"
        params.append(user_id)
    sql += " ORDER BY mf.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [row["id"] for row in rows]


def get_meeting_files_summaries(
    conn: sqlite3.Connection,
    file_ids: list[int],
    *,
    user_id: str | None = None,
) -> dict[int, str]:
    """Return summaries for the requested files, optionally owner-scoped."""
    if not file_ids:
        return {}
    placeholders = ",".join("?" for _ in file_ids)
    sql = (
        "SELECT mf.id, mf.summary FROM meeting_files mf "
        "JOIN meetings m ON m.id=mf.meeting_id "
        f"WHERE mf.id IN ({placeholders}) "
        "AND mf.summary IS NOT NULL AND mf.summary != ''"
    )
    params: list[Any] = list(file_ids)
    if user_id is not None:
        sql += " AND m.user_id=?"
        params.append(user_id)
    rows = conn.execute(
        sql,
        params,
    ).fetchall()
    return {row["id"]: row["summary"] for row in rows}


def get_file_metadata_bulk(
    conn: sqlite3.Connection,
    file_ids: list[int],
) -> dict[int, dict]:
    """Return routing and size metadata keyed by file ID."""
    if not file_ids:
        return {}
    placeholders = ",".join("?" for _ in file_ids)
    rows = conn.execute(
        f"SELECT id, meeting_id, file_type, page_count, duration_seconds "
        f"FROM meeting_files WHERE id IN ({placeholders})",
        file_ids,
    ).fetchall()
    return {
        row["id"]: {
            "meeting_id": row["meeting_id"],
            "file_type": row["file_type"] or "",
            "page_count": row["page_count"] or 0,
            "duration_seconds": row["duration_seconds"] or 0.0,
        }
        for row in rows
    }


def update_meeting_file_raganything(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    doc_id: str | None,
    indexed_at: str | None,
) -> None:
    """Persist RAGAnything indexing state for a meeting file."""
    conn.execute(
        """UPDATE meeting_files
           SET raganything_doc_id=?,
               raganything_indexed_at=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (doc_id, indexed_at, file_id),
    )


def get_meeting_file_by_raganything_doc_id(
    conn: sqlite3.Connection,
    doc_id: str,
) -> dict | None:
    """Return the meeting file row mapped to a persisted RAGAnything doc ID."""
    row = conn.execute(
        "SELECT * FROM meeting_files WHERE raganything_doc_id=? LIMIT 1",
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_meeting_file(
    conn: sqlite3.Connection, file_id: int, *, user_id: str | None = None
) -> dict | None:
    """Delete a meeting file and return its details for cleanup."""
    file_record = get_meeting_file(conn, file_id, user_id=user_id)
    if file_record:
        conn.execute("DELETE FROM meeting_files WHERE id=?", (file_id,))
    return file_record


def get_meeting_file_by_hash(
    conn: sqlite3.Connection, content_hash: str, meeting_id: int | None = None
) -> dict | None:
    """Check if a file with the same content hash already exists."""
    if meeting_id is None:
        row = conn.execute(
            "SELECT * FROM meeting_files WHERE content_hash=? AND status='ready'",
            (content_hash,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM meeting_files
               WHERE meeting_id=? AND content_hash=?
                 AND status IN ('ready', 'processing')""",
            (meeting_id, content_hash),
        ).fetchone()
    return dict(row) if row else None


def get_meeting_transcripts(
    conn: sqlite3.Connection, meeting_id: int, *, user_id: str | None = None
) -> str:
    """Get combined transcripts from all ready files for a meeting."""
    if user_id is not None:
        rows = conn.execute(
            """SELECT mf.transcript FROM meeting_files mf
               JOIN meetings m ON mf.meeting_id = m.id
               WHERE mf.meeting_id=? AND m.user_id=?
                 AND mf.status='ready' AND mf.transcript IS NOT NULL
               ORDER BY mf.created_at""",
            (meeting_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT transcript FROM meeting_files
               WHERE meeting_id=? AND status='ready' AND transcript IS NOT NULL
               ORDER BY created_at""",
            (meeting_id,),
        ).fetchall()
    transcripts = [r["transcript"] for r in rows if r["transcript"]]
    return "\n\n---\n\n".join(transcripts) if transcripts else ""


def list_ready_meeting_files(conn: sqlite3.Connection, meeting_id: int) -> list[dict]:
    """List all ready files for a meeting."""
    rows = conn.execute(
        """SELECT * FROM meeting_files
           WHERE meeting_id=? AND status='ready'
           ORDER BY created_at""",
        (meeting_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_meeting_files(conn: sqlite3.Connection, meeting_id: int) -> int:
    """Count total files for a meeting."""
    row = conn.execute(
        "SELECT COUNT(*) FROM meeting_files WHERE meeting_id=?",
        (meeting_id,),
    ).fetchone()
    return row[0]


def count_meeting_files_by_status(conn: sqlite3.Connection, meeting_id: int, status: str) -> int:
    """Count files with a specific status for a meeting."""
    row = conn.execute(
        "SELECT COUNT(*) FROM meeting_files WHERE meeting_id=? AND status=?",
        (meeting_id, status),
    ).fetchone()
    return row[0]


def get_meeting_file_status_counts(conn: sqlite3.Connection, meeting_id: int) -> dict[str, int]:
    """Get counts of files grouped by status for a meeting."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM meeting_files WHERE meeting_id=? GROUP BY status",
        (meeting_id,),
    ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}
