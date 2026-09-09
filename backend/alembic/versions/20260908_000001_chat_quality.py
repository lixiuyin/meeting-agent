"""Keep incomplete-answer status when restoring chat history."""

from sqlalchemy import inspect

from alembic import op

revision = "20260908_000001"
down_revision = "20260907_000001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("chat_messages")}
    if "degradation_reason" not in columns:
        op.execute("ALTER TABLE chat_messages ADD COLUMN degradation_reason TEXT")


def downgrade():
    raise RuntimeError("Restore a verified backup to preserve incomplete-answer history")
