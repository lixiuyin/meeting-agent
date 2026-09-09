"""Schema migration definitions — aggregator loading categorized migration files.

Each entry is a (version, description, sql) tuple applied sequentially
on startup.  Extracted from ``_migrations.py`` to keep that module focused
on the runner logic.
"""

from ._domain_schema import DOMAIN_SCHEMA_SQL
from ._memory_schema import MEMORY_SEMANTICS_SCHEMA_SQL
from ._migrations_core import _MIGRATIONS_CORE
from ._migrations_features import _MIGRATIONS_FEATURES
from ._migrations_memory import _MIGRATIONS_MEMORY
from ._relation_evidence_schema import RELATION_EVIDENCE_SCHEMA_SQL
from ._summary_cjk_schema import FILE_SUMMARY_CJK_SCHEMA_SQL
from .conversation_state import CONVERSATION_STATE_SCHEMA_SQL
from .idempotency_schema import IDEMPOTENCY_LIFECYCLE_SCHEMA_SQL
from .index_state import INDEX_HEALTH_SCHEMA_SQL
from .jobs import JOBS_SCHEMA_SQL
from .meeting_file_semantics import MEETING_FILE_SEMANTICS_SCHEMA_SQL
from .memory_lifecycle import LIFECYCLE_SQL

_MIGRATIONS: list[tuple[int, str, str]] = (
    _MIGRATIONS_CORE + _MIGRATIONS_MEMORY + _MIGRATIONS_FEATURES
)

# Exported so tests can reuse the full schema instead of duplicating DDL.
# ``_MIGRATIONS`` is frozen at v52. New production changes are Alembic-only;
# bootstrap SQL is appended for isolated unit tests that build an empty schema
# without running application lifespan.
SCHEMA_SQL = "\n".join(
    [
        *(sql for _, _, sql in _MIGRATIONS),
        JOBS_SCHEMA_SQL,
        INDEX_HEALTH_SCHEMA_SQL,
        MEMORY_SEMANTICS_SCHEMA_SQL,
        FILE_SUMMARY_CJK_SCHEMA_SQL,
        RELATION_EVIDENCE_SCHEMA_SQL,
        CONVERSATION_STATE_SCHEMA_SQL,
        "ALTER TABLE chat_messages ADD COLUMN degradation_reason TEXT;",
        "ALTER TABLE chat_sessions ADD COLUMN parent_session_id TEXT "
        "REFERENCES chat_sessions(id) ON DELETE SET NULL;",
        "ALTER TABLE chat_sessions ADD COLUMN branched_from_message_id INTEGER;",
        "ALTER TABLE chat_sessions ADD COLUMN branch_reason TEXT;",
        IDEMPOTENCY_LIFECYCLE_SCHEMA_SQL,
        MEETING_FILE_SEMANTICS_SCHEMA_SQL,
        "ALTER TABLE meeting_files ADD COLUMN business_domain TEXT NOT NULL DEFAULT 'unspecified';",
        "ALTER TABLE meeting_file_semantic_events ADD COLUMN business_domain "
        "TEXT NOT NULL DEFAULT 'unspecified';",
        DOMAIN_SCHEMA_SQL,
        LIFECYCLE_SQL,
    ]
)
