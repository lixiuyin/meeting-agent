"""Apply legacy schema versions 48-49 through the production migration path.

Revision ID: 20260903_000001
Revises: 20260503_000001
Create Date: 2026-09-03 17:25:00
"""

from __future__ import annotations

from alembic import op

revision = "20260903_000001"
down_revision = "20260503_000001"
branch_labels = None
depends_on = None


def _raw_connection():
    bind = op.get_bind()
    proxied = getattr(bind, "connection", None)
    raw_conn = getattr(proxied, "driver_connection", None)
    if raw_conn is None:
        raw_conn = getattr(proxied, "connection", None)
    if raw_conn is None:
        raise RuntimeError("Unable to access sqlite driver connection from Alembic bind")
    return raw_conn


def upgrade() -> None:
    from src.core.database._migrations import _MIGRATIONS, _apply_migration

    raw_conn = _raw_connection()
    raw_conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, description TEXT NOT NULL, "
        "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    for version, description, sql in _MIGRATIONS:
        if version not in {48, 49}:
            continue
        already_applied = raw_conn.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", (version,)
        ).fetchone()
        if not already_applied:
            _apply_migration(raw_conn, sql)
            raw_conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
    raw_conn.commit()


def downgrade() -> None:
    raw_conn = _raw_connection()
    raw_conn.executescript(
        """
        DROP TRIGGER IF EXISTS bm25_chunks_cjk_ai;
        DROP TRIGGER IF EXISTS bm25_chunks_cjk_ad;
        DROP TRIGGER IF EXISTS bm25_chunks_cjk_au;
        DROP TABLE IF EXISTS bm25_chunks_cjk;
        DROP TABLE IF EXISTS kv_state;
        DELETE FROM schema_version WHERE version IN (48, 49);
        """
    )
    raw_conn.commit()
