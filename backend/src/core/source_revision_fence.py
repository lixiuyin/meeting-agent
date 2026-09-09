"""Fence file-derived writes against deletion or source replacement.

The initial durable-job check happens before an LLM call and can therefore be
stale by the time extraction commits.  This context-propagated guard is checked
inside every outer write transaction, immediately before commit.
"""

from __future__ import annotations

import contextvars
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

_active_source_fence: contextvars.ContextVar[tuple[str, tuple[tuple[int, str], ...]] | None] = (
    contextvars.ContextVar("active_source_revision_fence", default=None)
)


class SourceRevisionChangedError(RuntimeError):
    """Raised when a file-derived job tries to commit against stale input."""


def meeting_file_source_token(record: dict) -> str:
    """Return a monotonic token, with timestamp fallback for legacy rows."""
    revision = record.get("source_revision")
    if revision is not None:
        return f"r:{int(revision)}"
    return str(record.get("updated_at") or "")


def meeting_file_source_tokens(record: dict) -> set[str]:
    """Return every current token accepted for a meeting file.

    ``r:N`` is the canonical monotonic token written by new extraction jobs.
    ``source:N`` was exposed by older API responses, so it remains a strict
    alias for the same numeric revision while persisted references migrate.
    Content/index identities and the legacy timestamp are also valid exact
    identities for callers that recorded those values.
    """
    tokens = {
        str(value)
        for value in (
            record.get("content_hash"),
            record.get("active_index_generation"),
            record.get("updated_at"),
        )
        if value is not None and str(value)
    }
    revision = record.get("source_revision")
    if revision is not None:
        number = int(revision)
        tokens.update({f"r:{number}", f"source:{number}"})
    return tokens


def meeting_file_source_matches(record: dict, expected: str) -> bool:
    expected_token = str(expected)
    if expected_token == meeting_file_source_token(record):
        return True
    revision = record.get("source_revision")
    return revision is not None and expected_token == f"source:{int(revision)}"


@contextmanager
def activate_source_revision_fence(
    user_id: str,
    file_revisions: list[tuple[int, str]],
) -> Iterator[None]:
    normalized = tuple((int(file_id), str(revision)) for file_id, revision in file_revisions)
    token = _active_source_fence.set((user_id, normalized) if normalized else None)
    try:
        yield
    finally:
        _active_source_fence.reset(token)


def assert_active_source_revision_fence(conn: sqlite3.Connection) -> None:
    """Require every fenced source row to exist at the expected revision."""
    fence = _active_source_fence.get()
    if fence is None:
        return
    user_id, file_revisions = fence
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(meeting_files)").fetchall()}
    select_columns = "source_revision, updated_at" if "source_revision" in columns else "updated_at"
    for file_id, expected_updated_at in file_revisions:
        row = conn.execute(
            f"SELECT {select_columns} FROM meeting_files WHERE id=? AND user_id=?",
            (file_id, user_id),
        ).fetchone()
        if row is None or not meeting_file_source_matches(dict(row), expected_updated_at):
            raise SourceRevisionChangedError(
                f"source file {file_id} was deleted or replaced before commit"
            )
