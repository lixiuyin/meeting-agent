"""Meeting-domain metadata used to interpret retrieval evidence."""

from __future__ import annotations

import sqlite3

MEETING_FILE_SEMANTICS_SCHEMA_SQL = """
ALTER TABLE meeting_files ADD COLUMN material_role TEXT;
ALTER TABLE meeting_files ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE meeting_files ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE meeting_files ADD COLUMN content_recorded_at TIMESTAMP;
ALTER TABLE meeting_files ADD COLUMN semantic_updated_at TIMESTAMP;
ALTER TABLE meeting_files ADD COLUMN approval_reason TEXT;
CREATE TABLE IF NOT EXISTS meeting_file_semantic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES meeting_files(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    material_role TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    approval_reason TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_meeting_file_semantic_events_file
    ON meeting_file_semantic_events(file_id, source_revision DESC);
UPDATE meeting_files
   SET content_recorded_at=COALESCE(content_recorded_at, created_at, CURRENT_TIMESTAMP);
"""


def ensure_meeting_file_semantics_schema(conn: sqlite3.Connection) -> None:
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(meeting_files)")}
    if "material_role" not in existing:
        conn.execute("ALTER TABLE meeting_files ADD COLUMN material_role TEXT")
    if "approval_status" not in existing:
        conn.execute(
            "ALTER TABLE meeting_files ADD COLUMN approval_status "
            "TEXT NOT NULL DEFAULT 'unreviewed'"
        )
    additions = {
        "source_revision": "INTEGER NOT NULL DEFAULT 1",
        "content_recorded_at": "TIMESTAMP",
        "semantic_updated_at": "TIMESTAMP",
        "approval_reason": "TEXT",
    }
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE meeting_files ADD COLUMN {name} {definition}")
    conn.execute(
        "UPDATE meeting_files SET content_recorded_at="
        "COALESCE(content_recorded_at, created_at, CURRENT_TIMESTAMP)"
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meeting_file_semantic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES meeting_files(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            material_role TEXT NOT NULL,
            approval_status TEXT NOT NULL,
            approval_reason TEXT,
            changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_file_semantic_events_file
            ON meeting_file_semantic_events(file_id, source_revision DESC);
        """
    )
