"""Baseline revision — consolidates all 27 legacy migrations.

Revision ID: 20260414_000001
Revises:
Create Date: 2026-04-14 20:58:00
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260414_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply all legacy migrations in a single transaction.

    Reuses the existing _apply_migration helper so trigger parsing,
    duplicate-column tolerance, and PRAGMA foreign_key bookkeeping are
    preserved.
    """
    from src.core.database._migrations import _MIGRATIONS, _apply_migration

    bind = op.get_bind()
    proxied = getattr(bind, "connection", None)
    raw_conn = getattr(proxied, "driver_connection", None)
    if raw_conn is None:
        raw_conn = getattr(proxied, "connection", None)
    if raw_conn is None:
        raise RuntimeError("Unable to access sqlite driver connection from Alembic bind")

    # Ensure schema_version table exists for backward compatibility
    raw_conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  description TEXT NOT NULL,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )

    for version, description, sql in _MIGRATIONS:
        _apply_migration(raw_conn, sql)
        raw_conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )

    raw_conn.commit()


def downgrade() -> None:
    """Drop all application tables (destructive — dev/test only)."""
    op.execute("PRAGMA foreign_keys=OFF;")

    tables = [
        "schema_version",
        "alembic_version",
        "pending_vector_deletions",
        "index_state",
        "speaker_mappings",
        "memory_relations",
        "memory_entities",
        "session_summaries",
        "chat_messages_fts",
        "bm25_chunks",
        "bm25_index",
        "bm25_stats",
        "chat_messages",
        "chat_sessions",
        "idempotency_keys",
        "user_memories",
        "memory_decay_state",
        "meeting_files",
        "meetings",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table};")

    op.execute("PRAGMA foreign_keys=ON;")
