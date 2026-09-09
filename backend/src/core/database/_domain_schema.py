"""Additive single-principal project registry and query revision fences."""

DOMAIN_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS projects (
        user_id TEXT NOT NULL, project_id TEXT NOT NULL, name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(user_id, project_id))""",
    """CREATE TABLE IF NOT EXISTS project_files (
        user_id TEXT NOT NULL, project_id TEXT NOT NULL,
        file_id INTEGER NOT NULL REFERENCES meeting_files(id) ON DELETE CASCADE,
        PRIMARY KEY(user_id, project_id, file_id),
        FOREIGN KEY(user_id, project_id) REFERENCES projects(user_id, project_id)
        ON DELETE CASCADE)""",
    """INSERT OR IGNORE INTO projects(user_id,project_id,name)
        SELECT DISTINCT user_id,project_id,project_id FROM user_memories
        WHERE project_id IS NOT NULL AND project_id!=''""",
    """CREATE TABLE IF NOT EXISTS memory_query_epochs (
        user_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_versions_user_key_revision "
    "ON memory_fact_versions(user_id,memory_key,revision DESC)",
]
for _table in ("user_memories", "memory_fact_versions"):
    for _event, _ref in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        _operation = _event
        if _table == "user_memories" and _event == "UPDATE":
            _operation += (
                " OF key,revision,salience,updated_at,last_confirmed_at,expires_at,"
                "valid_from,valid_to,due_at,assertion_status,value,project_id,action_status,assignee"
            )
        DOMAIN_STATEMENTS.append(f"""CREATE TRIGGER IF NOT EXISTS epoch_{_table}_{_event.lower()}
            AFTER {_operation} ON {_table} BEGIN
            INSERT INTO memory_query_epochs(user_id,epoch) VALUES({_ref}.user_id,1)
            ON CONFLICT(user_id) DO UPDATE SET epoch=epoch+1;
            END""")
for _column in ("expires_at", "valid_from", "valid_to", "due_at"):
    DOMAIN_STATEMENTS.append(
        f"CREATE INDEX IF NOT EXISTS idx_memory_clock_{_column} "
        f"ON user_memories(user_id,julianday({_column}))"
    )

DOMAIN_SCHEMA_SQL = ";\n".join(DOMAIN_STATEMENTS) + ";"


def ensure_domain_schema(conn):
    for table in ("meeting_files", "meeting_file_semantic_events"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "business_domain" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN business_domain "
                "TEXT NOT NULL DEFAULT 'unspecified'"
            )
    for statement in DOMAIN_STATEMENTS:
        conn.execute(statement)
    if "revision" not in {row[1] for row in conn.execute("PRAGMA table_info(projects)")}:
        conn.execute("ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
