"""User memory CRUD operations."""

import contextlib
import json
import sqlite3
from typing import Any

from ..memory_query import (
    ActionConstraints,
    memory_scope_sql,
    parse_action_constraints,
)
from ._scopes import MEMORY_SCOPE_COLUMNS, add_scopes, get_scopes


class MemoryScopeConflictError(ValueError):
    """A value-changing upsert attempted to broaden/change an existing scope."""


class MemoryRevisionConflictError(RuntimeError):
    """A compare-and-swap memory write observed a different current revision."""


def write_memory_audit(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    memory_key: str,
    action: str,
    old_value: str | None = None,
    new_value: str | None = None,
    detail: str | None = None,
) -> None:
    """Write a row to the memory_audit_log table (best-effort).

    Sets expires_at to 90 days from now so stale rows can be swept.
    """
    with contextlib.suppress(Exception):
        conn.execute(
            "INSERT INTO memory_audit_log"
            " (user_id, memory_key, action, old_value, new_value, detail, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', '+90 days'))",
            (user_id, memory_key, action, old_value, new_value, detail),
        )


def cleanup_expired_audit_logs(conn: sqlite3.Connection) -> int:
    """Delete expired memory_audit_log rows. Returns count deleted."""
    try:
        cursor = conn.execute(
            "DELETE FROM memory_audit_log "
            "WHERE expires_at IS NOT NULL "
            "AND expires_at <= strftime('%Y-%m-%d %H:%M:%S', 'now')"
        )
        return cursor.rowcount
    except Exception:
        return 0


def _encode_id_list(ids: list[int] | None) -> str | None:
    if not ids:
        return None
    return ",".join(str(i) for i in ids)


