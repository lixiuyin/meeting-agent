"""Preserve edit, retry and withdrawal history as conversation branches."""

from sqlalchemy import inspect

from alembic import op

revision = "20260908_000002"
down_revision = "20260908_000001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("chat_sessions")}
    if "parent_session_id" not in columns:
        op.execute(
            "ALTER TABLE chat_sessions ADD COLUMN parent_session_id TEXT "
            "REFERENCES chat_sessions(id) ON DELETE SET NULL"
        )
    if "branched_from_message_id" not in columns:
        op.execute("ALTER TABLE chat_sessions ADD COLUMN branched_from_message_id INTEGER")
    if "branch_reason" not in columns:
        op.execute("ALTER TABLE chat_sessions ADD COLUMN branch_reason TEXT")


def downgrade():
    raise RuntimeError("Restore a verified backup to preserve conversation branch provenance")
