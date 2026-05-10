"""Index consistency state CRUD for Chroma and RAGAnything."""

from __future__ import annotations

import sqlite3


def mark_chroma_indexed(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    indexed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO index_state (
            file_id,
            meeting_id,
            chroma_indexed_at,
            updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            chroma_indexed_at=excluded.chroma_indexed_at,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id, indexed_at),
    )


def mark_raganything_indexed(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    doc_id: str,
    indexed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO index_state (
            file_id,
            meeting_id,
            raganything_doc_id,
            raganything_indexed_at,
            last_error,
            updated_at
        )
        VALUES (?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            raganything_doc_id=excluded.raganything_doc_id,
            raganything_indexed_at=excluded.raganything_indexed_at,
            last_error=NULL,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id, doc_id, indexed_at),
    )


def mark_raganything_failed(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT INTO index_state (
            file_id,
            meeting_id,
            last_error,
            updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            last_error=excluded.last_error,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id, error[:500]),
    )


def clear_index_state(conn: sqlite3.Connection, *, file_id: int) -> None:
    conn.execute("DELETE FROM index_state WHERE file_id=?", (file_id,))


def reconcile_index_state(*, limit: int = 500) -> dict[str, int]:
    """Backfill and normalize index_state from meeting_files metadata."""
    from . import get_write_connection

    with get_write_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                mf.id AS file_id,
                mf.meeting_id,
                mf.updated_at,
                mf.raganything_doc_id,
                mf.raganything_indexed_at
            FROM meeting_files mf
            WHERE mf.status='ready'
            ORDER BY mf.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        reconciled = 0
        for row in rows:
            file_id = int(row["file_id"])
            meeting_id = int(row["meeting_id"])
            chroma_ts = str(row["updated_at"] or "")
            conn.execute(
                """
                INSERT INTO index_state (
                    file_id,
                    meeting_id,
                    chroma_indexed_at,
                    raganything_doc_id,
                    raganything_indexed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_id) DO UPDATE SET
                    meeting_id=excluded.meeting_id,
                    chroma_indexed_at=COALESCE(
                        index_state.chroma_indexed_at,
                        excluded.chroma_indexed_at
                    ),
                    raganything_doc_id=COALESCE(
                        excluded.raganything_doc_id,
                        index_state.raganything_doc_id
                    ),
                    raganything_indexed_at=COALESCE(
                        excluded.raganything_indexed_at,
                        index_state.raganything_indexed_at
                    ),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    file_id,
                    meeting_id,
                    chroma_ts if chroma_ts else None,
                    row["raganything_doc_id"],
                    row["raganything_indexed_at"],
                ),
            )
            reconciled += 1
        return {"reconciled": reconciled}
