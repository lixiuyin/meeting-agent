"""Real SQL execution journals: replay, cancellation, and owner isolation."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from src.api.routers.chat import _safe_sources
from src.services import chat_runs
from src.services.stream_bus import serialize_event


def test_session_config_preserves_temporal_and_continuation_semantics() -> None:
    config = json.loads(
        chat_runs._session_config(
            {
                "valid_at": "2025-03-01T00:00:00Z",
                "known_at": "2025-04-01T00:00:00Z",
                "continuation_mode": "saved_snapshot",
            }
        )
    )
    assert config["valid_at"] == "2025-03-01T00:00:00Z"
    assert config["known_at"] == "2025-04-01T00:00:00Z"
    assert config["continuation_mode"] == "saved_snapshot"


def test_withdraw_tolerates_malformed_historical_sources() -> None:
    assert _safe_sources(None) == []
    assert _safe_sources("not-json") == []
    assert _safe_sources('{"unexpected": true}') == []
    sources = _safe_sources('[{"meeting_id": 7}]')
    assert len(sources) == 1
    assert sources[0].meeting_id == 7


@pytest.mark.asyncio
async def test_disconnect_and_duplicate_request_share_one_execution():
    release = asyncio.Event()
    calls = 0

    async def source(session_id):
        nonlocal calls
        calls += 1
        await release.wait()
        yield serialize_event({"type": "token", "content": "answer"})
        yield serialize_event({"type": "done", "session_id": session_id})

    run_id = await chat_runs.start_run("u", "once", {"question": "q"}, source)
    stream = chat_runs.replay_run(run_id, "u")
    first = await anext(stream)
    assert "heartbeat" in first
    await stream.aclose()
    assert run_id in chat_runs._tasks
    assert await chat_runs.start_run("u", "once", {"question": "q"}, source) == run_id
    release.set()
    replay = [frame async for frame in chat_runs.replay_run(run_id, "u")]
    assert sum('"type": "token"' in frame for frame in replay) == 1
    assert calls == 1
    state = await asyncio.to_thread(chat_runs.get_run, run_id, "u")
    assert state["status"] == "completed"
    assert state["session_id"]
    after = [frame async for frame in chat_runs.replay_run(run_id, "u", after=1)]
    assert all("heartbeat" not in frame for frame in after)
    await chat_runs.shutdown_runs()


@pytest.mark.asyncio
async def test_run_cancellation_is_durable_and_cross_user_reads_fail():
    async def source(session_id):
        await asyncio.sleep(120)
        yield serialize_event({"type": "done", "session_id": session_id})

    run_id = await chat_runs.start_run("owner", "cancel", {"question": "q"}, source)
    with pytest.raises(HTTPException) as error:
        await asyncio.to_thread(chat_runs.get_run, run_id, "intruder")
    assert error.value.status_code == 404
    await chat_runs.cancel_run(run_id, "owner")
    await chat_runs.shutdown_runs()
    assert (await asyncio.to_thread(chat_runs.get_run, run_id, "owner"))["status"] == "cancelled"
    assert "RUN_CANCELLED" in "".join(
        [frame async for frame in chat_runs.replay_run(run_id, "owner")]
    )


@pytest.mark.asyncio
async def test_duplicate_cancel_waits_for_one_uninterrupted_cleanup():
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()

    async def source(session_id):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield serialize_event({"type": "done", "session_id": session_id})
        finally:
            cleaning.set()
            await asyncio.sleep(0.05)
            cleaned.set()

    run_id = await chat_runs.start_run("owner", "duplicate-cancel", {"question": "q"}, source)
    await asyncio.wait_for(entered.wait(), timeout=1)
    first = asyncio.create_task(chat_runs.cancel_run(run_id, "owner"))
    await asyncio.wait_for(cleaning.wait(), timeout=1)
    second = asyncio.create_task(chat_runs.cancel_run(run_id, "owner"))
    await asyncio.gather(first, second)

    assert cleaned.is_set()
    assert run_id not in chat_runs._tasks


@pytest.mark.asyncio
async def test_withdraw_running_turn_creates_branch_without_that_turn():
    entered = asyncio.Event()

    async def source(session_id):
        entered.set()
        await asyncio.sleep(120)
        yield serialize_event({"type": "done", "session_id": session_id})

    run_id = await chat_runs.start_run("u", "withdraw", {"question": "remove me"}, source)
    await asyncio.wait_for(entered.wait(), timeout=1)
    state = await asyncio.to_thread(chat_runs.get_run, run_id, "u")
    with chat_runs.db.get_write_connection() as conn:
        chat_runs.db.add_turn(
            conn,
            session_id=state["session_id"],
            human_content="kept question",
            ai_content="kept answer",
        )

    branch = await chat_runs.withdraw_run(run_id, "u")

    assert branch["session"]["parent_session_id"] == state["session_id"]
    assert branch["session"]["branch_reason"] == "withdraw"
    assert [item["content"] for item in branch["messages"]] == [
        "kept question",
        "kept answer",
    ]
    assert chat_runs.get_run(run_id, "u")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_changed_body_cannot_reuse_execution_key():
    async def source(session_id):
        yield serialize_event({"type": "done", "session_id": session_id})

    await chat_runs.start_run("u", "conflict", {"question": "q1"}, source)
    with pytest.raises(HTTPException) as error:
        await chat_runs.start_run("u", "conflict", {"question": "q2"}, source)
    assert error.value.status_code == 409
    await chat_runs.shutdown_runs()


@pytest.mark.asyncio
async def test_low_volume_progress_is_journaled_on_a_time_bound():
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def source(session_id):
        yield serialize_event({"type": "token", "content": "early"})
        waiting.set()
        await release.wait()
        yield serialize_event({"type": "done", "session_id": session_id})

    run_id = await chat_runs.start_run("u", "timed-flush", {"question": "q"}, source)
    await asyncio.wait_for(waiting.wait(), timeout=1)
    await asyncio.sleep(0.15)
    with chat_runs.db.get_connection() as conn:
        payloads = [
            row["payload"]
            for row in conn.execute(
                "SELECT payload FROM chat_run_events WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
        ]
    assert any('"content": "early"' in payload for payload in payloads)
    release.set()
    await chat_runs.shutdown_runs()


def test_recovery_does_not_append_error_after_committed_done_event():
    run_id = chat_runs.run_identity("u", "done-crash-gap")
    claimed, _session_id = chat_runs._claim(run_id, "u", {"question": "q"})
    assert claimed
    chat_runs._append(run_id, serialize_event({"type": "done", "session_id": _session_id}))

    with chat_runs.db.get_write_connection() as conn:
        conn.execute("UPDATE chat_runs SET owner='dead-process' WHERE id=?", (run_id,))
        chat_runs._recover(conn)

    with chat_runs.db.get_connection() as conn:
        state = conn.execute("SELECT status FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        payloads = [
            row["payload"]
            for row in conn.execute(
                "SELECT payload FROM chat_run_events WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
        ]
    assert state["status"] == "completed"
    assert sum('"type": "done"' in payload for payload in payloads) == 1
    assert all('"type": "error"' not in payload for payload in payloads)


def test_recovery_completes_a_turn_whose_answer_was_already_saved():
    run_id = chat_runs.run_identity("u", "saved-crash-gap")
    claimed, session_id = chat_runs._claim(run_id, "u", {"question": "q"})
    assert claimed
    with chat_runs.db.get_write_connection() as conn:
        conn.execute(
            "UPDATE chat_runs SET saved_ai_id=42,owner='dead-process' WHERE id=?", (run_id,)
        )
        chat_runs._recover(conn)

    state = chat_runs.get_run(run_id, "u")
    replay = []

    async def collect():
        replay.extend([frame async for frame in chat_runs.replay_run(run_id, "u")])

    asyncio.run(collect())
    assert state["status"] == "completed"
    assert state["session_id"] == session_id
    assert len(replay) == 1
    assert '"type": "done"' in replay[0]
    assert '"recovered": true' in replay[0]
