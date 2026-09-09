"""Context-propagated durable-job fencing for database commits."""

from __future__ import annotations

import contextvars
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

_active_job_fence: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "active_job_fence", default=None
)


class JobLeaseLostError(RuntimeError):
    """Raised when an expired durable-job executor attempts to commit."""


@contextmanager
def activate_job_fence(job_id: str, owner: str) -> Iterator[None]:
    """Fence database writes made by this job, including asyncio.to_thread calls."""
    token = _active_job_fence.set((job_id, owner))
    try:
        yield
    finally:
        _active_job_fence.reset(token)


def assert_active_job_fence(conn: sqlite3.Connection) -> None:
    """Reject a commit after the current job lost ownership or its lease expired."""
    fence = _active_job_fence.get()
    if fence is None:
        return
    job_id, owner = fence
    row = conn.execute(
        "SELECT 1 FROM durable_jobs WHERE id=? AND status='running' "
        "AND lease_owner=? AND lease_expires_at>CURRENT_TIMESTAMP",
        (job_id, owner),
    ).fetchone()
    if row is None:
        raise JobLeaseLostError(f"Durable job {job_id} no longer owns a valid lease")
