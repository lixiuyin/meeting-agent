"""Resolve explicit project mentions without silently widening multi-project queries."""

import json
import re
import sqlite3


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").replace("-", " ").split())


def resolve_project_ids(conn: sqlite3.Connection, user_id: str, query: str) -> tuple[str, ...]:
    projects = [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT project_id FROM user_memories "
            "WHERE user_id=? AND project_id IS NOT NULL AND project_id!=''",
            (user_id,),
        )
    ]
    names: dict[str, set[str]] = {}
    for project in projects:
        names.setdefault(_normalize(project), set()).add(project)
    for project_id, name, aliases_json in conn.execute(
        "SELECT project_id,name,aliases FROM projects WHERE user_id=?", (user_id,)
    ):
        for alias in [project_id, name, *json.loads(aliases_json)]:
            if alias.strip():
                names.setdefault(_normalize(alias), set()).add(project_id)
    # Reuse user-curated entity aliases only when their canonical name identifies
    # a project. A person/company alias must never turn into a project constraint.
    for row in conn.execute("SELECT name,aliases FROM memory_entities WHERE user_id=?", (user_id,)):
        canonical = names.get(_normalize(str(row[0])))
        if not canonical or not row[1]:
            continue
        try:
            aliases = json.loads(row[1])
        except (ValueError, TypeError):
            continue
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    names.setdefault(_normalize(alias), set()).update(canonical)
    text = _normalize(query)
    found = []
    for name, ids in names.items():
        for match in re.finditer(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", text):
            found.append((match.start(), match.end(), ids))
    # A longer canonical name wins over its prefix at the same mention location.
    selected: set[str] = set()
    for start, end, ids in found:
        if not any(
            left <= start and right >= end and right - left > end - start
            for left, right, _ in found
        ):
            selected.update(ids)
    # Ambiguous aliases yield their explicit candidate set, not all user facts.
    return tuple(sorted(selected))


def resolve_assertion_project(
    conn: sqlite3.Connection, user_id: str, proposed: str | None, quote: str
) -> tuple[str | None, str | None]:
    """Resolve an explicit, unique source mention to its user-owned project ID.

    Return its source label separately: opaque IDs are not literal evidence.
    Ambiguous mentions and unrelated proposed scopes never acquire an identity.
    """
    projects = resolve_project_ids(conn, user_id, quote)
    if len(projects) != 1:
        if not projects and not proposed:
            # An explicit meeting-scope prefix is source data, not a semantic
            # project guess or a grant of access to a project directory entry.
            match = re.match(
                r"\s*(?:in|at|during)\s+(?:the\s+)?"
                r"(?P<label>[A-Za-z][A-Za-z0-9 -]{0,70}\s+(?:Review|Meeting))\s*,",
                quote,
                re.IGNORECASE,
            )
            if match and not re.search(r"\b(?:and|or)\b", match["label"], re.I):
                label = match["label"].strip()
                return _normalize(label).replace(" ", "_"), label
        return proposed, proposed
    project_id = projects[0]
    aliases = [project_id]
    row = conn.execute(
        "SELECT name,aliases FROM projects WHERE user_id=? AND project_id=?",
        (user_id, project_id),
    ).fetchone()
    if row:
        aliases.extend([row[0], *json.loads(row[1])])
    if proposed and _normalize(proposed) not in {_normalize(alias) for alias in aliases}:
        return proposed, proposed
    text = _normalize(quote)
    explicit = [
        alias
        for alias in aliases
        if re.search(r"(?<![a-z0-9])" + re.escape(_normalize(alias)) + r"(?![a-z0-9])", text)
    ]
    if not explicit:
        return proposed, proposed
    return project_id, max(explicit, key=len)
