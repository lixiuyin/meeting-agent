"""Shared helpers for the schema migration system.

Extracted from ``_migrations.py`` to keep the migration list and the
runner logic separate from the low-level SQL-parsing utilities.
"""

import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

# Regex: captures table_name and column_name from ALTER TABLE ... ADD COLUMN ...
ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+\"?(\w+)\"?\s+ADD\s+COLUMN\s+\"?(\w+)\"?",
    re.IGNORECASE,
)

# Regex: detects PRAGMA foreign_keys=OFF (case-insensitive)
PRAGMA_FK_OFF_RE = re.compile(
    r"PRAGMA\s+foreign_keys\s*=\s*OFF",
    re.IGNORECASE,
)


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if *column* already exists in *table*."""
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.OperationalError:
        return False
    return any(row[1] == column for row in rows)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL into individual statements, respecting trigger bodies."""
    statements: list[str] = []
    current_parts: list[str] = []
    in_trigger = False
    begin_depth = 0

    for part in sql.split(";"):
        stripped = part.strip()
        if not stripped:
            continue
        while stripped.startswith("--"):
            newline_idx = stripped.find("\n")
            if newline_idx == -1:
                stripped = ""
                break
            stripped = stripped[newline_idx + 1 :].strip()
        if not stripped:
            continue

        if not in_trigger:
            upper = stripped.upper()
            if "CREATE TRIGGER" in upper and "BEGIN" in upper:
                if stripped.upper().rstrip().endswith("END"):
                    statements.append(stripped)
                    continue
                in_trigger = True
                current_parts = [stripped]
                begin_depth += upper.count("BEGIN") - upper.count("END")
            else:
                statements.append(stripped)
        else:
            current_parts.append(stripped)
            upper = stripped.upper()
            begin_depth += upper.count("BEGIN") - upper.count("END")
            if begin_depth <= 0 and (upper == "END" or upper.endswith("END")):
                statements.append("; ".join(current_parts))
                current_parts = []
                in_trigger = False
                begin_depth = 0

    if current_parts:
        statements.append("; ".join(current_parts))

    return statements


def extract_alter_add_columns(sql: str) -> list[tuple[str, str]]:
    """Extract all (table, column) pairs from ALTER TABLE ADD COLUMN statements."""
    pairs: list[tuple[str, str]] = []
    for stmt in split_sql_statements(sql):
        match = ALTER_ADD_COLUMN_RE.match(stmt.strip())
        if match:
            pairs.append((match.group(1), match.group(2)))
    return pairs


def migration_columns_already_present(
    conn: sqlite3.Connection,
    sql: str,
) -> bool:
    """Return True if every ALTER TABLE ADD COLUMN in *sql* already exists."""
    alter_pairs = extract_alter_add_columns(sql)
    if not alter_pairs:
        return False
    return all(column_exists(conn, table, column) for table, column in alter_pairs)
