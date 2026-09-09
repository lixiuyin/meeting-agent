"""Add CJK trigram search for file summaries.

Revision ID: 20260904_000004
Revises: 20260904_000003
"""

from alembic import op

revision = "20260904_000004"
down_revision = "20260904_000003"
branch_labels = None
depends_on = None


def _raw_connection():
    bind = op.get_bind()
    proxied = getattr(bind, "connection", None)
    raw = getattr(proxied, "driver_connection", None)
    return raw or getattr(proxied, "connection", None)


def upgrade() -> None:
    from src.core.database._summary_cjk_schema import FILE_SUMMARY_CJK_SCHEMA_SQL

    raw = _raw_connection()
    if raw is None:
        raise RuntimeError("Unable to access sqlite driver connection")
    raw.executescript(FILE_SUMMARY_CJK_SCHEMA_SQL)
    raw.execute("INSERT INTO file_summary_fts_cjk(file_summary_fts_cjk) VALUES('rebuild')")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS file_summary_fts_cjk_au")
    op.execute("DROP TRIGGER IF EXISTS file_summary_fts_cjk_ad")
    op.execute("DROP TRIGGER IF EXISTS file_summary_fts_cjk_ai")
    op.execute("DROP TABLE IF EXISTS file_summary_fts_cjk")
