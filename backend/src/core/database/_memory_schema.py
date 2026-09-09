"""Forward-compatible schema for evidence-backed long-term memory facts."""

from __future__ import annotations

import sqlite3

MEMORY_SEMANTICS_COLUMNS: dict[str, str] = {
    "revision": "INTEGER NOT NULL DEFAULT 1",
    "archived_at": "TEXT",
    "archive_reason": "TEXT",
    "vector_attempts": "INTEGER NOT NULL DEFAULT 0",
    "vector_retry_at": "TIMESTAMP",
    # ``importance`` remains as a compatibility mirror for older API clients.
    # New ranking code uses the explicitly named fields below.
    "salience": "REAL NOT NULL DEFAULT 3.0",
    "confidence": "REAL NOT NULL DEFAULT 1.0",
    "freshness_score": "REAL NOT NULL DEFAULT 1.0",
    "usefulness_score": "REAL NOT NULL DEFAULT 0.0",
    "usefulness_count": "INTEGER NOT NULL DEFAULT 0",
    "last_confirmed_at": "TIMESTAMP",
    "valid_from": "TIMESTAMP",
    "valid_to": "TIMESTAMP",
    "evidence_message_ids": "TEXT",
    "evidence_excerpt": "TEXT",
    "evidence_refs": "TEXT",
    "conflicts_with": "TEXT",
    "fact_type": "TEXT NOT NULL DEFAULT 'fact'",
    "assertion_status": "TEXT NOT NULL DEFAULT 'confirmed'",
    "project_id": "TEXT",
    "subject": "TEXT",
    "predicate": "TEXT",
    "object_value": "TEXT",
    "action_status": "TEXT",
    "assignee": "TEXT",
    "due_at": "TIMESTAMP",
    "retracted_at": "TIMESTAMP",
}

MEMORY_FACT_VERSIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_fact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES user_memories(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    fact_type TEXT NOT NULL DEFAULT 'fact',
    assertion_status TEXT NOT NULL DEFAULT 'confirmed',
    project_id TEXT,
    subject TEXT,
    predicate TEXT,
    object_value TEXT,
    action_status TEXT,
    assignee TEXT,
    due_at TIMESTAMP,
    category TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    evidence_message_ids TEXT,
    evidence_excerpt TEXT,
    evidence_refs TEXT,
    conflicts_with TEXT,
    meeting_ids TEXT,
    file_ids TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    recorded_to TIMESTAMP,
    UNIQUE(memory_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_memory_fact_versions_lookup
    ON memory_fact_versions(user_id, memory_key, revision DESC);
"""

MEMORY_SEMANTICS_SCHEMA_SQL = (
    "\n".join(
        f"ALTER TABLE user_memories ADD COLUMN {name} {definition};"
        for name, definition in MEMORY_SEMANTICS_COLUMNS.items()
    )
    + """
UPDATE user_memories
   SET salience = importance,
       last_confirmed_at = COALESCE(last_confirmed_at, updated_at, created_at)
 WHERE salience IS NULL OR last_confirmed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_memories_user_salience
    ON user_memories(user_id, salience DESC)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_memories_lifecycle
    ON user_memories(user_id, assertion_status, fact_type, project_id);
"""
    + MEMORY_FACT_VERSIONS_SCHEMA_SQL
)


def ensure_memory_semantics_schema(conn: sqlite3.Connection) -> None:
    """Create post-v52 memory columns in isolated test/dev databases.

    Production upgrades use Alembic.  This helper exists for the legacy
    ``init_db`` bootstrap used by unit tests and emergency development mode.
    """
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(user_memories)")}
    for name, definition in MEMORY_SEMANTICS_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE user_memories ADD COLUMN {name} {definition}")
    conn.execute("UPDATE user_memories SET salience=importance WHERE salience IS NULL")
    conn.execute(
        "UPDATE user_memories SET last_confirmed_at=COALESCE(updated_at, created_at) "
        "WHERE last_confirmed_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_user_salience "
        "ON user_memories(user_id, salience DESC) WHERE superseded_by IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_lifecycle "
        "ON user_memories(user_id, assertion_status, fact_type, project_id)"
    )
    conn.executescript(MEMORY_FACT_VERSIONS_SCHEMA_SQL)
    version_existing = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(memory_fact_versions)")
    }
    for name, definition in (
        ("action_status", "TEXT"),
        ("assignee", "TEXT"),
        ("due_at", "TIMESTAMP"),
        ("evidence_refs", "TEXT"),
        ("recorded_to", "TIMESTAMP"),
    ):
        if name not in version_existing:
            conn.execute(f"ALTER TABLE memory_fact_versions ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_project_actions "
        "ON user_memories(user_id, project_id, fact_type, action_status, due_at)"
    )
