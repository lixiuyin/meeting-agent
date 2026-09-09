"""Evidence and temporal fields for knowledge-graph assertions."""

import sqlite3

RELATION_EVIDENCE_COLUMNS: dict[str, str] = {
    "evidence_message_ids": "TEXT",
    "valid_from": "TIMESTAMP",
    "valid_to": "TIMESTAMP",
    # SQLite cannot add a column with a non-constant CURRENT_TIMESTAMP
    # default to a non-empty table. Inserts set this field explicitly and the
    # schema reconciler backfills legacy rows below.
    "updated_at": "TIMESTAMP",
}

RELATION_EVIDENCE_SCHEMA_SQL = "\n".join(
    f"ALTER TABLE memory_relations ADD COLUMN {name} {definition};"
    for name, definition in RELATION_EVIDENCE_COLUMNS.items()
)


def ensure_relation_evidence_schema(conn: sqlite3.Connection) -> None:
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_relations)")}
    for name, definition in RELATION_EVIDENCE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE memory_relations ADD COLUMN {name} {definition}")
    conn.execute(
        "UPDATE memory_relations "
        "SET updated_at=COALESCE(created_at, CURRENT_TIMESTAMP) "
        "WHERE updated_at IS NULL"
    )
