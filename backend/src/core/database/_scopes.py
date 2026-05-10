"""Junction-table CRUD for memory and entity scope IDs.

Replaces the legacy CSV columns ``meeting_ids`` / ``file_ids`` on
``user_memories`` and ``memory_entities`` with relational ``memory_scopes``
and ``entity_scopes`` tables. The junction tables are now the source of
truth; the CSV columns remain in the schema only for legacy data and are
neither read nor written by current code paths after migration #32.

Cascade deletion is enforced by SQLite foreign keys (``ON DELETE CASCADE``),
so removing a memory or entity automatically prunes its scope rows.
"""

import sqlite3

ScopeKind = str  # "memory" | "entity"

# Validated lookup maps — eliminates f-string SQL interpolation.
_SCOPE_TABLE: dict[ScopeKind, str] = {
    "memory": "memory_scopes",
    "entity": "entity_scopes",
}
_SCOPE_ID_COL: dict[ScopeKind, str] = {
    "memory": "memory_id",
    "entity": "entity_id",
}


def _table_for(kind: ScopeKind) -> str:
    try:
        return _SCOPE_TABLE[kind]
    except KeyError:
        raise ValueError(f"Unknown scope kind: {kind!r}") from None


def _id_column_for(kind: ScopeKind) -> str:
    try:
        return _SCOPE_ID_COL[kind]
    except KeyError:
        raise ValueError(f"Unknown scope kind: {kind!r}") from None


def add_scopes(
    conn: sqlite3.Connection,
    *,
    kind: ScopeKind,
    owner_id: int,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> None:
    """Insert ``(owner, scope_type, scope_id)`` rows. Idempotent.

    Existing rows are preserved (``INSERT OR IGNORE``), so this implements
    "union" semantics on repeated calls — matching the legacy CSV append
    behaviour.
    """
    if not meeting_ids and not file_ids:
        return
    table = _table_for(kind)
    id_col = _id_column_for(kind)
    rows: list[tuple[int, str, int]] = []
    for mid in meeting_ids or []:
        rows.append((owner_id, "meeting", int(mid)))
    for fid in file_ids or []:
        rows.append((owner_id, "file", int(fid)))
    if not rows:
        return
    conn.executemany(
        f"INSERT OR IGNORE INTO {table} ({id_col}, scope_type, scope_id) VALUES (?, ?, ?)",
        rows,
    )


def get_scopes(
    conn: sqlite3.Connection,
    *,
    kind: ScopeKind,
    owner_id: int,
) -> tuple[list[int], list[int]]:
    """Return ``(meeting_ids, file_ids)`` for an owner, ascending by id.

    Returns two empty lists when the owner has no scope rows.
    """
    table = _table_for(kind)
    id_col = _id_column_for(kind)
    rows = conn.execute(
        f"SELECT scope_type, scope_id FROM {table} WHERE {id_col}=? ORDER BY scope_type, scope_id",
        (owner_id,),
    ).fetchall()
    meeting_ids: list[int] = []
    file_ids: list[int] = []
    for r in rows:
        scope_id = int(r["scope_id"])
        if r["scope_type"] == "meeting":
            meeting_ids.append(scope_id)
        elif r["scope_type"] == "file":
            file_ids.append(scope_id)
    return meeting_ids, file_ids


def clear_scopes(conn: sqlite3.Connection, *, kind: ScopeKind, owner_id: int) -> None:
    """Remove every scope row for an owner. Used to overwrite (not merge)."""
    table = _table_for(kind)
    id_col = _id_column_for(kind)
    conn.execute(f"DELETE FROM {table} WHERE {id_col}=?", (owner_id,))


def encode_scope_csv(scope_ids: list[int] | None) -> str | None:
    """Compatibility helper: encode a scope id list as the legacy CSV format."""
    if not scope_ids:
        return None
    return ",".join(str(int(x)) for x in scope_ids)


def get_owner_id_for_memory(conn: sqlite3.Connection, *, user_id: str, key: str) -> int | None:
    """Resolve the integer ``user_memories.id`` for a (user_id, key) pair."""
    row = conn.execute(
        "SELECT id FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    return int(row["id"]) if row else None


# Reusable subquery fragments for embedding scope IDs into existing SELECTs as
# the legacy CSV-format ``meeting_ids`` / ``file_ids`` columns. The returned
# value is a comma-separated string (or NULL when there are no scope rows),
# preserving the previous on-disk shape so callers can keep using
# ``_decode_id_list`` to parse back into a Python list.
MEMORY_SCOPE_COLUMNS = """
    (SELECT GROUP_CONCAT(scope_id) FROM memory_scopes
        WHERE memory_id={alias}.id AND scope_type='meeting') AS meeting_ids,
    (SELECT GROUP_CONCAT(scope_id) FROM memory_scopes
        WHERE memory_id={alias}.id AND scope_type='file') AS file_ids
"""

ENTITY_SCOPE_COLUMNS = """
    (SELECT GROUP_CONCAT(scope_id) FROM entity_scopes
        WHERE entity_id={alias}.id AND scope_type='meeting') AS meeting_ids,
    (SELECT GROUP_CONCAT(scope_id) FROM entity_scopes
        WHERE entity_id={alias}.id AND scope_type='file') AS file_ids
"""
