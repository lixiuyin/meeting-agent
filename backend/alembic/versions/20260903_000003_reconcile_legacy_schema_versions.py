"""Reconcile legacy schema versions 45-50 for already-deployed databases.

Revision ID: 20260903_000003
Revises: 20260903_000002
Create Date: 2026-09-03 18:45:00
"""

from __future__ import annotations

from alembic import op

revision = "20260903_000003"
down_revision = "20260903_000002"
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
    """Apply any missing late legacy migration and record the exact version."""
    from src.core.database._migrations import _MIGRATIONS, _apply_migration

    raw_conn = _raw_connection()
    for version, description, sql in _MIGRATIONS:
        if version < 45 or version > 50:
            continue
        already_applied = raw_conn.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", (version,)
        ).fetchone()
        if already_applied:
            continue
        _apply_migration(raw_conn, sql)
        raw_conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    raw_conn.commit()


def downgrade() -> None:
    # This revision repairs metadata and potentially applies forward-only table
    # rebuilds. Reversing it would risk deleting schema that predated the repair.
    pass
