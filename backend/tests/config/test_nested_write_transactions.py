"""Regression tests for nested SQLite write transaction semantics."""

from uuid import uuid4

import pytest

from src.core.database import get_connection, get_write_connection


def _read_value(key: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM kv_state WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def test_inner_success_does_not_commit_outer_transaction() -> None:
    outer_key = f"nested-outer-{uuid4().hex}"
    inner_key = f"nested-inner-{uuid4().hex}"

    with pytest.raises(RuntimeError, match="abort outer"):
        with get_write_connection() as conn:
            conn.execute("INSERT INTO kv_state (key, value) VALUES (?, 'outer')", (outer_key,))
            with get_write_connection() as nested:
                nested.execute(
                    "INSERT INTO kv_state (key, value) VALUES (?, 'inner')", (inner_key,)
                )
            raise RuntimeError("abort outer")

    assert _read_value(outer_key) is None
    assert _read_value(inner_key) is None


def test_inner_failure_rolls_back_to_savepoint_only() -> None:
    before_key = f"nested-before-{uuid4().hex}"
    failed_key = f"nested-failed-{uuid4().hex}"
    after_key = f"nested-after-{uuid4().hex}"

    with get_write_connection() as conn:
        conn.execute("INSERT INTO kv_state (key, value) VALUES (?, 'before')", (before_key,))
        with pytest.raises(ValueError, match="inner failure"):
            with get_write_connection() as nested:
                nested.execute(
                    "INSERT INTO kv_state (key, value) VALUES (?, 'failed')", (failed_key,)
                )
                raise ValueError("inner failure")
        conn.execute("INSERT INTO kv_state (key, value) VALUES (?, 'after')", (after_key,))

    assert _read_value(before_key) == "before"
    assert _read_value(failed_key) is None
    assert _read_value(after_key) == "after"


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_nested_first_write_never_commits_before_outer_exit(failure) -> None:
    key = f"nested-first-{uuid4().hex}"
    with pytest.raises(failure):
        with get_write_connection():
            with get_write_connection() as conn:
                conn.execute("INSERT INTO kv_state (key, value) VALUES (?, 'inner')", (key,))
            raise failure()
    assert _read_value(key) is None
