"""Relax meetings.status CHECK to include 'summarizing'.

Revision ID: 20260429_000002
Revises: 20260429_000001
Create Date: 2026-04-29 14:00:00

The three-stage meeting lifecycle (processing -> summarizing -> ready) requires
'meetings.status' to accept 'summarizing'.  Migration 20260429_000001 only relaxed
meeting_files.status; this one fixes the meetings table.

SQLite lacks ALTER COLUMN, so we recreate the table with the relaxed constraint.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260429_000002"
down_revision = "20260429_000001"
branch_labels = None
depends_on = None

_CREATE_TABLE_SQL = """\
CREATE TABLE meetings_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    file_type TEXT CHECK(file_type IN (
        'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
    )),
    file_name TEXT,
    file_path TEXT,
    status TEXT NOT NULL DEFAULT 'uploading'
        CHECK(status IN (
            'uploading', 'processing', 'summarizing', 'ready', 'failed', 'error'
        )),
    meeting_date TIMESTAMP,
    transcript TEXT,
    error_message TEXT,
    content_hash TEXT,
    processing_started_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    summary_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(summary_status IN ('pending', 'ready', 'failed', 'generating')),
    summary_lock_owner TEXT DEFAULT NULL
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    schema_row = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='meetings'"
        )
    ).fetchone()
    if schema_row and "'summarizing'" in (schema_row[0] or ""):
        return  # already relaxed — idempotent

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text("DROP TABLE IF EXISTS meetings_new"))

    op.execute(sa.text(_CREATE_TABLE_SQL))
    op.execute(
        sa.text(
            "INSERT INTO meetings_new "
            "(id, title, description, file_type, file_name, file_path, status, "
            "meeting_date, transcript, error_message, content_hash, processing_started_at, "
            "created_at, updated_at, summary_status, summary_lock_owner) "
            "SELECT id, title, description, file_type, file_name, file_path, status, "
            "meeting_date, transcript, error_message, content_hash, processing_started_at, "
            "created_at, updated_at, summary_status, summary_lock_owner "
            "FROM meetings"
        )
    )
    op.execute(sa.text("DROP TABLE meetings"))
    op.execute(sa.text("ALTER TABLE meetings_new RENAME TO meetings"))

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_meetings_content_hash "
            "ON meetings(content_hash)"
        )
    )

    op.execute(sa.text("PRAGMA foreign_keys=ON"))


_ORIGINAL_CREATE_TABLE_SQL = """\
CREATE TABLE meetings_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    file_type TEXT CHECK(file_type IN (
        'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
    )),
    file_name TEXT,
    file_path TEXT,
    status TEXT NOT NULL DEFAULT 'uploading'
        CHECK(status IN (
            'uploading', 'processing', 'ready', 'failed', 'error'
        )),
    meeting_date TIMESTAMP,
    transcript TEXT,
    error_message TEXT,
    content_hash TEXT,
    processing_started_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    summary_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(summary_status IN ('pending', 'ready', 'failed', 'generating')),
    summary_lock_owner TEXT DEFAULT NULL
)
"""


def downgrade() -> None:
    bind = op.get_bind()
    schema_row = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='meetings'"
        )
    ).fetchone()
    if not schema_row or "'summarizing'" not in (schema_row[0] or ""):
        return

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text("DROP TABLE IF EXISTS meetings_new"))

    op.execute(sa.text(_ORIGINAL_CREATE_TABLE_SQL))
    # Map 'summarizing' back to 'processing' during downgrade
    op.execute(
        sa.text(
            "INSERT INTO meetings_new "
            "(id, title, description, file_type, file_name, file_path, status, "
            "meeting_date, transcript, error_message, content_hash, processing_started_at, "
            "created_at, updated_at, summary_status, summary_lock_owner) "
            "SELECT id, title, description, file_type, file_name, file_path, "
            "CASE WHEN status='summarizing' THEN 'processing' ELSE status END, "
            "meeting_date, transcript, error_message, content_hash, processing_started_at, "
            "created_at, updated_at, summary_status, summary_lock_owner "
            "FROM meetings"
        )
    )
    op.execute(sa.text("DROP TABLE meetings"))
    op.execute(sa.text("ALTER TABLE meetings_new RENAME TO meetings"))

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_meetings_content_hash "
            "ON meetings(content_hash)"
        )
    )

    op.execute(sa.text("PRAGMA foreign_keys=ON"))
