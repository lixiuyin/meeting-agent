"""Durable SSE journal and single-instance execution supervision.

Reconnects replay the same execution; they never regenerate a committed turn.
Process loss is explicitly interrupted, not silently restarted. Consumers can
read the canonical saved session if a crash occurred after answer persistence.
"""

import asyncio
import hashlib
import json
import logging
import uuid

from fastapi import HTTPException

from ..core import database as db
from ..core.chat_run_context import active_run, terminal_run_transition
from .stream_bus import serialize_event

logger = logging.getLogger(__name__)
_OWNER = uuid.uuid4().hex
_tasks: dict[str, asyncio.Task] = {}
_MAX_BYTES = 4 << 20


def run_identity(user_id: str, key: str) -> str:
    return hashlib.sha256(f"{user_id}\0{key}".encode()).hexdigest()


def _session_config(body: dict) -> str:
    fields = (
        "meeting_ids",
        "file_ids",
        "file_types",
        "date_from",
        "date_to",
        "valid_at",
        "known_at",
        "top_k",
        "use_web_search",
        "web_search_mode",
        "web_search_results",
        "rag_mode",
        "retrieval_profile",
        "memory_mode",
        "continuation_mode",
    )
    payload: dict[str, object] = {"schema_version": 1}
    payload.update({field: body.get(field) for field in fields})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _event_types(payload: str) -> list[str]:
    """Return valid SSE event types without letting one malformed frame hide others."""
    kinds: list[str] = []
    for line in payload.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            value = json.loads(line[6:])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            kinds.append(value["type"])
    return kinds


def _terminal(conn, run_id: str, status: str, message: str):
    row = conn.execute(
        "SELECT status,saved_ai_id,session_id FROM chat_runs WHERE id=?", (run_id,)
    ).fetchone()
    if row is None or row["status"] != "running":
        return False

    # The answer and saved_ai_id are committed together.  If the process died
    # after that transaction but before emitting ``done``, recovery must expose
    # the canonical saved answer instead of manufacturing a contradictory
    # error terminal.
    if row["saved_ai_id"] is not None and status == "interrupted":
        status = "completed"
        payload = serialize_event(
            {
                "type": "done",
                "session_id": row["session_id"],
                "run_id": run_id,
                "recovered": True,
            }
        )
    else:
        payload = serialize_event(
            {
                "type": "error",
                "code": f"RUN_{status.upper()}",
                "message": message,
                "run_id": run_id,
            }
        )
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0)+1 FROM chat_run_events WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    conn.execute("INSERT INTO chat_run_events VALUES (?, ?, ?)", (run_id, seq, payload))
    conn.execute(
        "UPDATE chat_runs SET status=?, event_bytes=event_bytes+?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
        (status, len(payload.encode()), run_id),
    )
    return True


def _recover(conn):
    stale = conn.execute(
        "SELECT id FROM chat_runs WHERE status='running' AND (owner!=? OR "
        "lease_expires_at<=CURRENT_TIMESTAMP)",
        (_OWNER,),
    ).fetchall()
    for row in stale:
        _terminal(
            conn,
            row["id"],
            "interrupted",
            "Execution was interrupted. Reload the saved session before starting another turn.",
        )


