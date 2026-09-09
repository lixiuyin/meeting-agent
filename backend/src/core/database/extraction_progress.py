"""Read source-version-specific ingestion coverage without conflating it with review."""

import json

from ..memory_admission import file_memory_policy
from ..source_revision_fence import meeting_file_source_token


def extraction_progress(conn, user_id: str, *, meeting_id=None, project_id=None) -> dict[str, int]:
    clauses = ["f.user_id=?", "m.user_id=?"]
    values: list = [user_id, user_id]
    if meeting_id is not None:
        clauses.append("f.meeting_id=?")
        values.append(meeting_id)
    if project_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM project_files p WHERE p.file_id=f.id "
            "AND p.user_id=? AND p.project_id=?)"
        )
        values.extend([user_id, project_id])
    files = conn.execute(
        "SELECT f.* FROM meeting_files f JOIN meetings m ON m.id=f.meeting_id WHERE "
        + " AND ".join(clauses),
        values,
    ).fetchall()
    progress: dict[str, int] = dict.fromkeys(
        (
            "indexing",
            "reference",
            "held_for_source_review",
            "unknown",
            "queued",
            "running",
            "failed",
            "completed",
        ),
        0,
    )
    jobs_by_file: dict[int, list[dict]] = {}
    for row in conn.execute(
        "SELECT payload_json,status,rerun_requested FROM durable_jobs "
        "WHERE kind='fact_extraction' AND json_valid(payload_json) "
        "AND json_extract(payload_json,'$.user_id')=?",
        (user_id,),
    ):
        payload = json.loads(row["payload_json"])
        for file_id in payload.get("file_ids") or []:
            jobs_by_file.setdefault(file_id, []).append({**dict(row), "payload": payload})
    for raw in files:
        file = dict(raw)
        if (
            file_memory_policy(file, file.get("filename") or file.get("file_name") or "")
            != "project_state"
        ):
            progress["reference"] += 1
            continue
        if file["status"] != "ready":
            progress["failed" if file["status"] == "error" else "indexing"] += 1
            continue
        jobs = [
            job
            for job in jobs_by_file.get(file["id"], [])
            if job["payload"].get("source_file_revision") == meeting_file_source_token(file)
        ]
        states = {job["status"] for job in jobs}
        expected = {job["payload"].get("source_window_count") for job in jobs}
        windows = {job["payload"].get("source_window_start") for job in jobs}
        if states & {"dead_letter", "cancelled"}:
            stage = "failed"
        elif "running" in states:
            stage = "running"
        elif "pending" in states or any(job["rerun_requested"] for job in jobs):
            stage = "queued"
        elif (
            states == {"completed"}
            and expected == {len(jobs)}
            and len(windows) == len(jobs)
            and None not in windows
        ):
            stage = "completed"
        else:
            # Legacy jobs without a complete window manifest cannot prove coverage.
            stage = "unknown"
        progress[stage] += 1
    from ..memory_admission import reference_memory_sql

    memory_clauses = [
        "m.user_id=?",
        "m.assertion_status IN ('pending','disputed')",
        reference_memory_sql(),
    ]
    memory_values: list = [user_id]
    if meeting_id is not None:
        memory_clauses.append(
            "EXISTS (SELECT 1 FROM memory_scopes s WHERE s.memory_id=m.id "
            "AND s.scope_type='meeting' AND s.scope_id=?)"
        )
        memory_values.append(meeting_id)
    if project_id:
        memory_clauses.append("m.project_id=?")
        memory_values.append(project_id)
    progress["held_for_source_review"] = int(
        conn.execute(
            "SELECT COUNT(*) FROM user_memories m WHERE " + " AND ".join(memory_clauses),
            memory_values,
        ).fetchone()[0]
    )
    return progress
