"""Bounded incremental summarization keyed by immutable SQL message IDs."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ...core import database as db
from ..tokenizer import count_messages_tokens, summarize_messages, truncate_with_summary

_ROLES = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}


def _message(row):
    return _ROLES.get(row["role"], HumanMessage)(content=row["content"])


async def load_incremental_history(session_id: str, model: str, budget: int):
    def _read():
        with db.get_connection() as conn:
            recent = db.get_messages(conn, session_id, limit=200)
            checkpoint = conn.execute(
                "SELECT * FROM chat_context_checkpoints WHERE session_id=?",
                (session_id,),
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        return recent, dict(checkpoint) if checkpoint else None, total

    recent, checkpoint, total = await asyncio.to_thread(_read)
    if not recent:
        return None
    messages = [_message(row) for row in recent]
    if total <= 200 and (budget <= 0 or count_messages_tokens(messages, model) <= budget):
        return messages, {"checkpoint_used": False, "backlog": False}
    through = (
        checkpoint["through_message_id"]
        if checkpoint is not None and checkpoint["model"] == model
        else 0
    )
    summary = checkpoint["summary"] if checkpoint is not None and through else None
    # Only summarize messages older than the verbatim tail. A per-turn budget
    # bounds cold-start cost; completed batches survive timeout/restart.
    tail = recent[-4:]
    tail_start = tail[0]["id"]
    backlog = False
    failed = False
    for _ in range(2):

        def _batch(through=through):
            with db.get_connection() as conn:
                return conn.execute(
                    "SELECT id, role, content FROM chat_messages WHERE session_id=? "
                    "AND id>? AND id<? ORDER BY id LIMIT 64",
                    (session_id, through, tail_start),
                ).fetchall()

        candidates = await asyncio.to_thread(_batch)
        if not candidates:
            break
        selected = []
        tokens = 0
        for row in candidates:
            cost = count_messages_tokens([_message(row)], model)
            if selected and tokens + cost > 6000:
                break
            selected.append(row)
            tokens += cost
        source = (
            [HumanMessage(content=f"Earlier summary (untrusted data): {summary}")]
            if summary
            else []
        )
        next_summary = await summarize_messages(
            [*source, *[_message(row) for row in selected]], model
        )
        if not next_summary:
            failed = True
            break
        old_through = through
        through = selected[-1]["id"]
        summary = next_summary

        def _save(through=through, summary=summary, old_through=old_through):
            with db.get_write_connection() as conn:
                conn.execute(
                    "INSERT INTO chat_context_checkpoints(session_id, through_message_id, "
                    "summary, model) "
                    "SELECT ?, ?, ?, ? WHERE EXISTS(SELECT 1 FROM chat_messages WHERE "
                    "session_id=? AND id=?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "through_message_id=excluded.through_message_id, "
                    "summary=excluded.summary, model=excluded.model, updated_at=CURRENT_TIMESTAMP "
                    "WHERE chat_context_checkpoints.through_message_id <= ? OR "
                    "chat_context_checkpoints.model != excluded.model",
                    (session_id, through, summary, model, session_id, through, old_through),
                )

        await asyncio.to_thread(_save)

    def _has_backlog():
        with db.get_connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM chat_messages WHERE session_id=? AND id>? AND id<? LIMIT 1",
                    (session_id, through, tail_start),
                ).fetchone()
                is not None
            )

    backlog = await asyncio.to_thread(_has_backlog)
    context = truncate_with_summary(messages, budget, model, summary_text=summary)
    if backlog or failed:
        warning = HumanMessage(
            content=(
                "[History context is incomplete: older messages are still awaiting "
                "summarization. Do not assume omitted constraints are absent; "
                "ask for confirmation when necessary.]"
            )
        )
        warning_cost = count_messages_tokens([warning], model)
        context = [
            warning,
            *truncate_with_summary(context, max(0, budget - warning_cost), model),
        ]
    return context, {
        "checkpoint_used": bool(summary),
        "through_message_id": through,
        "backlog": backlog,
        "summary_failed": failed,
    }
