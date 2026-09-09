"""Regression tests for the SQLite-backed durable job queue."""

import asyncio
import json

import pytest

from src.core import database as db


def test_enqueue_is_idempotent_while_job_is_active(db_conn):
    first = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:7",
        payload={"file_id": 7},
    )
    second = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:7",
        payload={"file_id": 7, "force_meeting_summary": True},
    )

    assert second == first
    row = db_conn.execute("SELECT * FROM durable_jobs WHERE id=?", (first,)).fetchone()
    assert row["status"] == "pending"
    assert "force_meeting_summary" in row["payload_json"]


@pytest.mark.asyncio
async def test_processing_scheduler_persists_force_native_reindex():
    from src.core.database import get_connection
    from src.services.processor._scheduler import schedule_meeting_file_processing

    await schedule_meeting_file_processing(71, force_native_reindex=True)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload_json FROM durable_jobs WHERE dedupe_key='file:71'"
        ).fetchone()
    assert json.loads(row["payload_json"])["force_native_reindex"] is True


def test_claim_complete_and_reactivate_terminal_job(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="meeting_summary",
        dedupe_key="meeting:3",
        payload={"meeting_id": 3},
    )
    claimed = db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["attempts"] == 1
    assert db.claim_next_job(db_conn, owner="worker-b", lease_seconds=60) is None
    assert db.complete_job(db_conn, job_id=job_id, owner="worker-a") is True

    reactivated = db.enqueue_job(
        db_conn,
        kind="meeting_summary",
        dedupe_key="meeting:3",
        payload={"meeting_id": 3},
    )
    row = db_conn.execute(
        "SELECT status, attempts FROM durable_jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert reactivated == job_id
    assert dict(row) == {"status": "pending", "attempts": 0}


def test_enqueue_while_running_promotes_successor_after_completion(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:successor",
        payload={"generation": 1},
    )
    claimed = db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed["payload_json"] == '{"generation":1}'

    same_id = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:successor",
        payload={"generation": 2},
    )
    assert same_id == job_id
    running = db_conn.execute(
        "SELECT status, payload_json, rerun_requested, next_payload_json "
        "FROM durable_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    assert dict(running) == {
        "status": "running",
        "payload_json": '{"generation":1}',
        "rerun_requested": 1,
        "next_payload_json": '{"generation":2}',
    }

    assert db.complete_job(db_conn, job_id=job_id, owner="worker-a")
    successor = db.claim_next_job(db_conn, owner="worker-b", lease_seconds=60)
    assert successor is not None
    assert successor["payload_json"] == '{"generation":2}'
    assert successor["attempts"] == 1


def test_terminal_failure_promotes_waiting_successor(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:failed-successor",
        payload={"generation": 1},
        max_attempts=1,
    )
    assert db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)
    db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:failed-successor",
        payload={"generation": 2},
        max_attempts=1,
    )

    state = db.fail_job(
        db_conn,
        job_id=job_id,
        owner="worker-a",
        error="old generation failed",
        retry_delay_seconds=60,
    )
    assert state == "pending"
    successor = db.claim_next_job(db_conn, owner="worker-b", lease_seconds=60)
    assert successor is not None
    assert successor["payload_json"] == '{"generation":2}'


def test_running_job_lease_can_be_renewed_only_by_owner(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="meeting_summary",
        dedupe_key="meeting:lease",
        payload={"meeting_id": 4},
    )
    assert db.claim_next_job(db_conn, owner="worker-a", lease_seconds=10)

    assert db.renew_job_lease(
        db_conn,
        job_id=job_id,
        owner="worker-a",
        lease_seconds=60,
    )
    assert not db.renew_job_lease(
        db_conn,
        job_id=job_id,
        owner="worker-b",
        lease_seconds=60,
    )


@pytest.mark.asyncio
async def test_active_handler_is_cancelled_when_lease_is_lost(monkeypatch):
    from src.services import jobs

    with db.get_write_connection() as conn:
        db.enqueue_job(
            conn,
            kind="meeting_summary",
            dedupe_key="meeting:lost-lease",
            payload={"meeting_id": 44},
        )
        claimed = db.claim_next_job(conn, owner="worker-a", lease_seconds=60)
    assert claimed is not None

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _handler(_payload):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def _lose_lease(_job_id, _owner, _lease_seconds):
        await started.wait()

    monkeypatch.setattr(jobs, "_handler_for", lambda _kind: _handler)
    monkeypatch.setattr(jobs, "_renew_lease", _lose_lease)

    await jobs._execute_claimed(dict(claimed), "worker-a", 60)

    assert cancelled.is_set()
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT status, last_error FROM durable_jobs WHERE id=?", (claimed["id"],)
        ).fetchone()
    assert row["status"] == "pending"
    assert "ownership was lost" in row["last_error"]


