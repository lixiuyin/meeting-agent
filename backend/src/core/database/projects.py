"""User-owned project/material mappings. A semantic alias never grants access."""

import json


class ProjectConflict(ValueError):
    def __init__(self, current):
        super().__init__("Project changed; review the latest bindings before saving")
        self.current = current


def list_projects(conn, user_id):
    rows = conn.execute(
        "SELECT * FROM projects WHERE user_id=? ORDER BY name,project_id", (user_id,)
    )
    result = []
    for raw in rows:
        row = dict(raw)
        row["aliases"] = json.loads(row["aliases"])
        row["file_ids"] = project_file_ids(conn, user_id, (row["project_id"],))
        result.append(row)
    return result


def project_file_ids(conn, user_id, project_ids):
    if not project_ids:
        return []
    return [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT p.file_id FROM project_files p JOIN meeting_files f ON f.id=p.file_id "
            "JOIN meetings m ON m.id=f.meeting_id WHERE p.user_id=? AND m.user_id=? "
            f"AND p.project_id IN ({','.join('?' for _ in project_ids)}) ORDER BY p.file_id",
            (user_id, user_id, *project_ids),
        )
    ]


def save_project(conn, user_id, project_id, name, aliases, file_ids, *, expected_revision=None):
    ids = sorted(set(file_ids))
    if ids:
        owned = {
            row[0]
            for row in conn.execute(
                "SELECT f.id FROM meeting_files f JOIN meetings m ON m.id=f.meeting_id "
                f"WHERE m.user_id=? AND f.id IN ({','.join('?' for _ in ids)})",
                (user_id, *ids),
            )
        }
        if owned != set(ids):
            raise ValueError("One or more materials are unavailable")
    raw = conn.execute(
        "SELECT * FROM projects WHERE user_id=? AND project_id=?", (user_id, project_id)
    ).fetchone()
    current = dict(raw) if raw else None
    if expected_revision is not None and expected_revision != (
        current["revision"] if current else 0
    ):
        if current:
            current["aliases"] = json.loads(current["aliases"])
            current["file_ids"] = project_file_ids(conn, user_id, (project_id,))
        raise ProjectConflict(current)
    conn.execute(
        "INSERT INTO projects(user_id,project_id,name,aliases) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id,project_id) DO UPDATE SET name=excluded.name, "
        "aliases=excluded.aliases, "
        "revision=projects.revision+1",
        (user_id, project_id, name, json.dumps(aliases)),
    )
    conn.execute(
        "DELETE FROM project_files WHERE user_id=? AND project_id=?", (user_id, project_id)
    )
    conn.executemany(
        "INSERT INTO project_files(user_id,project_id,file_id) VALUES(?,?,?)",
        [(user_id, project_id, fid) for fid in ids],
    )
    return (current["revision"] if current else 0) + 1
