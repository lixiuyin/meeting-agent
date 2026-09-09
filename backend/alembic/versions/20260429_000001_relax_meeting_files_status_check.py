"""Relax meeting_files.status CHECK to include 'summarizing'.

Revision ID: 20260429_000001
Revises: 20260425_000002
Create Date: 2026-04-29 12:20:00

The processing pipeline sets status='summarizing' when auto-summarizing files,
but the original CHECK constraint only allowed ('processing', 'ready', 'error').
SQLite lacks ALTER COLUMN, so we recreate the table with the relaxed constraint.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260429_000001"
down_revision = "20260425_000002"
branch_labels = None
depends_on = None

_CREATE_TABLE_SQL = """\
CREATE TABLE meeting_files_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    file_type TEXT NOT NULL CHECK(file_type IN (
        'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
    )),
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT,
    transcript TEXT,
    status TEXT DEFAULT 'processing'
        CHECK(status IN ('processing', 'summarizing', 'ready', 'error')),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    segments_json TEXT,
    structured_json TEXT,
    structured_kind TEXT,
    summary TEXT,
    key_points_json TEXT,
    duration_seconds REAL,
    page_count INTEGER,
    word_count INTEGER,
    language TEXT,
    metrics_json TEXT,
    raganything_doc_id TEXT,
    raganything_indexed_at TIMESTAMP,
    processing_started_at TIMESTAMP,
    summary_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(summary_status IN ('pending', 'generating', 'ready', 'failed'))
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    schema_row = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='meeting_files'")
    ).fetchone()
    if schema_row and "'summarizing'" in (schema_row[0] or ""):
        return

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text("DROP TABLE IF EXISTS meeting_files_new"))

    op.execute(sa.text(_CREATE_TABLE_SQL))
    op.execute(sa.text("INSERT INTO meeting_files_new SELECT * FROM meeting_files"))
    op.execute(sa.text("DROP TABLE meeting_files"))
    op.execute(sa.text("ALTER TABLE meeting_files_new RENAME TO meeting_files"))

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_meeting_files_meeting_id ON meeting_files(meeting_id)"
        )
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_meeting_files_status ON meeting_files(status)")
    )

    op.execute(sa.text("PRAGMA foreign_keys=ON"))


_ORIGINAL_CREATE_TABLE_SQL = """\
CREATE TABLE meeting_files_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    file_type TEXT NOT NULL CHECK(file_type IN (
        'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
    )),
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT,
    transcript TEXT,
    status TEXT DEFAULT 'processing'
        CHECK(status IN ('processing', 'ready', 'error')),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    segments_json TEXT,
    structured_json TEXT,
    structured_kind TEXT,
    summary TEXT,
    key_points_json TEXT,
    duration_seconds REAL,
    page_count INTEGER,
    word_count INTEGER,
    language TEXT,
    metrics_json TEXT,
    raganything_doc_id TEXT,
    raganything_indexed_at TIMESTAMP,
    processing_started_at TIMESTAMP,
    summary_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(summary_status IN ('pending', 'generating', 'ready', 'failed'))
)
"""


def downgrade() -> None:
    bind = op.get_bind()
    schema_row = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='meeting_files'")
    ).fetchone()
    # Only downgrade if the table still has the relaxed constraint
    if not schema_row or "'summarizing'" not in (schema_row[0] or ""):
        return

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text("DROP TABLE IF EXISTS meeting_files_new"))

    op.execute(sa.text(_ORIGINAL_CREATE_TABLE_SQL))
    # Map 'summarizing' back to 'processing' during downgrade
    op.execute(
        sa.text(
            "INSERT INTO meeting_files_new "
            "SELECT id, meeting_id, file_type, file_name, file_path, content_hash, "
            "transcript, CASE WHEN status='summarizing' THEN 'processing' ELSE status END, "
            "error_message, created_at, updated_at, segments_json, structured_json, "
            "structured_kind, summary, key_points_json, duration_seconds, page_count, "
            "word_count, language, metrics_json, raganything_doc_id, raganything_indexed_at, "
            "processing_started_at, summary_status "
            "FROM meeting_files"
        )
    )
    op.execute(sa.text("DROP TABLE meeting_files"))
    op.execute(sa.text("ALTER TABLE meeting_files_new RENAME TO meeting_files"))

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_meeting_files_meeting_id ON meeting_files(meeting_id)"
        )
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_meeting_files_status ON meeting_files(status)")
    )

    op.execute(sa.text("PRAGMA foreign_keys=ON"))