@pytest.mark.asyncio
async def test_withdrawn_handler_does_not_cancel_its_worker(monkeypatch):
    from src.services import jobs

    with db.get_write_connection() as conn:
        job_id = db.enqueue_job(
            conn,
            kind="meeting_summary",
            dedupe_key="meeting:withdrawn-handler",
            payload={"meeting_id": 47},
        )
        claimed = db.claim_next_job(conn, owner="worker-a", lease_seconds=60)
    assert claimed is not None

    started = asyncio.Event()

    async def _handler(_payload):
        started.set()
        await asyncio.Event().wait()

    async def _renew_forever(_job_id, _owner, _lease_seconds):
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "_handler_for", lambda _kind: _handler)
    monkeypatch.setattr(jobs, "_renew_lease", _renew_forever)

    executor = asyncio.create_task(jobs._execute_claimed(dict(claimed), "worker-a", 60))
    await started.wait()
    assert await jobs.cancel_durable_jobs(
        kind="meeting_summary",
        dedupe_prefix="meeting:withdrawn-handler",
        exact=True,
    )
    await asyncio.wait_for(executor, timeout=1)

    assert not executor.cancelled()
    with db.get_connection() as conn:
        state = conn.execute("SELECT status FROM durable_jobs WHERE id=?", (job_id,)).fetchone()[0]
    assert state == "cancelled"


def test_expired_job_cannot_commit_database_side_effects():
    from src.core.job_fence import JobLeaseLostError, activate_job_fence

    with db.get_write_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS job_fence_probe (value TEXT)")
        job_id = db.enqueue_job(
            conn,
            kind="meeting_summary",
            dedupe_key="meeting:fence-probe",
            payload={"meeting_id": 45},
        )
        assert db.claim_next_job(conn, owner="worker-a", lease_seconds=60)
        conn.execute(
            "UPDATE durable_jobs SET lease_expires_at=datetime('now', '-1 second') WHERE id=?",
            (job_id,),
        )

    with pytest.raises(JobLeaseLostError), activate_job_fence(job_id, "worker-a"):
        with db.get_write_connection() as conn:
            conn.execute("INSERT INTO job_fence_probe(value) VALUES ('must-rollback')")

    with db.get_connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM job_fence_probe WHERE value='must-rollback'"
            ).fetchone()[0]
            == 0
        )


def test_orphaned_file_dead_letters_are_retired_but_preserved(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:999999",
        payload={"file_id": 999999},
        max_attempts=1,
    )
    assert db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)
    assert (
        db.fail_job(
            db_conn,
            job_id=job_id,
            owner="worker-a",
            error="source disappeared",
            retry_delay_seconds=0,
        )
        == "dead_letter"
    )

    assert db.retire_orphaned_file_jobs(db_conn) == 1
    row = db_conn.execute(
        "SELECT status,last_error FROM durable_jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert dict(row) == {"status": "cancelled", "last_error": "source disappeared"}


def test_exact_job_cancellation_does_not_match_a_numeric_prefix(db_conn):
    first = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:1",
        payload={"file_id": 1},
        priority=10,
        max_attempts=1,
    )
    second = db.enqueue_job(
        db_conn, kind="file_processing", dedupe_key="file:10", payload={"file_id": 10}
    )
    assert db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)
    assert (
        db.fail_job(
            db_conn,
            job_id=first,
            owner="worker-a",
            error="failed",
            retry_delay_seconds=0,
        )
        == "dead_letter"
    )

    assert db.cancel_jobs(db_conn, kind="file_processing", dedupe_prefix="file:1", exact=True) == [
        first
    ]
    states = {
        row["id"]: row["status"]
        for row in db_conn.execute(
            "SELECT id,status FROM durable_jobs WHERE id IN (?,?)", (first, second)
        ).fetchall()
    }
    assert states == {first: "cancelled", second: "pending"}


@pytest.mark.asyncio
async def test_job_fence_propagates_into_worker_threads():
    from src.core.job_fence import JobLeaseLostError, activate_job_fence

    with db.get_write_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS job_fence_probe (value TEXT)")
        job_id = db.enqueue_job(
            conn,
            kind="meeting_summary",
            dedupe_key="meeting:fence-thread",
            payload={"meeting_id": 46},
        )
        assert db.claim_next_job(conn, owner="worker-a", lease_seconds=60)
        conn.execute(
            "UPDATE durable_jobs SET lease_expires_at=datetime('now', '-1 second') WHERE id=?",
            (job_id,),
        )

    def _write() -> None:
        with db.get_write_connection() as conn:
            conn.execute("INSERT INTO job_fence_probe(value) VALUES ('thread-must-rollback')")

    with activate_job_fence(job_id, "worker-a"), pytest.raises(JobLeaseLostError):
        await asyncio.to_thread(_write)


