"""Recall archival and provenance for rebuildable user profiles.

Archiving never changes business validity or deletes the fact/version ledger.
Explicit user/account erasure continues to use the existing deletion APIs.
"""

import json

LIFECYCLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_profile_provenance (
    user_id TEXT PRIMARY KEY,
    profile_revision INTEGER NOT NULL,
    source_revisions TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    generator_version TEXT NOT NULL DEFAULT 'facts-only-v1'
);
CREATE INDEX IF NOT EXISTS idx_memory_recall_archive ON user_memories(user_id, archived_at);
"""
for event, reference in (("INSERT", "NEW"), ("DELETE", "OLD"), ("UPDATE", "NEW")):
    operation = event
    if event == "UPDATE":
        operation += (
            " OF revision, value, assertion_status, expires_at, valid_from, valid_to, archived_at"
        )
    LIFECYCLE_SQL += f"""
CREATE TRIGGER IF NOT EXISTS invalidate_profile_{event.lower()}
AFTER {operation} ON user_memories
BEGIN
    DELETE FROM memory_profile_provenance WHERE user_id={reference}.user_id;
END;
"""


def ensure_lifecycle_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(user_memories)")}
    for name in ("archived_at", "archive_reason"):
        if name not in columns:
            conn.execute(f"ALTER TABLE user_memories ADD COLUMN {name} TEXT")
    # executescript commits implicitly; callers may be inside a migration.
    import sqlite3

    statement = ""
    for line in LIFECYCLE_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""


def archive_memories(conn, rows, *, reason: str) -> int:
    """Archive an already-selected set and enqueue vector cleanup atomically."""
    count = 0
    for row in rows:
        memory = dict(row)
        identity = memory["id"]
        if memory.get("embedding_id"):
            conn.execute(
                "INSERT OR IGNORE INTO pending_vector_deletions(collection,embedding_id) "
                "VALUES ('memory',?)",
                (memory["embedding_id"],),
            )
        count += conn.execute(
            "UPDATE user_memories SET archived_at=CURRENT_TIMESTAMP,archive_reason=?, "
            "vector_state='inactive',embedding_id=NULL WHERE id=? AND archived_at IS NULL",
            (reason, identity),
        ).rowcount
    return count


def valid_profile(
    conn,
    user_id: str,
    *,
    excluded_session_ids: set[str] | None = None,
) -> str | None:
    from ..memory_policy import is_active_memory
    from .memories import get_memory_full

    provenance = conn.execute(
        "SELECT * FROM memory_profile_provenance WHERE user_id=?",
        (user_id,),
    ).fetchone()
    profile = get_memory_full(conn, user_id=user_id, key="__profile__")
    if not provenance or not profile or profile["revision"] != provenance["profile_revision"]:
        return None
    if not is_active_memory(profile):
        return None
    sources = json.loads(provenance["source_revisions"])
    if not sources:
        return None
    for key, revision in sources.items():
        source = get_memory_full(conn, user_id=user_id, key=key)
        if not source or source["revision"] != revision or not is_active_memory(source):
            return None
        if excluded_session_ids and source.get("session_id") in excluded_session_ids:
            return None
    return profile["value"]


def profile_sources(conn, user_id: str) -> list[dict]:
    from .memories import get_memory_full

    row = conn.execute(
        "SELECT source_revisions FROM memory_profile_provenance WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        return []
    return [
        memory
        for key in json.loads(row[0])
        if (memory := get_memory_full(conn, user_id=user_id, key=key)) is not None
    ]
