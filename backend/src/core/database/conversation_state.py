"""Durable context checkpoints and replay journals; raw messages remain primary."""

CONVERSATION_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_context_checkpoints (
    session_id TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    through_message_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    model TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chat_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    question TEXT NOT NULL,
    session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running',
    owner TEXT NOT NULL,
    lease_expires_at TIMESTAMP NOT NULL,
    event_bytes INTEGER NOT NULL DEFAULT 0,
    saved_ai_id INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chat_run_events (
    run_id TEXT NOT NULL REFERENCES chat_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_chat_runs_owner ON chat_runs(user_id, created_at);
"""


def ensure_conversation_state_schema(conn) -> None:
    for statement in CONVERSATION_STATE_SCHEMA_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)
    session_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
    }
    if "parent_session_id" not in session_columns:
        conn.execute(
            "ALTER TABLE chat_sessions ADD COLUMN parent_session_id TEXT "
            "REFERENCES chat_sessions(id) ON DELETE SET NULL"
        )
    if "branched_from_message_id" not in session_columns:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN branched_from_message_id INTEGER")
    if "branch_reason" not in session_columns:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN branch_reason TEXT")
    if "config_json" not in session_columns:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN config_json TEXT")
    if "config_version" not in session_columns:
        conn.execute(
            "ALTER TABLE chat_sessions ADD COLUMN config_version INTEGER NOT NULL DEFAULT 1"
        )
    if "task_state_json" not in session_columns:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN task_state_json TEXT")
    if "task_state_version" not in session_columns:
        conn.execute(
            "ALTER TABLE chat_sessions ADD COLUMN task_state_version INTEGER NOT NULL DEFAULT 1"
        )
    message_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
    }
    if "degradation_reason" not in message_columns:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN degradation_reason TEXT")