def _claim(run_id: str, user_id: str, body: dict) -> tuple[bool, str]:
    fingerprint = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with db.get_write_connection() as conn:
        _recover(conn)
        row = conn.execute(
            "SELECT * FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
        if row:
            if row["request_hash"] != fingerprint:
                raise HTTPException(409, "Idempotency-Key was used for another chat request")
            return False, row["session_id"]
        if (
            conn.execute("SELECT COUNT(*) FROM chat_runs WHERE status='running'").fetchone()[0]
            >= 64
        ):
            raise HTTPException(429, "Chat execution capacity reached")
        config_json = _session_config(body)
        session_id = body.get("session_id") or db.create_session(
            conn, user_id=user_id, config_json=config_json
        )
        if body.get("session_id"):
            conn.execute(
                "UPDATE chat_sessions SET config_json=?,config_version=1 WHERE id=? AND user_id=?",
                (config_json, session_id, user_id),
            )
        conn.execute(
            "INSERT INTO "
            "chat_runs(id,user_id,request_hash,question,session_id,owner,lease_expires_at) "
            "VALUES(?,?,?,?,?,?,datetime('now','+60 seconds'))",
            (run_id, user_id, fingerprint, body["question"], session_id, _OWNER),
        )
    return True, session_id


def _append(run_id: str, payload: str):
    size = len(payload.encode())
    kinds = _event_types(payload)
    terminal_kinds = {kind for kind in kinds if kind in {"done", "error"}}
    if len(terminal_kinds) > 1:
        raise RuntimeError("One journal append cannot contain conflicting terminal events")
    terminal_status = (
        "failed" if "error" in terminal_kinds else "completed" if "done" in terminal_kinds else None
    )
    with terminal_run_transition(), db.get_write_connection() as conn:
        row = conn.execute(
            "SELECT event_bytes,status FROM chat_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] != "running":
            raise RuntimeError("Run is no longer active")
        if row["event_bytes"] + size > _MAX_BYTES:
            raise RuntimeError("Durable stream output budget exceeded")
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM chat_run_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        frames = [frame + "\n\n" for frame in payload.split("\n\n") if frame.strip()]
        conn.executemany(
            "INSERT INTO chat_run_events VALUES(?,?,?)",
            [(run_id, seq + i, frame) for i, frame in enumerate(frames)],
        )
        if terminal_status:
            updated = conn.execute(
                "UPDATE chat_runs SET event_bytes=event_bytes+?,status=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                (size, terminal_status, run_id),
            ).rowcount
        else:
            updated = conn.execute(
                "UPDATE chat_runs SET event_bytes=event_bytes+?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='running'",
                (size, run_id),
            ).rowcount
        if updated != 1:
            raise RuntimeError("Run lost ownership while appending journal events")


async def _produce(run_id: str, session_id: str, factory):
    async def renew():
        while True:
            await asyncio.sleep(15)

            def tick():
                with db.get_write_connection() as conn:
                    return (
                        conn.execute(
                            "UPDATE chat_runs SET lease_expires_at=datetime('now','+60 seconds') "
                            "WHERE id=? AND owner=? AND status='running' AND "
                            "lease_expires_at>CURRENT_TIMESTAMP",
                            (run_id, _OWNER),
                        ).rowcount
                        == 1
                    )

            if not await asyncio.to_thread(tick):
                raise RuntimeError("Chat execution lease lost")

    async def execute():
        pending: list[str] = []
        pending_lock = asyncio.Lock()
        terminal = False
        stream = factory(session_id)

        async def flush_pending() -> None:
            async with pending_lock:
                if not pending:
                    return
                payload = "".join(pending)
                pending.clear()
                await asyncio.to_thread(_append, run_id, payload)

        async def timed_flush() -> None:
            # Persist low-volume progress even while the upstream generator is
            # blocked waiting for its next token. This bounds replay loss by
            # time, rather than by the provider's chunk cadence.
            while True:
                await asyncio.sleep(0.1)
                await flush_pending()

        flusher = asyncio.create_task(timed_flush(), name=f"chat-run-flush:{run_id}")
        try:
            with active_run(run_id, _OWNER):
                await asyncio.to_thread(
                    _append,
                    run_id,
                    serialize_event(
                        {"type": "heartbeat", "session_id": session_id, "run_id": run_id}
                    ),
                )
                async for chunk in stream:
                    if flusher.done():
                        await flusher
                    # Only terminal JSON event types determine success.
                    types = _event_types(chunk)
                    terminal |= any(kind in {"done", "error"} for kind in types)
                    async with pending_lock:
                        pending.append(chunk)
                        should_flush = terminal or len(pending) >= 20
                    if should_flush:
                        await flush_pending()
        finally:
            flusher.cancel()
            await asyncio.gather(flusher, return_exceptions=True)
            await flush_pending()
            await stream.aclose()
        if not terminal:
            raise RuntimeError("Chat ended without terminal event")

        # The terminal event and run status were committed atomically by
        # ``_append``.  There is deliberately no second completion transaction.

    worker = asyncio.create_task(execute())
    heartbeat = asyncio.create_task(renew())
    try:
        async with asyncio.timeout(1200):
            done, _ = await asyncio.wait({worker, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise RuntimeError("Chat heartbeat ended")
            await worker
    except BaseException as exc:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        def fail():
            with db.get_write_connection() as conn:
                row = conn.execute("SELECT status FROM chat_runs WHERE id=?", (run_id,)).fetchone()
                if row and row["status"] == "running":
                    _terminal(
                        conn,
                        run_id,
                        "interrupted",
                        "Execution interrupted; reload the saved session before retrying.",
                    )

        await asyncio.to_thread(fail)
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning("Durable chat failed: %s", type(exc).__name__)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        _tasks.pop(run_id, None)


async def start_run(user_id: str, key: str, body: dict, factory) -> str:
    if len(key) > 200:
        raise HTTPException(422, "Idempotency-Key is too long")
    run_id = run_identity(user_id, key)
    claim_task = asyncio.create_task(asyncio.to_thread(_claim, run_id, user_id, body))
    try:
        claimed, session_id = await asyncio.shield(claim_task)
    except asyncio.CancelledError:
        # ``to_thread`` cannot be cancelled once SQL has started. Wait for the
        # claim outcome and attach its supervisor before propagating request
        # cancellation, otherwise a committed running row can have no worker.
        claimed, session_id = await asyncio.shield(claim_task)
        if claimed:
            _tasks[run_id] = asyncio.create_task(
                _produce(run_id, session_id, factory), name=f"chat-run:{run_id}"
            )
        raise
    if claimed:
        _tasks[run_id] = asyncio.create_task(
            _produce(run_id, session_id, factory), name=f"chat-run:{run_id}"
        )
    return run_id


def get_run(run_id: str, user_id: str) -> dict:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT "
            "id,session_id,question,status,saved_ai_id,created_at,updated_at,owner,"
            "lease_expires_at<=CURRENT_TIMESTAMP AS expired "
            "FROM chat_runs WHERE id=? AND user_id=?",
            (run_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Chat run not found")
    if row["status"] == "running" and (row["owner"] != _OWNER or row["expired"]):
        with db.get_write_connection() as conn:
            _recover(conn)
        return get_run(run_id, user_id)
    return {k: v for k, v in dict(row).items() if k not in {"owner", "expired"}}


async def replay_run(run_id: str, user_id: str, after: int = 0):
    while True:
        state = await asyncio.to_thread(get_run, run_id, user_id)

        def read(after=after):
            with db.get_connection() as conn:
                return conn.execute(
                    "SELECT seq,payload FROM chat_run_events WHERE run_id=? AND seq>? ORDER BY "
                    "seq LIMIT 100",
                    (run_id, after),
                ).fetchall()

        rows = await asyncio.to_thread(read)
        for row in rows:
            after = row["seq"]
            yield f"id: {after}\n" + row["payload"]
        if state["status"] != "running" and len(rows) < 100:
            if not rows and after == 0:
                yield serialize_event(
                    {
                        "type": "error",
                        "code": "RUN_REPLAY_EXPIRED",
                        "message": "Replay expired; open the saved session.",
                        "session_id": state["session_id"],
                    }
                )
            return
        if not rows:
            await asyncio.sleep(0.1)


async def cancel_run(run_id: str, user_id: str):
    await asyncio.to_thread(get_run, run_id, user_id)

    def cancel():
        with db.get_write_connection() as conn:
            row = conn.execute(
                "SELECT status,saved_ai_id FROM chat_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if row and row["status"] == "running" and row["saved_ai_id"] is None:
                _terminal(conn, run_id, "cancelled", "Execution cancelled by user")
                return True
            return bool(row and row["status"] == "cancelled")

    if await asyncio.to_thread(cancel) and (task := _tasks.get(run_id)):
        # ``Task.cancel()`` is counted. A second cancel injected while the
        # supervisor is awaiting its worker's ``finally`` block can interrupt
        # persistence/resource cleanup. Cancellation is idempotent at the API
        # boundary, so only the first caller injects it; every caller still
        # waits for the same supervisor to drain completely.
        if not task.done() and task.cancelling() == 0:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def withdraw_run(run_id: str, user_id: str) -> dict:
    """Cancel a current turn and activate a branch that excludes that turn."""
    await cancel_run(run_id, user_id)

    def branch() -> dict:
        with db.get_write_connection() as conn:
            run = conn.execute(
                "SELECT session_id,saved_ai_id FROM chat_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if run is None:
                raise HTTPException(404, "Chat run not found")
            boundary = None
            if run["saved_ai_id"] is not None:
                boundary_row = conn.execute(
                    "SELECT id FROM chat_messages WHERE session_id=? AND role='human' AND id<? "
                    "ORDER BY id DESC LIMIT 1",
                    (run["session_id"], run["saved_ai_id"]),
                ).fetchone()
                if boundary_row is None:
                    raise RuntimeError("Saved chat run has no paired user message")
                boundary = int(boundary_row["id"])
            branch_id = db.branch_session(
                conn,
                source_session_id=run["session_id"],
                user_id=user_id,
                before_message_id=boundary,
                reason="withdraw",
            )
            session = db.get_session(conn, branch_id, user_id=user_id)
            assert session is not None
            total = db.count_messages(conn, branch_id)
            messages = db.get_messages(conn, branch_id, limit=200)
            return {
                "session": session,
                "messages": messages,
                "total": total,
                "next_before_id": (
                    messages[0]["id"] if total > len(messages) and messages else None
                ),
            }

    return await asyncio.to_thread(branch)


def cleanup_runs():
    with db.get_write_connection() as conn:
        _recover(conn)
        # The execution record (and dedupe identity) outlives the 24h journal.
        conn.execute(
            "DELETE FROM chat_run_events WHERE run_id IN (SELECT id FROM chat_runs WHERE "
            "status!='running' AND updated_at<datetime('now','-1 day'))"
        )


async def shutdown_runs():
    tasks = list(_tasks.values())
    for task in tasks:
        if not task.done() and task.cancelling() == 0:
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
