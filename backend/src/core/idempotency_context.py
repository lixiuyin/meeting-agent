"""Fence request mutations and remember ambiguous commits durably.

Resource endpoints should store their replayable response in the business
transaction. Other guarded mutations fail closed after an ambiguous commit:
an expired reservation must never silently authorize duplicate side effects.
"""

import contextvars
import json
from contextlib import contextmanager

_active: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "idempotency_mutation", default=None
)


def activate(key: str, token: str) -> None:
    _active.set((key, token))


def deactivate(key: str) -> None:
    active = _active.get()
    if active and active[0] == key:
        _active.set(None)


@contextmanager
def internal_operation():
    token = _active.set(None)
    try:
        yield
    finally:
        _active.reset(token)


def fence_commit(conn) -> None:
    active = _active.get()
    if active is None:
        return
    from .database.idempotency import _IN_PROGRESS_FIELD, _decrypt, _encrypt

    key, token = active
    row = conn.execute(
        "SELECT response_body, expires_at > CURRENT_TIMESTAMP AS live "
        "FROM idempotency_keys WHERE key=?",
        (key,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Idempotency reservation was removed")
    value = json.loads(_decrypt(row["response_body"]))
    if not value.get(_IN_PROGRESS_FIELD):
        if value.get("_idempotency_completed_by") != token:
            raise RuntimeError("Idempotency response belongs to another executor")
        return  # Response was completed inside this business transaction.
    if value.get(_IN_PROGRESS_FIELD) != token or not row["live"]:
        raise RuntimeError("Idempotency reservation ownership was lost")
    value["_effects_committed"] = True
    conn.execute(
        "UPDATE idempotency_keys SET response_body=?, lifecycle_state='effects_committed' "
        "WHERE key=?",
        (_encrypt(json.dumps(value)), key),
    )
