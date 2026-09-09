"""Index consistency state CRUD for Chroma and RAGAnything."""

from __future__ import annotations

import sqlite3

INDEX_HEALTH_SCHEMA_SQL = """
ALTER TABLE index_state ADD COLUMN bm25_indexed_at TIMESTAMP;
ALTER TABLE index_state ADD COLUMN native_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK(native_status IN ('unknown','building','ready','failed'));
ALTER TABLE index_state ADD COLUMN native_last_error TEXT;
ALTER TABLE index_state ADD COLUMN native_generation TEXT;
ALTER TABLE index_state ADD COLUMN native_config_fingerprint TEXT;
ALTER TABLE index_state ADD COLUMN chroma_chunk_count INTEGER;
ALTER TABLE index_state ADD COLUMN bm25_chunk_count INTEGER;
ALTER TABLE index_state ADD COLUMN native_manifest_checksum TEXT;
ALTER TABLE index_state ADD COLUMN repair_pending INTEGER NOT NULL DEFAULT 0
    CHECK(repair_pending IN (0, 1));
"""


def ensure_index_health_schema(conn: sqlite3.Connection) -> None:
    """Add post-v52 index-health columns for bootstrap/legacy databases.

    Production deployments apply the equivalent Alembic revision.  The
    built-in initializer is still used by tests and development commands, so
    it must be able to open an existing v52 database safely as well.
    """
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(index_state)").fetchall()}
    additions = {
        "bm25_indexed_at": "TIMESTAMP",
        "native_status": (
            "TEXT NOT NULL DEFAULT 'unknown' "
            "CHECK(native_status IN ('unknown','building','ready','failed'))"
        ),
        "native_last_error": "TEXT",
        "native_generation": "TEXT",
        "native_config_fingerprint": "TEXT",
        "chroma_chunk_count": "INTEGER",
        "bm25_chunk_count": "INTEGER",
        "native_manifest_checksum": "TEXT",
        "repair_pending": "INTEGER NOT NULL DEFAULT 0 CHECK(repair_pending IN (0, 1))",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE index_state ADD COLUMN {name} {definition}")


def mark_native_index_building(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
) -> None:
    conn.execute(
        """
        INSERT INTO index_state (file_id, meeting_id, native_status, updated_at)
        VALUES (?, ?, 'building', CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            native_status='building',
            native_last_error=NULL,
            repair_pending=0,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id),
    )


def mark_native_index_ready(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    indexed_at: str,
    generation: str | None = None,
    config_fingerprint: str | None = None,
    chroma_chunk_count: int | None = None,
    bm25_chunk_count: int | None = None,
    manifest_checksum: str | None = None,
) -> None:
    """Commit Chroma and BM25 readiness together after both writes succeed."""
    conn.execute(
        """
        INSERT INTO index_state (
            file_id, meeting_id, chroma_indexed_at, bm25_indexed_at,
            native_status, native_last_error, native_generation,
            native_config_fingerprint, chroma_chunk_count, bm25_chunk_count,
            native_manifest_checksum, repair_pending, updated_at
        )
        VALUES (?, ?, ?, ?, 'ready', NULL, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            chroma_indexed_at=excluded.chroma_indexed_at,
            bm25_indexed_at=excluded.bm25_indexed_at,
            native_status='ready',
            native_last_error=NULL,
            native_generation=COALESCE(excluded.native_generation, index_state.native_generation),
            native_config_fingerprint=COALESCE(
                excluded.native_config_fingerprint, index_state.native_config_fingerprint
            ),
            chroma_chunk_count=COALESCE(
                excluded.chroma_chunk_count, index_state.chroma_chunk_count
            ),
            bm25_chunk_count=COALESCE(
                excluded.bm25_chunk_count, index_state.bm25_chunk_count
            ),
            native_manifest_checksum=COALESCE(
                excluded.native_manifest_checksum, index_state.native_manifest_checksum
            ),
            repair_pending=0,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            file_id,
            meeting_id,
            indexed_at,
            indexed_at,
            generation,
            config_fingerprint,
            chroma_chunk_count,
            bm25_chunk_count,
            manifest_checksum,
        ),
    )


def mark_native_index_failed(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT INTO index_state (
            file_id, meeting_id, native_status, native_last_error, repair_pending, updated_at
        )
        VALUES (?, ?, 'failed', ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            native_status='failed',
            native_last_error=excluded.native_last_error,
            repair_pending=1,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id, error[:500]),
    )


def mark_chroma_indexed(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    indexed_at: str,
) -> None:
    """Backward-compatible alias for callers that completed both native stores."""
    mark_native_index_ready(
        conn,
        file_id=file_id,
        meeting_id=meeting_id,
        indexed_at=indexed_at,
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


def mark_native_index_repair_pending(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    meeting_id: int,
    error: str,
) -> None:
    """Mark a file unsafe for the active config until durable reprocessing runs."""
    conn.execute(
        """
        INSERT INTO index_state (
            file_id, meeting_id, native_status, native_last_error,
            repair_pending, updated_at
        ) VALUES (?, ?, 'failed', ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(file_id) DO UPDATE SET
            meeting_id=excluded.meeting_id,
            native_status='failed',
            native_last_error=excluded.native_last_error,
            repair_pending=1,
            updated_at=CURRENT_TIMESTAMP
        """,
        (file_id, meeting_id, error[:500]),
    )
