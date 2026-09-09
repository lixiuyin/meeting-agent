"""Fence SQL side effects of cancelled/interrupted streaming runs."""

import contextvars
from contextlib import contextmanager

_run: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "chat_run", default=None
)
_terminal_transition: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chat_run_terminal_transition", default=False
)
_cancelled_partial_commit: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chat_run_cancelled_partial_commit", default=False
)


@contextmanager
def active_run(run_id: str, owner: str):
    token = _run.set((run_id, owner))
    try:
        yield
    finally:
        _run.reset(token)


@contextmanager
def terminal_run_transition():
    """Allow only the journal transaction to atomically leave ``running``."""
    token = _terminal_transition.set(True)
    try:
        yield
    finally:
        _terminal_transition.reset(token)


@contextmanager
def cancelled_partial_commit():
    """Allow the owning cancelled run to save its visible partial answer once."""
    token = _cancelled_partial_commit.set(True)
    try:
        yield
    finally:
        _cancelled_partial_commit.reset(token)


def fence_run_commit(conn):
    current = _run.get()
    if _terminal_transition.get():
        return
    allowed_status = (
        "IN ('running','cancelled')" if _cancelled_partial_commit.get() else "='running'"
    )
    if (
        current is not None
        and conn.execute(
            f"SELECT 1 FROM chat_runs WHERE id=? AND owner=? AND status {allowed_status} "
            "AND lease_expires_at>CURRENT_TIMESTAMP",
            current,
        ).fetchone()
        is None
    ):
        raise RuntimeError("Chat run no longer owns a live execution lease")


def record_saved_turn(conn, session_id: str, ai_id: int):
    current = _run.get()
    if current:
        conn.execute(
            "UPDATE chat_runs SET session_id=?, saved_ai_id=? WHERE id=? AND owner=?",
            (session_id, ai_id, *current),
        )