def _decode_id_list(raw: str | None) -> list[int] | None:
    """Decode comma-separated ID list, deduplicating while preserving order."""
    if not raw:
        return None
    seen: set[int] = set()
    result: list[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        val = int(x)
        if val in seen:
            continue
        seen.add(val)
        result.append(val)
    return result or None


_MEMORY_SCOPE_COLS_M = MEMORY_SCOPE_COLUMNS.format(alias="m")

# Explicit base columns for SELECTs that join the scope subqueries. We avoid
# ``SELECT m.*`` here because the legacy CSV columns ``meeting_ids`` /
# ``file_ids`` still exist on ``user_memories`` and would shadow our junction
# subquery aliases of the same name in sqlite3.Row lookups.
_MEMORY_BASE_COLS_M = (
    "m.id, m.user_id, m.key, m.value, m.source, m.created_at, m.updated_at, "
    "m.importance, m.expires_at, m.last_accessed, m.access_count, m.category, "
    "m.embedding_id, m.session_id, m.turn_index, m.superseded_by, "
    "m.relevance_score, m.is_legacy_scope, m.salience, m.confidence, "
    "m.freshness_score, m.usefulness_score, m.usefulness_count, "
    "m.last_confirmed_at, m.valid_from, m.valid_to, m.evidence_message_ids, "
    "m.evidence_excerpt, m.evidence_refs, m.conflicts_with, m.vector_state, m.revision"
    ", m.fact_type, m.assertion_status, m.project_id, m.subject, m.predicate, "
    "m.object_value, m.action_status, m.assignee, m.due_at, m.retracted_at, "
    "m.archived_at, m.archive_reason"
)


def _record_memory_version(conn: sqlite3.Connection, memory_id: int) -> None:
    """Append the current authoritative fact snapshot exactly once."""
    row = conn.execute("SELECT * FROM user_memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        return
    # Every new revision closes the system-time interval of all older open
    # snapshots, including metadata-only edits that do not alter valid time.
    conn.execute(
        "UPDATE memory_fact_versions SET recorded_to=COALESCE(recorded_to, CURRENT_TIMESTAMP) "
        "WHERE memory_id=? AND revision < ?",
        (memory_id, int(row["revision"])),
    )
    meeting_ids, file_ids = get_scopes(conn, kind="memory", owner_id=memory_id)
    conn.execute(
        """INSERT OR IGNORE INTO memory_fact_versions
           (memory_id, user_id, memory_key, revision, value, source, fact_type,
            assertion_status, project_id, subject, predicate, object_value,
            action_status, assignee, due_at,
            category, confidence, valid_from, valid_to, evidence_message_ids,
            evidence_excerpt, evidence_refs, conflicts_with, meeting_ids, file_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            memory_id,
            row["user_id"],
            row["key"],
            row["revision"],
            row["value"],
            row["source"],
            row["fact_type"],
            row["assertion_status"],
            row["project_id"],
            row["subject"],
            row["predicate"],
            row["object_value"],
            row["action_status"],
            row["assignee"],
            row["due_at"],
            row["category"],
            row["confidence"],
            row["valid_from"],
            row["valid_to"],
            row["evidence_message_ids"],
            row["evidence_excerpt"],
            row["evidence_refs"],
            row["conflicts_with"],
            _encode_id_list(meeting_ids),
            _encode_id_list(file_ids),
        ),
    )


def _database_now(conn: sqlite3.Connection) -> str:
    """Return one SQLite-generated UTC timestamp for temporal fact transitions."""
    row = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS observed_at").fetchone()
    return str(row["observed_at"])


def _close_recorded_validity(
    conn: sqlite3.Connection, *, memory_id: int, revision: int, valid_to: str
) -> None:
    """Close a recorded revision's valid-time interval without changing its assertion."""
    conn.execute(
        "UPDATE memory_fact_versions SET valid_to=COALESCE(valid_to, ?), "
        "recorded_to=COALESCE(recorded_to, CURRENT_TIMESTAMP) "
        "WHERE memory_id=? AND revision=?",
        (valid_to, memory_id, revision),
    )


def set_memory(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    key: str,
    value: str,
    source: str = "manual",
    importance: float = 3,
    expires_at: str | None = None,
    category: str | None = None,
    embedding_id: str | None = None,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    confidence: float = 1.0,
    valid_from: str | None = None,
    valid_to: str | None = None,
    evidence_message_ids: list[int] | None = None,
    evidence_excerpt: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    conflicts_with: list[str] | None = None,
    fact_type: str = "fact",
    assertion_status: str = "confirmed",
    project_id: str | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    object_value: str | None = None,
    action_status: str | None = None,
    assignee: str | None = None,
    due_at: str | None = None,
    expected_revision: int | None = None,
) -> None:
    """Upsert a memory row without silently applying a new fact to old scopes.

    Re-confirming the same value may safely broaden its evidence scope. A value
    change may update the exact existing scope, or omit scope to mean "edit the
    existing record". Changing both value and explicit scope is ambiguous and
    rejected so callers must use a distinct fact key or an explicit merge flow.
    """
    existing = conn.execute(
        "SELECT id,value,assertion_status,revision,action_status,assignee,due_at,project_id "
        "FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    if expected_revision is not None:
        current_revision = int(existing["revision"]) if existing is not None else None
        if (expected_revision == -1 and existing is not None) or (
            expected_revision >= 0 and current_revision != expected_revision
        ):
            raise MemoryRevisionConflictError(
                f"memory revision changed for {key!r}: expected {expected_revision}, "
                f"current {current_revision}"
            )
    value_changed = existing is not None and any(
        existing[name] != next_value
        for name, next_value in (
            ("value", value),
            ("action_status", action_status),
            ("assignee", assignee),
            ("due_at", due_at),
            ("project_id", project_id),
        )
    )
    requested_scope = (set(meeting_ids or []), set(file_ids or []))
    if existing is not None and any(requested_scope) and value_changed:
        current_meetings, current_files = get_scopes(
            conn, kind="memory", owner_id=int(existing["id"])
        )
        current_scope = (set(current_meetings), set(current_files))
        if requested_scope != current_scope:
            raise MemoryScopeConflictError(
                "Refusing to change both memory value and scope; use a distinct key "
                "or update the existing scope explicitly"
            )
    status_opened = bool(
        existing is not None
        and existing["assertion_status"] != "confirmed"
        and assertion_status == "confirmed"
    )
    status_closed = bool(
        existing is not None
        and existing["assertion_status"] == "confirmed"
        and assertion_status != "confirmed"
    )
    transition_at = (
        (valid_from or _database_now(conn))
        if value_changed or status_opened or status_closed
        else None
    )
    resolved_valid_from = valid_from or (transition_at if value_changed or status_opened else None)
    resolved_valid_to = valid_to or (transition_at if status_closed else None)
    if existing is not None:
        if value_changed or status_closed:
            # Close the outgoing fact before snapshotting it.  The subsequent
            # upsert installs a new validity window for the replacement value.
            conn.execute(
                "UPDATE user_memories SET valid_to=COALESCE(valid_to, ?) WHERE id=?",
                (transition_at, int(existing["id"])),
            )
            _close_recorded_validity(
                conn,
                memory_id=int(existing["id"]),
                revision=int(existing["revision"]),
                valid_to=str(transition_at),
            )
        _record_memory_version(conn, int(existing["id"]))
    cursor = conn.execute(
        """INSERT INTO user_memories
           (user_id, key, value, source, importance, salience, confidence,
            freshness_score, expires_at, category, embedding_id,
            last_confirmed_at, valid_from, valid_to, evidence_message_ids,
            evidence_excerpt, evidence_refs, conflicts_with, fact_type, assertion_status,
            project_id, subject, predicate, object_value, retracted_at,
            action_status, assignee, due_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, CASE WHEN ?='retracted' THEN CURRENT_TIMESTAMP ELSE NULL END,
                   ?, ?, ?)
           ON CONFLICT(user_id, key) DO UPDATE SET
               revision=user_memories.revision+1, vector_attempts=0, vector_retry_at=NULL,
               value=?, source=?, importance=?, salience=?, confidence=?,
               freshness_score=1.0, expires_at=?, category=?,
               embedding_id=COALESCE(?, embedding_id),
               last_confirmed_at=CURRENT_TIMESTAMP,
               valid_from=COALESCE(?, valid_from), valid_to=?,
               evidence_message_ids=?, evidence_excerpt=?, evidence_refs=?, conflicts_with=?,
               fact_type=?, assertion_status=?, project_id=?, subject=?, predicate=?,
               object_value=?,
               action_status=?, assignee=?, due_at=?,
               retracted_at=CASE WHEN ?='retracted' THEN CURRENT_TIMESTAMP ELSE NULL END,
               superseded_by=NULL, archived_at=NULL, archive_reason=NULL,
               updated_at=CURRENT_TIMESTAMP
           RETURNING id""",
        (
            user_id,
            key,
            value,
            source,
            importance,
            importance,
            max(0.0, min(1.0, float(confidence))),
            expires_at,
            category,
            embedding_id,
            resolved_valid_from,
            resolved_valid_to,
            json.dumps(evidence_message_ids) if evidence_message_ids else None,
            evidence_excerpt,
            json.dumps(evidence_refs, ensure_ascii=False) if evidence_refs else None,
            json.dumps(conflicts_with) if conflicts_with else None,
            fact_type,
            assertion_status,
            project_id,
            subject,
            predicate,
            object_value,
            assertion_status,
            action_status,
            assignee,
            due_at,
            value,
            source,
            importance,
            importance,
            max(0.0, min(1.0, float(confidence))),
            expires_at,
            category,
            embedding_id,
            resolved_valid_from,
            resolved_valid_to,
            json.dumps(evidence_message_ids) if evidence_message_ids else None,
            evidence_excerpt,
            json.dumps(evidence_refs, ensure_ascii=False) if evidence_refs else None,
            json.dumps(conflicts_with) if conflicts_with else None,
            fact_type,
            assertion_status,
            project_id,
            subject,
            predicate,
            object_value,
            action_status,
            assignee,
            due_at,
            assertion_status,
        ),
    )
    row = cursor.fetchone()
    if row is not None and embedding_id is None:
        conn.execute(
            "UPDATE user_memories SET vector_state=? WHERE id=?",
            ("pending" if assertion_status == "confirmed" else "inactive", row["id"]),
        )
    if row is not None and (meeting_ids or file_ids):
        add_scopes(
            conn,
            kind="memory",
            owner_id=int(row["id"]),
            meeting_ids=meeting_ids,
            file_ids=file_ids,
        )
    if row is not None:
        _record_memory_version(conn, int(row["id"]))

    write_memory_audit(
        conn,
        user_id=user_id,
        memory_key=key,
        action="upsert",
        new_value=value,
        detail=f"source={source} importance={importance}",
    )


def get_memory(conn: sqlite3.Connection, *, user_id: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    return row["value"] if row else None


def get_memory_full(conn: sqlite3.Connection, *, user_id: str, key: str) -> dict | None:
    """Get full memory record including importance, category, etc.

    The returned dict includes ``meeting_ids`` / ``file_ids`` as legacy CSV
    strings sourced from the ``memory_scopes`` junction table.
    """
    row = conn.execute(
        "SELECT " + _MEMORY_BASE_COLS_M + ", " + _MEMORY_SCOPE_COLS_M + " "
        "FROM user_memories m WHERE m.user_id=? AND m.key=?",
        (user_id, key),
    ).fetchone()
    return dict(row) if row else None


def get_memories_batch(
    conn: sqlite3.Connection, *, user_id: str, keys: list[str]
) -> dict[str, dict]:
    """Fetch multiple memory records in a single SELECT.

    Returns a ``{key: row_dict}`` mapping.  Missing keys are silently
    omitted from the result.
    """
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        "SELECT " + _MEMORY_BASE_COLS_M + ", " + _MEMORY_SCOPE_COLS_M + " FROM user_memories m "
        "WHERE m.user_id=? AND m.key IN (" + placeholders + ")",
        (user_id, *keys),
    ).fetchall()
    return {row["key"]: dict(row) for row in rows}


def list_memory_keys_for_scope(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    include_unscoped: bool = False,
    project_ids: tuple[str, ...] = (),
    exclude_reference: bool = False,
    action_constraints: ActionConstraints | None = None,
    filters=None,
) -> list[str]:
    """Preselect recall-eligible keys before vector search.

    This turns meeting/file scope into an authoritative SQL allow-list rather
    than retrieving across the whole user collection and discarding results
    afterwards.  Profile memories remain globally eligible by design.
    """
    predicate, scope_params = memory_scope_sql(meeting_ids, file_ids)
    clauses = [predicate]
    params: list[Any] = [user_id, *scope_params]
    clauses.append("LOWER(COALESCE(m.category, '')) IN ('profile', 'user_profile')")
    if include_unscoped:
        clauses.append(
            "(m.is_legacy_scope=0 AND NOT EXISTS "
            "(SELECT 1 FROM memory_scopes s WHERE s.memory_id=m.id))"
        )
    extra = []
    if filters is not None:
        filter_clauses, filter_values = filters.sql()
        extra.extend(filter_clauses)
        params.extend(filter_values)
    if project_ids:
        extra.append(f"m.project_id IN ({','.join('?' for _ in project_ids)})")
        params.extend(project_ids)
    if exclude_reference:
        from ..memory_admission import reference_memory_sql

        extra.append("NOT " + reference_memory_sql())
    if action_constraints:
        for values, operator in (
            (action_constraints.included, "IN"),
            (action_constraints.excluded, "NOT IN"),
        ):
            if values:
                extra.append(
                    "(m.fact_type!='action_item' OR COALESCE(m.action_status,'open') "
                    f"{operator} ({','.join('?' for _ in values)}))"
                )
                params.extend(values)
        if action_constraints.overdue:
            extra.append("(m.fact_type!='action_item' OR julianday(m.due_at)<julianday('now'))")
    rows = conn.execute(
        "SELECT DISTINCT m.key FROM user_memories m WHERE m.user_id=? "
        "AND m.assertion_status='confirmed' AND m.superseded_by IS NULL AND m.archived_at IS NULL "
        "AND (m.expires_at IS NULL OR julianday(m.expires_at) > julianday('now')) "
        "AND (m.valid_from IS NULL OR julianday(m.valid_from) <= julianday('now')) "
        "AND (m.valid_to IS NULL OR julianday(m.valid_to) > julianday('now')) "
        "AND ("
        + " OR ".join(clauses)
        + ")"
        + (" AND " + " AND ".join(extra) if extra else "")
        + " ORDER BY m.salience DESC,m.key",
        params,
    ).fetchall()
    return [str(row["key"]) for row in rows]


def search_structured_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    fact_types: list[str],
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    include_unscoped: bool = False,
    query_text: str | None = None,
    project_id: str | None = None,
    project_ids: tuple[str, ...] | None = None,
    as_of: str | None = None,
    known_at: str | None = None,
    limit: int | None = 50,
    action_constraints: ActionConstraints | None = None,
    offset: int = 0,
    assignee: str | None = None,
) -> tuple[list[dict], int]:
    """Return typed facts using indexed SQL predicates and exact scope.

    When the query mentions a known project identifier, results are narrowed
    to that project before limiting. ``as_of`` applies business-validity time;
    ``known_at`` applies system-recording time. Supplying both provides a
    bitemporal snapshot: what was valid at one instant using only information
    the system had recorded by another instant.
    """
    if not fact_types:
        return [], 0
    placeholders = ",".join("?" for _ in fact_types)
    cutoff = as_of or "now"
    clauses = [
        "m.user_id=?",
        f"m.fact_type IN ({placeholders})",
        "m.assertion_status='confirmed'",
        "m.superseded_by IS NULL",
        "(m.expires_at IS NULL OR julianday(m.expires_at) > julianday(?))",
        "(m.valid_from IS NULL OR julianday(m.valid_from) <= julianday(?))",
        "(m.valid_to IS NULL OR julianday(m.valid_to) > julianday(?))",
    ]
    params: list[Any] = [user_id, *fact_types, cutoff, cutoff, cutoff]

    scope_predicate, scope_params = memory_scope_sql(
        meeting_ids, file_ids, include_unscoped=include_unscoped
    )
    clauses.append(scope_predicate)
    params.extend(scope_params)

    from ..project_resolution import resolve_project_ids

    resolved_projects = (project_id,) if project_id else project_ids
    if resolved_projects is None:
        resolved_projects = resolve_project_ids(conn, user_id, query_text or "")
    if resolved_projects:
        clauses.append(f"m.project_id IN ({','.join('?' for _ in resolved_projects)})")
        params.extend(resolved_projects)

    if assignee is not None:
        clauses.append("m.assignee=? COLLATE NOCASE")
        params.append(assignee)

    folded_query = (query_text or "").casefold()
    constraints = action_constraints or parse_action_constraints(folded_query)
    if as_of is not None or known_at is not None:
        return _search_structured_memory_versions(
            conn,
            user_id=user_id,
            fact_types=fact_types,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
            include_unscoped=include_unscoped,
            project_ids=resolved_projects,
            folded_query=folded_query,
            as_of=as_of or known_at or "now",
            known_at=known_at,
            limit=limit,
            action_constraints=constraints,
            offset=offset,
            assignee=assignee,
        )
    if "action_item" in fact_types:
        for values, operator in ((constraints.included, "IN"), (constraints.excluded, "NOT IN")):
            if values:
                clauses.append(
                    "(m.fact_type!='action_item' OR COALESCE(m.action_status,'open') "
                    f"{operator} ({','.join('?' for _ in values)}))"
                )
                params.extend(values)
        if constraints.overdue:
            clauses.append(
                "(m.fact_type!='action_item' OR (m.due_at IS NOT NULL "
                "AND julianday(m.due_at) < julianday(?)))"
            )
            params.append(cutoff)

    where = " AND ".join(clauses)
    total_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM user_memories m WHERE {where}", params
    ).fetchone()
    query = (
        "SELECT " + _MEMORY_BASE_COLS_M + ", " + _MEMORY_SCOPE_COLS_M + " "
        f"FROM user_memories m WHERE {where} "
        "ORDER BY m.salience DESC, COALESCE(m.last_confirmed_at, m.updated_at) DESC, m.key"
    )
    query_params = list(params)
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        query_params.extend([limit, max(0, offset)])
    rows = conn.execute(query, query_params).fetchall()
    return [dict(row) for row in rows], int(total_row["n"] if total_row else 0)


def _search_structured_memory_versions(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    fact_types: list[str],
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    include_unscoped: bool,
    project_ids: tuple[str, ...],
    folded_query: str,
    as_of: str,
    known_at: str | None,
    limit: int | None,
    action_constraints: ActionConstraints | None = None,
    offset: int = 0,
    assignee: str | None = None,
) -> tuple[list[dict], int]:
    """Select authoritative versions in SQL before applying business filters."""
    eligibility = [
        "user_id=?",
        "(valid_from IS NULL OR julianday(valid_from)<=julianday(?))",
        "(valid_to IS NULL OR ((julianday(?)<julianday(valid_to) "
        "OR (? IS NOT NULL AND julianday(?)<julianday(recorded_to))) "
        "AND NOT (assertion_status IN ('retracted','superseded') "
        "AND julianday(?)<julianday(valid_to))))",
    ]
    params: list[Any] = [user_id, as_of, as_of, known_at, known_at, as_of]
    if known_at is not None:
        eligibility.append("julianday(recorded_at)<=julianday(?)")
        params.append(known_at)
    filters = [
        "m.rn=1",
        "m.assertion_status='confirmed'",
        f"m.fact_type IN ({','.join('?' for _ in fact_types)})",
    ]
    params.extend(fact_types)
    if project_ids:
        filters.append(f"m.project_id IN ({','.join('?' for _ in project_ids)})")
        params.extend(project_ids)
    if assignee is not None:
        filters.append("m.assignee=? COLLATE NOCASE")
        params.append(assignee)
    scope_parts = []
    scope_params = []
    for column, ids in (("meeting_ids", meeting_ids), ("file_ids", file_ids)):
        if ids:
            scope_parts.append(
                "("
                + " OR ".join(
                    f"instr(','||COALESCE(m.{column},'')||',',','||?||',')>0" for _ in ids
                )
                + ")"
            )
            scope_params.extend(ids)
    if scope_parts:
        scope = "(" + " AND ".join(scope_parts) + ")"
        if include_unscoped:
            scope = (
                "(" + scope + " OR (COALESCE(m.meeting_ids,'')='' AND COALESCE(m.file_ids,'')=''))"
            )
        filters.append(scope)
        params.extend(scope_params)
    constraints = action_constraints or parse_action_constraints(folded_query)
    for values, operator in ((constraints.included, "IN"), (constraints.excluded, "NOT IN")):
        if values:
            filters.append(
                "(m.fact_type!='action_item' OR COALESCE(m.action_status,'open') "
                f"{operator} ({','.join('?' for _ in values)}))"
            )
            params.extend(values)
    if constraints.overdue:
        filters.append(
            "(m.fact_type!='action_item' OR "
            "(COALESCE(m.action_status,'open') NOT IN ('done','cancelled') "
            "AND julianday(m.due_at)<julianday(?)))"
        )
        params.append(as_of)
    cte = (
        "WITH versions AS (SELECT *, ROW_NUMBER() OVER "
        "(PARTITION BY memory_key ORDER BY revision DESC,recorded_at DESC) AS rn "
        "FROM memory_fact_versions WHERE " + " AND ".join(eligibility) + "), "
        "selected AS (SELECT * FROM versions m WHERE " + " AND ".join(filters) + ") "
    )
    query = (
        cte + "SELECT *,COUNT(*) OVER() AS result_total FROM selected "
        "ORDER BY recorded_at DESC,memory_key DESC"
    )
    page_params = list(params)
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        page_params.extend((limit, max(0, offset)))
    elif offset:
        query += " LIMIT -1 OFFSET ?"
        page_params.append(max(0, offset))
    matches = []
    total = 0
    for raw in conn.execute(query, page_params):
        row = dict(raw)
        total = row.pop("result_total")
        row.pop("rn", None)
        row.update(
            id=row["memory_id"],
            key=row["memory_key"],
            importance=3.0,
            salience=3.0,
            updated_at=row["recorded_at"],
            last_confirmed_at=row["recorded_at"],
        )
        matches.append(row)
    if not matches:
        total = conn.execute(cte + "SELECT COUNT(*) FROM selected", params).fetchone()[0]
    return matches, int(total)


def _build_memory_where(
    user_id: str,
    category: str | None = None,
    include_expired: bool = False,
    text_query: str | None = None,
    fact_type: str | None = None,
    assertion_status: str | None = None,
    project_id: str | None = None,
    memory_kind: str = "all",
) -> tuple[str, list[Any]]:
    """Build the WHERE clause shared by list and count queries."""
    clauses = ["m.user_id=?"]
    params: list[Any] = [user_id]
    if memory_kind in {"personal", "reference"}:
        from ..memory_admission import reference_memory_sql

        clauses.append(("" if memory_kind == "reference" else "NOT ") + reference_memory_sql())
    if category:
        clauses.append("m.category=?")
        params.append(category)
    if fact_type:
        clauses.append("m.fact_type=?")
        params.append(fact_type)
    if assertion_status:
        clauses.append("m.assertion_status=?")
        params.append(assertion_status)
    if project_id:
        clauses.append("m.project_id=?")
        params.append(project_id)
    if not include_expired:
        clauses.append("(m.expires_at IS NULL OR m.expires_at > CURRENT_TIMESTAMP)")
    if text_query:
        escaped = text_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(m.key LIKE ? ESCAPE '\\' OR m.value LIKE ? ESCAPE '\\')")
        pattern = f"%{escaped}%"
        params.extend([pattern, pattern])
    return " AND ".join(clauses), params


def list_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
    limit: int | None = 100,
    offset: int = 0,
    after: tuple[float, str, str] | None = None,
    text_query: str | None = None,
    fact_type: str | None = None,
    assertion_status: str | None = None,
    project_id: str | None = None,
    memory_kind: str = "all",
) -> list[dict]:
    """List memories for a user, optionally filtering by category and excluding expired."""
    cols = (
        "m.id, m.archived_at, m.archive_reason, m.key, m.value, m.source, "
        "m.created_at, m.importance, m.category, m.last_accessed, "
        "m.access_count, m.expires_at, m.updated_at, m.relevance_score, m.superseded_by, "
        "m.embedding_id, m.session_id, m.is_legacy_scope, m.salience, m.confidence, "
        "m.freshness_score, m.usefulness_score, m.usefulness_count, m.last_confirmed_at, "
        "m.valid_from, m.valid_to, m.evidence_message_ids, m.evidence_excerpt, m.evidence_refs, "
        "m.conflicts_with, m.vector_state, m.revision, "
        + _MEMORY_SCOPE_COLS_M
        + ", m.fact_type, m.assertion_status, m.project_id, m.subject, m.predicate, "
        "m.object_value, m.retracted_at, m.action_status, m.assignee, m.due_at"
    )
    where, params = _build_memory_where(
        user_id,
        category,
        include_expired,
        text_query,
        fact_type,
        assertion_status,
        project_id,
        memory_kind,
    )
    if after is not None:
        salience, updated_at, key = after
        where += (
            " AND (m.salience < ? OR (m.salience = ? AND m.updated_at < ?) "
            "OR (m.salience = ? AND m.updated_at = ? AND m.key > ?))"
        )
        params.extend([salience, salience, updated_at, salience, updated_at, key])
    query = (
        "SELECT " + cols + " FROM user_memories m WHERE " + where + " "
        "ORDER BY m.salience DESC, m.updated_at DESC, m.key ASC"
    )
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if after is None:
            query += " OFFSET ?"
            params.append(offset)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def count_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
    text_query: str | None = None,
    fact_type: str | None = None,
    assertion_status: str | None = None,
    project_id: str | None = None,
    memory_kind: str = "all",
) -> int:
    """Count memories for a user, optionally filtering by category and excluding expired."""
    where, params = _build_memory_where(
        user_id,
        category,
        include_expired,
        text_query,
        fact_type,
        assertion_status,
        project_id,
        memory_kind,
    )
    query = "SELECT COUNT(*) FROM user_memories m WHERE " + where
    row = conn.execute(query, params).fetchone()
    return row[0]


