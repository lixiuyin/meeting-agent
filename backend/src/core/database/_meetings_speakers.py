"""Segment JSON and speaker mapping persistence."""

import sqlite3


def save_segments_json(conn: sqlite3.Connection, file_id: int, segments_json: str | None) -> None:
    """Store or clear the serialized segment list for a file."""
    conn.execute(
        """UPDATE meeting_files
           SET segments_json=?,
               structured_json=CASE
                   WHEN ? IS NULL AND structured_kind='segments' THEN NULL
                   ELSE COALESCE(?, structured_json)
               END,
               structured_kind=CASE
                   WHEN ? IS NULL AND structured_kind='segments' THEN NULL
                   WHEN ? IS NOT NULL THEN 'segments'
                   ELSE structured_kind
               END
           WHERE id=?""",
        (segments_json, segments_json, segments_json, segments_json, segments_json, file_id),
    )


def get_segments_json(conn: sqlite3.Connection, file_id: int) -> str | None:
    """Retrieve the serialized segment list for a file."""
    row = conn.execute(
        """SELECT
               COALESCE(
                   segments_json,
                   CASE WHEN structured_kind='segments' THEN structured_json END
               ) AS segments_json
           FROM meeting_files
           WHERE id=?""",
        (file_id,),
    ).fetchone()
    return row["segments_json"] if row else None


def upsert_speaker_mapping(
    conn: sqlite3.Connection,
    file_id: int,
    meeting_id: int,
    speaker_code: str,
    speaker_name: str,
) -> None:
    """Insert or update a speaker name mapping for a file."""
    conn.execute(
        """INSERT INTO speaker_mappings (file_id, meeting_id, speaker_code, speaker_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(file_id, speaker_code)
           DO UPDATE SET speaker_name=excluded.speaker_name, updated_at=CURRENT_TIMESTAMP""",
        (file_id, meeting_id, speaker_code, speaker_name),
    )


def bulk_upsert_speaker_mappings(
    conn: sqlite3.Connection,
    file_id: int,
    meeting_id: int,
    mappings: list[tuple[str, str]],
) -> None:
    """Batch insert/update speaker name mappings for a file."""
    conn.executemany(
        """INSERT INTO speaker_mappings (file_id, meeting_id, speaker_code, speaker_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(file_id, speaker_code)
           DO UPDATE SET speaker_name=excluded.speaker_name, updated_at=CURRENT_TIMESTAMP""",
        [
            (file_id, meeting_id, speaker_code, speaker_name)
            for speaker_code, speaker_name in mappings
        ],
    )


def list_speaker_mappings(conn: sqlite3.Connection, file_id: int) -> list[dict]:
    """List all speaker mappings for a file."""
    rows = conn.execute(
        "SELECT speaker_code, speaker_name FROM speaker_mappings WHERE file_id=?",
        (file_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_file_ids_for_speakers(
    conn: sqlite3.Connection,
    speaker_names: list[str],
    meeting_ids: list[int] | None = None,
) -> set[int]:
    """Return file_ids whose speaker_mappings contain any of the given names.

    Matching is case-insensitive on ``speaker_name`` only — raw speaker
    codes ('A', 'B') are excluded to prevent false positives.
    Optionally scoped to specific meeting_ids.
    """
    if not speaker_names:
        return set()
    placeholders = ",".join("?" for _ in speaker_names)
    lower_names = [n.lower() for n in speaker_names]
    sql = f"""
        SELECT DISTINCT file_id FROM speaker_mappings
        WHERE LOWER(speaker_name) IN ({placeholders})
    """
    params: list[object] = list(lower_names)
    if meeting_ids:
        m_placeholders = ",".join("?" for _ in meeting_ids)
        sql += f" AND meeting_id IN ({m_placeholders})"
        params.extend(meeting_ids)
    rows = conn.execute(sql, params).fetchall()
    return {row["file_id"] for row in rows}


def delete_speaker_mappings(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete all speaker mappings for a file."""
    conn.execute("DELETE FROM speaker_mappings WHERE file_id=?", (file_id,))
