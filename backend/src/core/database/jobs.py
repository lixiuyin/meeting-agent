"""Repository helpers for durable background jobs.

SQLite remains the authoritative queue for the single-node deployment.  The
API process executes jobs itself so SQLite, Chroma and runtime configuration
remain under the same process-local coordination boundary.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

JOBS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'dead_letter', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,
    lease_expires_at DATETIME,
    last_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    rerun_requested INTEGER NOT NULL DEFAULT 0 CHECK(rerun_requested IN (0, 1)),
    next_payload_json TEXT,
    UNIQUE(kind, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim
    ON durable_jobs(status, available_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_durable_jobs_lease
    ON durable_jobs(status, lease_expires_at);
"""


def ensure_job_schema(conn: sqlite3.Connection) -> None:
    """Create the queue schema for legacy/bootstrap-only callers."""
    conn.executescript(JOBS_SCHEMA_SQL)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(durable_jobs)").fetchall()}
    if "rerun_requested" not in columns:
        conn.execute(
            "ALTER TABLE durable_jobs ADD COLUMN rerun_requested INTEGER NOT NULL "
            "DEFAULT 0 CHECK(rerun_requested IN (0, 1))"
        )
    if "next_payload_json" not in columns:
        conn.execute("ALTER TABLE durable_jobs ADD COLUMN next_payload_json TEXT")


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    dedupe_key: str,
    payload: Mapping[str, Any],
    priority: int = 0,
    max_attempts: int = 3,
) -> str:
    """Insert or coalesce a job without mutating a running execution.

    Pending work may safely absorb a newer payload because no worker has read it
    yet.  A running row is immutable: the newer payload is stored as a successor
    and promoted only after the current execution reaches a terminal state.
    """
    job_id = uuid.uuid4().hex
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO durable_jobs (
            id, kind, dedupe_key, payload_json, status, priority, max_attempts
        ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
        ON CONFLICT(kind, dedupe_key) DO UPDATE SET
            payload_json=CASE
                WHEN durable_jobs.status='running' THEN durable_jobs.payload_json
                ELSE excluded.payload_json
            END,
            next_payload_json=CASE
                WHEN durable_jobs.status='running' THEN excluded.payload_json
                ELSE NULL
            END,
            rerun_requested=CASE
                WHEN durable_jobs.status='running' THEN 1
                ELSE 0
            END,
            status=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled')
                    THEN 'pending'
                ELSE durable_jobs.status
            END,
            priority=MAX(durable_jobs.priority, excluded.priority),
            max_attempts=excluded.max_attempts,
            attempts=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled') THEN 0
                ELSE durable_jobs.attempts
            END,
            available_at=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled')
                    THEN CURRENT_TIMESTAMP
                ELSE durable_jobs.available_at
            END,
            lease_owner=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled') THEN NULL
                ELSE durable_jobs.lease_owner
            END,
            lease_expires_at=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled') THEN NULL
                ELSE durable_jobs.lease_expires_at
            END,
            last_error=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled') THEN NULL
                ELSE durable_jobs.last_error
            END,
            completed_at=CASE
                WHEN durable_jobs.status IN ('completed', 'dead_letter', 'cancelled') THEN NULL
                ELSE durable_jobs.completed_at
            END,
            updated_at=CURRENT_TIMESTAMP
        """,
        (job_id, kind, dedupe_key, payload_json, priority, max(1, max_attempts)),
    )
    row = conn.execute(
        "SELECT id FROM durable_jobs WHERE kind=? AND dedupe_key=?",
        (kind, dedupe_key),
    ).fetchone()
    if row is None:
        raise RuntimeError("Durable job was not persisted")
    return str(row["id"] if isinstance(row, sqlite3.Row) else row[0])


def is_job_active(conn: sqlite3.Connection, *, kind: str, dedupe_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM durable_jobs WHERE kind=? AND dedupe_key=? "
        "AND status IN ('pending', 'running')",
        (kind, dedupe_key),
    ).fetchone()
    return row is not None


def claim_next_job(
    conn: sqlite3.Connection,
    *,
    owner: str,
    lease_seconds: int,
    kinds: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next available job or an expired lease."""
    # A process may die after consuming the final attempt but before calling
    # fail_job(). Such a row used to remain ``running`` forever because the
    # claim predicate excludes attempts >= max_attempts. Reap it first so a
    # waiting successor is promoted or the abandoned execution is dead-lettered.
    reap_expired_jobs(conn)
    kind_values = tuple(sorted(set(kinds or ())))
    kind_sql = ""
    params: list[Any] = []
    if kind_values:
        kind_sql = f" AND kind IN ({','.join('?' for _ in kind_values)})"
        params.extend(kind_values)
    row = conn.execute(
        """
        SELECT * FROM durable_jobs
        WHERE attempts < max_attempts
          AND (
              (status='pending' AND available_at <= CURRENT_TIMESTAMP)
              OR (status='running' AND lease_expires_at < CURRENT_TIMESTAMP)
          )
        """
        + kind_sql
        + " ORDER BY priority DESC, created_at, id LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    job_id = str(row["id"])
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET status='running', attempts=attempts+1, lease_owner=?,
            lease_expires_at=datetime('now', ?), updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND (
            (status='pending' AND available_at <= CURRENT_TIMESTAMP)
            OR (status='running' AND lease_expires_at < CURRENT_TIMESTAMP)
        )
        """,
        (owner, f"+{max(1, lease_seconds)} seconds", job_id),
    )
    if cursor.rowcount != 1:
        return None
    claimed = conn.execute("SELECT * FROM durable_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(claimed) if claimed is not None else None


def reap_expired_jobs(conn: sqlite3.Connection) -> int:
    """Finalize expired leases that have no retry attempt remaining.

    A coalesced successor is promoted exactly as in :func:`fail_job`; otherwise
    the abandoned execution moves to ``dead_letter`` for operator visibility.
    """
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET status=CASE WHEN rerun_requested=1 THEN 'pending' ELSE 'dead_letter' END,
            payload_json=CASE
                WHEN rerun_requested=1 THEN COALESCE(next_payload_json, payload_json)
                ELSE payload_json
            END,
            attempts=CASE WHEN rerun_requested=1 THEN 0 ELSE attempts END,
            available_at=CASE
                WHEN rerun_requested=1 THEN CURRENT_TIMESTAMP
                ELSE available_at
            END,
            rerun_requested=0,
            next_payload_json=NULL,
            lease_owner=NULL,
            lease_expires_at=NULL,
            last_error='Worker lease expired after final attempt',
            updated_at=CURRENT_TIMESTAMP
        WHERE status='running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= CURRENT_TIMESTAMP
          AND attempts >= max_attempts
        """
    )
    return cursor.rowcount


def renew_job_lease(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    owner: str,
    lease_seconds: int,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET lease_expires_at=datetime('now', ?), updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='running' AND lease_owner=?
          AND lease_expires_at>CURRENT_TIMESTAMP
        """,
        (f"+{max(1, lease_seconds)} seconds", job_id, owner),
    )
    return cursor.rowcount == 1


def complete_job(conn: sqlite3.Connection, *, job_id: str, owner: str) -> bool:
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET status=CASE WHEN rerun_requested=1 THEN 'pending' ELSE 'completed' END,
            payload_json=CASE
                WHEN rerun_requested=1 THEN COALESCE(next_payload_json, payload_json)
                ELSE payload_json
            END,
            attempts=CASE WHEN rerun_requested=1 THEN 0 ELSE attempts END,
            available_at=CASE
                WHEN rerun_requested=1 THEN CURRENT_TIMESTAMP
                ELSE available_at
            END,
            rerun_requested=0,
            next_payload_json=NULL,
            lease_owner=NULL,
            lease_expires_at=NULL,
            last_error=NULL,
            completed_at=CASE
                WHEN rerun_requested=1 THEN NULL
                ELSE CURRENT_TIMESTAMP
            END,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='running' AND lease_owner=?
          AND lease_expires_at>CURRENT_TIMESTAMP
        """,
        (job_id, owner),
    )
    return cursor.rowcount == 1


def release_job(conn: sqlite3.Connection, *, job_id: str, owner: str) -> bool:
    """Release a claimed job without consuming an attempt during shutdown."""
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET status='pending', attempts=MAX(0, attempts-1), available_at=CURRENT_TIMESTAMP,
            lease_owner=NULL, lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='running' AND lease_owner=?
        """,
        (job_id, owner),
    )
    return cursor.rowcount == 1


def fail_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    owner: str,
    error: str,
    retry_delay_seconds: int,
    force_terminal: bool = False,
) -> str | None:
    """Release a failed job for retry or move it to the dead-letter state."""
    row = conn.execute(
        "SELECT attempts, max_attempts FROM durable_jobs "
        "WHERE id=? AND status='running' AND lease_owner=?",
        (job_id, owner),
    ).fetchone()
    if row is None:
        return None
    terminal = force_terminal or int(row["attempts"]) >= int(row["max_attempts"])
    successor = conn.execute(
        "SELECT rerun_requested FROM durable_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    promote_successor = bool(terminal and successor and int(successor["rerun_requested"]))
    new_status = "pending" if promote_successor or not terminal else "dead_letter"
    conn.execute(
        """
        UPDATE durable_jobs
        SET status=?,
            payload_json=CASE
                WHEN ? THEN COALESCE(next_payload_json, payload_json)
                ELSE payload_json
            END,
            attempts=CASE WHEN ? THEN 0 ELSE attempts END,
            rerun_requested=CASE WHEN ? THEN 0 ELSE rerun_requested END,
            next_payload_json=CASE WHEN ? THEN NULL ELSE next_payload_json END,
            available_at=CASE
                WHEN ? THEN CURRENT_TIMESTAMP
                ELSE datetime('now', ?)
            END,
            lease_owner=NULL,
            lease_expires_at=NULL,
            last_error=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='running' AND lease_owner=?
        """,
        (
            new_status,
            promote_successor,
            promote_successor,
            promote_successor,
            promote_successor,
            promote_successor,
            f"+{max(0, retry_delay_seconds)} seconds",
            error[:2000],
            job_id,
            owner,
        ),
    )
    return new_status


def cancel_jobs(
    conn: sqlite3.Connection,
    *,
    kind: str,
    dedupe_prefix: str,
    exact: bool = False,
) -> list[str]:
    operator = "=" if exact else "LIKE"
    value = dedupe_prefix if exact else f"{dedupe_prefix}%"
    rows = conn.execute(
        f"SELECT id FROM durable_jobs WHERE kind=? AND dedupe_key {operator} ? "
        "AND status IN ('pending', 'running', 'dead_letter')",
        (kind, value),
    ).fetchall()
    ids = [str(row["id"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]
    conn.execute(
        f"""
        UPDATE durable_jobs SET status='cancelled', lease_owner=NULL,
            lease_expires_at=NULL, completed_at=CURRENT_TIMESTAMP,
            rerun_requested=0, next_payload_json=NULL,
            updated_at=CURRENT_TIMESTAMP
        WHERE kind=? AND dedupe_key {operator} ?
          AND status IN ('pending', 'running', 'dead_letter')
        """,
        (kind, value),
    )
    return ids


def retire_orphaned_file_jobs(conn: sqlite3.Connection) -> int:
    """Retire diagnostic dead letters whose source file was intentionally removed."""
    cursor = conn.execute(
        """
        UPDATE durable_jobs
        SET status='cancelled', lease_owner=NULL, lease_expires_at=NULL,
            completed_at=COALESCE(completed_at, CURRENT_TIMESTAMP),
            updated_at=CURRENT_TIMESTAMP
        WHERE kind IN ('file_processing', 'file_summary')
          AND status='dead_letter'
          AND json_valid(payload_json)
          AND json_type(payload_json, '$.file_id') IN ('integer', 'real')
          AND NOT EXISTS (
              SELECT 1 FROM meeting_files
              WHERE meeting_files.id=CAST(
                  json_extract(durable_jobs.payload_json, '$.file_id') AS INTEGER
              )
          )
        """
    )
    return cursor.rowcount


def cleanup_finished_jobs(conn: sqlite3.Connection, *, retention_days: int = 7) -> int:
    cursor = conn.execute(
        "DELETE FROM durable_jobs WHERE status IN ('completed', 'cancelled') "
        "AND completed_at < datetime('now', ?)",
        (f"-{max(1, retention_days)} days",),
    )
    return cursor.rowcount


def job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM durable_jobs GROUP BY status"
        ).fetchall()
    }


def job_health_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Return lifecycle counts plus lease-expiry diagnostics."""
    stats = job_counts(conn)
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS expired_running,
            COALESCE(
                MAX(CAST((julianday('now') - julianday(lease_expires_at)) * 86400 AS INTEGER)),
                0
            )
                AS oldest_expired_seconds
        FROM durable_jobs
        WHERE status='running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= CURRENT_TIMESTAMP
        """
    ).fetchone()
    stats["expired_running"] = int(row["expired_running"] if row else 0)
    stats["oldest_expired_seconds"] = max(0, int(row["oldest_expired_seconds"] if row else 0))
    return stats