def list_and_count_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
    after: tuple[float, str, str] | None = None,
    text_query: str | None = None,
    fact_type: str | None = None,
    assertion_status: str | None = None,
    project_id: str | None = None,
    memory_kind: str = "all",
) -> tuple[list[dict], int]:
    """List memories with total count in a single connection use.

    Avoids the extra round-trip of calling list_memories + count_memories
    separately (merges two connections into one).
    """
    items = list_memories(
        conn,
        user_id=user_id,
        include_expired=include_expired,
        category=category,
        limit=limit,
        offset=offset,
        after=after,
        text_query=text_query,
        fact_type=fact_type,
        assertion_status=assertion_status,
        project_id=project_id,
        memory_kind=memory_kind,
    )
    total = count_memories(
        conn,
        user_id=user_id,
        include_expired=include_expired,
        category=category,
        text_query=text_query,
        fact_type=fact_type,
        assertion_status=assertion_status,
        project_id=project_id,
        memory_kind=memory_kind,
    )
    return items, total


def delete_memory(conn: sqlite3.Connection, *, user_id: str, key: str) -> str | None:
    """Delete memory and return its embedding_id for Chroma cleanup."""
    mem = get_memory_full(conn, user_id=user_id, key=key)
    conn.execute(
        "DELETE FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    )
    if mem:
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=key,
            action="delete",
            old_value=mem.get("value"),
            detail="hard_delete",
        )
    return mem.get("embedding_id") if mem else None