def test_failures_retry_then_dead_letter(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="file_summary",
        dedupe_key="file:9",
        payload={"file_id": 9, "meeting_id": 2},
        max_attempts=2,
    )
    first = db.claim_next_job(db_conn, owner="worker-a", lease_seconds=60)
    assert first is not None
    assert (
        db.fail_job(
            db_conn,
            job_id=job_id,
            owner="worker-a",
            error="transient",
            retry_delay_seconds=0,
        )
        == "pending"
    )

    second = db.claim_next_job(db_conn, owner="worker-b", lease_seconds=60)
    assert second is not None
    assert second["attempts"] == 2
    assert (
        db.fail_job(
            db_conn,
            job_id=job_id,
            owner="worker-b",
            error="permanent",
            retry_delay_seconds=0,
        )
        == "dead_letter"
    )


def test_circuit_breaker_retry_waits_for_recovery_window(monkeypatch):
    from src.core.exceptions import LLMCircuitBreakerError
    from src.services import jobs

    monkeypatch.setattr(jobs.settings, "LLM_CIRCUIT_BREAKER_RECOVERY", 60)
    delay, terminal = jobs._retry_policy(
        LLMCircuitBreakerError("open"), attempts=1, job_id="stable-job"
    )

    assert 60 <= delay <= 66
    assert terminal is False


def test_permanent_job_errors_are_not_retried():
    from src.core.exceptions import LLMAuthenticationError
    from src.services import jobs

    assert jobs._retry_policy(LLMAuthenticationError("bad key"), 1, "job") == (0, True)
    assert jobs._retry_policy(FileNotFoundError("gone"), 1, "job") == (0, True)


def test_expired_lease_can_be_reclaimed_and_cancelled(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="fact_extraction",
        dedupe_key="session:s1:abc",
        payload={"session_id": "s1"},
    )
    assert db.claim_next_job(db_conn, owner="dead-worker", lease_seconds=60)
    db_conn.execute(
        "UPDATE durable_jobs SET lease_expires_at=datetime('now', '-1 second') WHERE id=?",
        (job_id,),
    )

    reclaimed = db.claim_next_job(db_conn, owner="worker-b", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed["lease_owner"] == "worker-b"
    assert db.cancel_jobs(
        db_conn,
        kind="fact_extraction",
        dedupe_prefix="session:s1:",
    ) == [job_id]
    row = db_conn.execute("SELECT status FROM durable_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "cancelled"


def test_expired_final_attempt_is_dead_lettered_instead_of_stuck_running(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="meeting_summary",
        dedupe_key="meeting:expired-final",
        payload={"meeting_id": 12},
        max_attempts=1,
    )
    assert db.claim_next_job(db_conn, owner="dead-worker", lease_seconds=60)
    db_conn.execute(
        "UPDATE durable_jobs SET lease_expires_at=datetime('now', '-1 second') WHERE id=?",
        (job_id,),
    )

    assert db.claim_next_job(db_conn, owner="replacement", lease_seconds=60) is None
    row = db_conn.execute(
        "SELECT status, lease_owner, last_error FROM durable_jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert row["status"] == "dead_letter"
    assert row["lease_owner"] is None
    assert "lease expired" in row["last_error"].lower()


def test_expired_final_attempt_promotes_waiting_successor(db_conn):
    job_id = db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:expired-successor",
        payload={"generation": 1},
        max_attempts=1,
    )
    assert db.claim_next_job(db_conn, owner="dead-worker", lease_seconds=60)
    db.enqueue_job(
        db_conn,
        kind="file_processing",
        dedupe_key="file:expired-successor",
        payload={"generation": 2},
        max_attempts=1,
    )
    db_conn.execute(
        "UPDATE durable_jobs SET lease_expires_at=datetime('now', '-1 second') WHERE id=?",
        (job_id,),
    )

    successor = db.claim_next_job(db_conn, owner="replacement", lease_seconds=60)
    assert successor is not None
    assert successor["payload_json"] == '{"generation":2}'
    assert successor["attempts"] == 1


def test_job_health_stats_exposes_expired_lease_age(db_conn):
    db.enqueue_job(
        db_conn,
        kind="meeting_summary",
        dedupe_key="meeting:expired-health",
        payload={"meeting_id": 13},
    )
    assert db.claim_next_job(db_conn, owner="dead-worker", lease_seconds=60)
    db_conn.execute("UPDATE durable_jobs SET lease_expires_at=datetime('now', '-90 seconds')")

    stats = db.job_health_stats(db_conn)
    assert stats["expired_running"] == 1
    assert stats["oldest_expired_seconds"] >= 89


@pytest.mark.asyncio
async def test_deleted_file_processing_retires_without_dead_letter(monkeypatch):
    from unittest.mock import AsyncMock

    from src.services.jobs import _handle_file_processing

    monkeypatch.setattr("src.services.processor._pipeline.process_meeting_file", AsyncMock())
    await _handle_file_processing({"file_id": 987654321})