def touch_memory_access(conn: sqlite3.Connection, *, user_id: str, key: str) -> None:
    """Record memory access for importance decay tracking."""
    conn.execute(
        """UPDATE user_memories
           SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
           WHERE user_id=? AND key=?""",
        (user_id, key),
    )


def get_expired_memory_ids(conn: sqlite3.Connection) -> list[dict]:
    """Get embedding_id and key for all expired memories (before deletion)."""
    rows = conn.execute(
        "SELECT embedding_id, user_id, key FROM user_memories "
        "WHERE archived_at IS NULL AND expires_at IS NOT NULL AND "
        "julianday(expires_at)<=julianday('now')"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_expired_memories(conn: sqlite3.Connection) -> int:
    """Compatibility name: expiry retires recall, preserving historical evidence."""
    from .memory_lifecycle import archive_memories

    rows = conn.execute(
        "SELECT id,embedding_id FROM user_memories WHERE archived_at IS NULL "
        "AND expires_at IS NOT NULL AND julianday(expires_at)<=julianday('now')"
    ).fetchall()
    return archive_memories(conn, rows, reason="expired")


def search_memories_by_importance(
    conn: sqlite3.Connection, *, user_id: str, min_importance: float = 1, limit: int = 10
) -> list[dict]:
    """Get top active (non-superseded) memories by importance score."""
    rows = conn.execute(
        "SELECT m.key, m.value, m.source, m.importance, m.salience, m.confidence, "
        "m.freshness_score, m.usefulness_score, m.usefulness_count, m.last_confirmed_at, "
        "m.valid_from, m.valid_to, m.evidence_message_ids, m.evidence_excerpt, m.evidence_refs, "
        "m.conflicts_with, m.category, m.last_accessed, "
        "m.access_count, m.expires_at, m.updated_at, m.relevance_score, "
        "m.superseded_by, m.is_legacy_scope, m.fact_type, m.assertion_status, "
        "m.project_id, m.subject, m.predicate, m.object_value, m.retracted_at, "
        + _MEMORY_SCOPE_COLS_M
        + " "
        "FROM user_memories m "
        "WHERE m.user_id=? AND m.salience >= ? AND m.superseded_by IS NULL "
        "AND (m.expires_at IS NULL OR m.expires_at > CURRENT_TIMESTAMP) "
        "AND (m.valid_from IS NULL OR julianday(m.valid_from) <= julianday('now')) "
        "AND (m.valid_to IS NULL OR julianday(m.valid_to) > julianday('now')) "
        "ORDER BY m.salience DESC, m.relevance_score DESC, "
        "m.freshness_score DESC, m.last_confirmed_at DESC "
        "LIMIT ?",
        (user_id, min_importance, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_memory_superseded(
    conn: sqlite3.Connection, *, user_id: str, key: str, superseded_by: str
) -> None:
    """Mark a memory as superseded by a newer/consolidated memory."""
    current = conn.execute(
        "SELECT id,revision FROM user_memories WHERE user_id=? AND key=?", (user_id, key)
    ).fetchone()
    if current is not None:
        transition_at = _database_now(conn)
        conn.execute(
            "UPDATE user_memories SET valid_to=COALESCE(valid_to, ?) WHERE id=?",
            (transition_at, int(current["id"])),
        )
        _close_recorded_validity(
            conn,
            memory_id=int(current["id"]),
            revision=int(current["revision"]),
            valid_to=transition_at,
        )
        _record_memory_version(conn, int(current["id"]))
    conn.execute(
        (
            "UPDATE user_memories SET superseded_by=?, "
            "assertion_status='superseded', vector_state='inactive', revision=revision+1, "
            "valid_to=COALESCE(valid_to, CURRENT_TIMESTAMP), "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (superseded_by, user_id, key),
    )
    if current is not None:
        _record_memory_version(conn, int(current["id"]))
    write_memory_audit(
        conn,
        user_id=user_id,
        memory_key=key,
        action="supersede",
        detail=f"superseded_by={superseded_by}",
    )


def resolve_memory_conflict(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    winner_key: str,
    expected_revision: int,
    conflicting_keys: list[str],
    expected_conflict_revisions: dict[str, int] | None = None,
) -> list[str]:
    """Atomically confirm one assertion and supersede its declared conflicts."""
    winner = get_memory_full(conn, user_id=user_id, key=winner_key)
    if winner is None or int(winner["revision"]) != expected_revision:
        raise MemoryRevisionConflictError(
            f"winner {winner_key!r} no longer has revision {expected_revision}"
        )
    declared = set(json.loads(winner["conflicts_with"] or "[]"))
    requested = set(conflicting_keys)
    if not requested or requested != declared:
        raise ValueError(
            "conflicting_keys must exactly match every conflict declared by the winning assertion"
        )

    losers: list[dict] = []
    for key in conflicting_keys:
        loser = get_memory_full(conn, user_id=user_id, key=key)
        if loser is None or loser["assertion_status"] in {"retracted", "superseded"}:
            raise MemoryRevisionConflictError(f"conflicting assertion {key!r} is no longer active")
        # Structured identities may differ for legacy rows, but an explicit
        # project on both sides must never cross workspace/project boundaries.
        if (
            winner.get("project_id")
            and loser.get("project_id")
            and winner["project_id"] != loser["project_id"]
        ):
            raise ValueError("cannot resolve conflicts across different projects")
        if (
            expected_conflict_revisions is not None
            and expected_conflict_revisions.get(key) != loser["revision"]
        ):
            raise MemoryRevisionConflictError(f"conflicting assertion {key!r} changed after review")
        losers.append(loser)

    if not update_memory(
        conn,
        user_id=user_id,
        key=winner_key,
        expected_revision=expected_revision,
        assertion_status="confirmed",
        fields={"assertion_status"},
    ):
        raise MemoryRevisionConflictError(f"winner {winner_key!r} changed concurrently")
    current = get_memory_full(conn, user_id=user_id, key=winner_key)
    if current is None:
        raise MemoryRevisionConflictError(f"winner {winner_key!r} disappeared")
    conn.execute(
        "UPDATE user_memories SET conflicts_with=NULL WHERE id=?",
        (current["id"],),
    )
    conn.execute(
        "UPDATE memory_fact_versions SET conflicts_with=NULL WHERE memory_id=? AND revision=?",
        (current["id"], current["revision"]),
    )
    for loser in losers:
        mark_memory_superseded(
            conn,
            user_id=user_id,
            key=str(loser["key"]),
            superseded_by=winner_key,
        )
    write_memory_audit(
        conn,
        user_id=user_id,
        memory_key=winner_key,
        action="resolve_conflict",
        detail="superseded=" + ",".join(conflicting_keys),
    )
    return conflicting_keys


def detach_memory_file_evidence(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    file_id: int,
) -> list[str]:
    """Detach a deleted source file and retract facts with no remaining source file."""
    rows = conn.execute(
        "SELECT m.* FROM user_memories m JOIN memory_scopes s ON s.memory_id=m.id "
        "WHERE m.user_id=? AND s.scope_type='file' AND s.scope_id=?",
        (user_id, file_id),
    ).fetchall()
    changed: list[str] = []
    for raw in rows:
        row = dict(raw)
        memory_id = int(row["id"])
        revision = int(row["revision"])
        _record_memory_version(conn, memory_id)
        conn.execute(
            "DELETE FROM memory_scopes WHERE memory_id=? AND scope_type='file' AND scope_id=?",
            (memory_id, file_id),
        )
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM memory_scopes WHERE memory_id=? AND scope_type='file'",
            (memory_id,),
        ).fetchone()
        try:
            refs = json.loads(row.get("evidence_refs") or "[]")
        except (TypeError, json.JSONDecodeError):
            refs = []
        refs = [
            ref
            for ref in refs
            if not isinstance(ref, dict) or str(ref.get("file_id")) != str(file_id)
        ]
        retract = (
            int(remaining["n"] if remaining else 0) == 0 and row.get("source") == "auto_extracted"
        )
        transition_at = _database_now(conn) if retract else None
        conn.execute(
            "UPDATE user_memories SET evidence_refs=?, revision=revision+1, "
            "assertion_status=CASE WHEN ? THEN 'retracted' ELSE assertion_status END, "
            "retracted_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE retracted_at END, "
            "valid_to=CASE WHEN ? THEN COALESCE(valid_to, ?) ELSE valid_to END, "
            "vector_state=CASE WHEN ? THEN 'inactive' "
            "WHEN assertion_status='confirmed' THEN 'pending' ELSE 'inactive' END, "
            "vector_attempts=0, vector_retry_at=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?",
            (
                json.dumps(refs, ensure_ascii=False) if refs else None,
                retract,
                retract,
                retract,
                transition_at,
                retract,
                memory_id,
                revision,
            ),
        )
        _record_memory_version(conn, memory_id)
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=str(row["key"]),
            action="source_file_deleted",
            detail=f"file_id={file_id}; retracted={retract}",
        )
        changed.append(str(row["key"]))
    return changed


def retract_memories_with_only_rejected_file_evidence(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    file_id: int,
) -> list[str]:
    """Retract auto-extracted facts whose remaining file evidence is rejected.

    Evidence references and scopes are retained so the review trail remains
    inspectable and a later approved re-extraction can reopen the same fact.
    """
    rows = conn.execute(
        "SELECT DISTINCT m.id, m.key, m.revision FROM user_memories m "
        "JOIN memory_scopes target ON target.memory_id=m.id "
        "WHERE m.user_id=? AND m.source='auto_extracted' "
        "AND m.assertion_status NOT IN ('retracted','superseded') "
        "AND target.scope_type='file' AND target.scope_id=?",
        (user_id, file_id),
    ).fetchall()
    changed: list[str] = []
    for row in rows:
        memory_id = int(row["id"])
        active_support = conn.execute(
            "SELECT 1 FROM memory_scopes s JOIN meeting_files f ON f.id=s.scope_id "
            "WHERE s.memory_id=? AND s.scope_type='file' "
            "AND COALESCE(f.approval_status, 'unreviewed')!='rejected' LIMIT 1",
            (memory_id,),
        ).fetchone()
        if active_support is not None:
            continue
        _record_memory_version(conn, memory_id)
        transition_at = _database_now(conn)
        updated = conn.execute(
            "UPDATE user_memories SET assertion_status='retracted', "
            "retracted_at=CURRENT_TIMESTAMP, valid_to=COALESCE(valid_to, ?), "
            "revision=revision+1, vector_state='inactive', vector_attempts=0, "
            "vector_retry_at=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?",
            (transition_at, memory_id, int(row["revision"])),
        ).rowcount
        if updated <= 0:
            continue
        _close_recorded_validity(
            conn,
            memory_id=memory_id,
            revision=int(row["revision"]),
            valid_to=transition_at,
        )
        _record_memory_version(conn, memory_id)
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=str(row["key"]),
            action="source_file_rejected",
            detail=f"file_id={file_id}",
        )
        changed.append(str(row["key"]))
    return changed


def get_memory_timeline(
    conn: sqlite3.Connection, *, user_id: str, key: str, max_depth: int = 20
) -> list[dict]:
    """Walk the supersede chain for a memory, oldest → newest.

    Follows ``superseded_by`` forward from the supplied key and backward from
    any row whose ``superseded_by`` points to it, then returns the chain
    sorted by ``updated_at``. Useful for answering "what did the user use
    before / what changed" without polluting active recall.

    Includes entries filtered out of normal queries (superseded rows retained
    in the table by design).
    """
    visited: set[str] = set()
    to_visit: list[str] = [key]
    collected: list[dict] = []
    while to_visit and len(visited) < max_depth:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        row = conn.execute(
            "SELECT id, user_id, key, value, source, created_at, updated_at, "
            "importance, salience, confidence, freshness_score, usefulness_score, "
            "usefulness_count, last_confirmed_at, valid_from, valid_to, "
            "evidence_message_ids, evidence_excerpt, evidence_refs, conflicts_with, "
            "expires_at, last_accessed, access_count, category, "
            "embedding_id, session_id, turn_index, superseded_by, "
            "relevance_score, is_legacy_scope "
            "FROM user_memories WHERE user_id=? AND key=?",
            (user_id, current),
        ).fetchone()
        if not row:
            continue
        collected.append(dict(row))
        # Follow forward pointer
        if row["superseded_by"] and row["superseded_by"] not in visited:
            to_visit.append(row["superseded_by"])
        # Follow reverse pointers: anything that superseded this key
        rev = conn.execute(
            "SELECT key FROM user_memories WHERE user_id=? AND superseded_by=?",
            (user_id, current),
        ).fetchall()
        for rev_row in rev:
            if rev_row["key"] not in visited:
                to_visit.append(rev_row["key"])

    collected.sort(key=lambda r: r.get("updated_at") or "")
    return collected


def update_memory_relevance_score(
    conn: sqlite3.Connection, *, user_id: str, key: str, relevance_score: float
) -> None:
    """Update the float relevance score used for continuous decay."""
    conn.execute(
        (
            "UPDATE user_memories SET relevance_score=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (relevance_score, user_id, key),
    )


def update_memory_importance(
    conn: sqlite3.Connection, *, user_id: str, key: str, importance: float
) -> None:
    """Update the compatibility importance field and canonical salience."""
    conn.execute(
        (
            "UPDATE user_memories SET importance=?, salience=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (importance, importance, user_id, key),
    )


def record_memory_usefulness(
    conn: sqlite3.Connection, *, user_id: str, key: str, useful: bool
) -> dict[str, float | int] | None:
    """Update and return the authoritative usefulness signal for one fact."""
    cursor = conn.execute(
        """UPDATE user_memories SET
               usefulness_score=(usefulness_score * usefulness_count + ?) /
                   (usefulness_count + 1),
               usefulness_count=usefulness_count + 1,
               updated_at=CURRENT_TIMESTAMP
           WHERE user_id=? AND key=?""",
        (1.0 if useful else 0.0, user_id, key),
    )
    if cursor.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT usefulness_score, usefulness_count FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    if row is None:
        return None
    return {
        "usefulness_score": float(row["usefulness_score"]),
        "usefulness_count": int(row["usefulness_count"]),
    }


def update_memory(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    key: str,
    expected_revision: int,
    value: str | None = None,
    importance: float | None = None,
    category: str | None = None,
    confidence: float | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    fact_type: str | None = None,
    assertion_status: str | None = None,
    project_id: str | None = None,
    action_status: str | None = None,
    assignee: str | None = None,
    due_at: str | None = None,
    fields: set[str] | None = None,
) -> bool:
    """CAS-update fields; False means missing or concurrently modified."""
    current = conn.execute(
        "SELECT "
        "id,value,assertion_status,revision,action_status,assignee,due_at,project_id "
        "FROM user_memories "
        "WHERE user_id=? AND key=? AND revision=?",
        (user_id, key, expected_revision),
    ).fetchone()
    if current is None:
        return False
    value_changed = value is not None and value != current["value"]
    for name, next_value in (
        ("action_status", action_status),
        ("assignee", assignee),
        ("due_at", due_at),
        ("project_id", project_id),
    ):
        supplied_field = name in fields if fields is not None else next_value is not None
        value_changed = value_changed or (supplied_field and next_value != current[name])
    status_opened = bool(
        assertion_status == "confirmed" and current["assertion_status"] != "confirmed"
    )
    status_closed = bool(
        assertion_status is not None
        and assertion_status != "confirmed"
        and current["assertion_status"] == "confirmed"
    )
    transition_at = (
        (valid_from or _database_now(conn))
        if value_changed or status_opened or status_closed
        else None
    )

    updates: list[str] = []
    params: list[Any] = []

    def supplied(name: str, value: Any) -> bool:
        return name in fields if fields is not None else value is not None

    if value is not None:
        updates.append("value=?")
        params.append(value)
    if importance is not None:
        updates.extend(("importance=?", "salience=?"))
        params.extend((importance, importance))
    if supplied("category", category):
        updates.append("category=?")
        params.append(category)
    if confidence is not None:
        updates.append("confidence=?")
        params.append(max(0.0, min(1.0, float(confidence))))
    if supplied("valid_from", valid_from) or value_changed or status_opened:
        updates.append("valid_from=?")
        params.append(valid_from or transition_at)
    if supplied("valid_to", valid_to) or value_changed or status_opened or status_closed:
        updates.append("valid_to=?")
        params.append(valid_to or (transition_at if status_closed else None))
    if fact_type is not None:
        updates.append("fact_type=?")
        params.append(fact_type)
    if assertion_status is not None:
        updates.append("assertion_status=?")
        params.append(assertion_status)
        updates.append("retracted_at=CASE WHEN ?='retracted' THEN CURRENT_TIMESTAMP ELSE NULL END")
        params.append(assertion_status)
    if supplied("project_id", project_id):
        updates.append("project_id=?")
        params.append(project_id)
    if supplied("action_status", action_status):
        updates.append("action_status=?")
        params.append(action_status)
    if supplied("assignee", assignee):
        updates.append("assignee=?")
        params.append(assignee)
    if supplied("due_at", due_at):
        updates.append("due_at=?")
        params.append(due_at)
    if not updates:
        return False
    for part in updates:
        col = part.split("=")[0]
        if not col.isidentifier():
            raise ValueError(f"Invalid column name: {col}")
    updates.extend(("source='manual'", "archived_at=NULL", "archive_reason=NULL"))
    updates.append("updated_at=CURRENT_TIMESTAMP")
    updates.append("revision=revision+1")
    if assertion_status is not None:
        # SQLite evaluates every SET expression from the pre-update row, so a
        # CASE on assertion_status here would see the old lifecycle value.
        updates.append("vector_state=?")
        params.append("pending" if assertion_status == "confirmed" else "inactive")
    else:
        updates.append(
            "vector_state=CASE WHEN assertion_status='confirmed' THEN 'pending' ELSE 'inactive' END"
        )
    updates.extend(("vector_attempts=0", "vector_retry_at=NULL"))
    params.extend([user_id, key, expected_revision])
    if value_changed or status_closed:
        conn.execute(
            "UPDATE user_memories SET valid_to=COALESCE(valid_to, ?) WHERE id=?",
            (transition_at, int(current["id"])),
        )
        _close_recorded_validity(
            conn,
            memory_id=int(current["id"]),
            revision=int(current["revision"]),
            valid_to=str(transition_at),
        )
    _record_memory_version(conn, int(current["id"]))
    cursor = conn.execute(
        "UPDATE user_memories SET "
        + ", ".join(updates)
        + " WHERE user_id=? AND key=? AND revision=?",
        params,
    )
    if cursor.rowcount > 0:
        _record_memory_version(conn, int(current["id"]))
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=key,
            action="lifecycle_update" if assertion_status else "update",
            detail=(f"assertion_status={assertion_status}" if assertion_status else None),
        )
    return cursor.rowcount > 0


def list_memory_versions(
    conn: sqlite3.Connection, *, user_id: str, key: str, limit: int = 100
) -> list[dict]:
    """Return immutable snapshots for one fact, newest first."""
    rows = conn.execute(
        """SELECT revision, value, source, fact_type, assertion_status, project_id,
                  subject, predicate, object_value, action_status, assignee, due_at,
                  category, confidence,
                  valid_from, valid_to, evidence_message_ids, evidence_excerpt, evidence_refs,
                  conflicts_with, meeting_ids, file_ids, recorded_at, recorded_to
             FROM memory_fact_versions
            WHERE user_id=? AND memory_key=?
            ORDER BY revision DESC LIMIT ?""",
        (user_id, key, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def retract_memories_from_session(
    conn: sqlite3.Connection, *, user_id: str, session_id: str
) -> list[str]:
    """Retract all active facts derived from a deleted conversation."""
    rows = conn.execute(
        "SELECT key, revision FROM user_memories WHERE user_id=? AND session_id=? "
        "AND assertion_status NOT IN ('retracted', 'superseded')",
        (user_id, session_id),
    ).fetchall()
    retracted: list[str] = []
    for row in rows:
        if update_memory(
            conn,
            user_id=user_id,
            key=str(row["key"]),
            expected_revision=int(row["revision"]),
            assertion_status="retracted",
        ):
            retracted.append(str(row["key"]))
    return retracted


def get_memories_for_consolidation(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    category: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get active memories grouped for consolidation analysis."""
    base = (
        "SELECT m.key, m.value, m.source, m.importance, m.salience, m.confidence, "
        "m.freshness_score, m.usefulness_score, m.usefulness_count, m.last_confirmed_at, "
        "m.valid_from, m.valid_to, m.evidence_message_ids, m.evidence_excerpt, m.evidence_refs, "
        "m.conflicts_with, m.category, m.last_accessed, "
        "m.access_count, m.expires_at, m.updated_at, m.embedding_id, "
        + _MEMORY_SCOPE_COLS_M
        + " FROM user_memories m WHERE m.user_id=? AND m.superseded_by IS NULL "
        "AND (m.expires_at IS NULL OR m.expires_at > CURRENT_TIMESTAMP) "
    )
    params: list = [user_id]
    if category:
        base += " AND m.category=?"
        params.append(category)
    base += " ORDER BY m.salience DESC, m.last_confirmed_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]
