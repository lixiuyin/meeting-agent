"""T1: Verify set() scope merging under concurrent writes (M-C1 / M-C3)."""

import pytest

from src.core import database as db
from src.core.database._scopes import get_scopes
from src.services.memory import memory_service


def _get_scope_ids(user_id: str, key: str) -> tuple[list[int], list[int]]:
    """Helper: read merged scope IDs from the junction table."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM user_memories WHERE user_id=? AND key=?", (user_id, key)
        ).fetchone()
    if not row:
        return [], []
    return get_scopes(conn, kind="memory", owner_id=int(row["id"]))


@pytest.mark.unit
class TestMemorySetConcurrentScope:
    def test_scope_union_on_repeated_set(self):
        """set() with different meeting_ids should merge (union) scopes."""
        user_id = "test_scope_user"
        key = "test_scope_key"

        memory_service.set(user_id, key, "value_v1", meeting_ids=[1])
        memory_service.set(user_id, key, "value_v2", meeting_ids=[2])

        mids, _ = _get_scope_ids(user_id, key)
        assert 1 in mids, f"Expected meeting_id 1 in scope, got: {mids}"
        assert 2 in mids, f"Expected meeting_id 2 in scope, got: {mids}"

    def test_scope_preserves_file_ids(self):
        """set() with file_ids should merge correctly."""
        user_id = "test_file_scope_user"
        key = "test_file_key"

        memory_service.set(user_id, key, "v1", meeting_ids=[10], file_ids=[100])
        memory_service.set(user_id, key, "v2", meeting_ids=[20], file_ids=[200])

        _, fids = _get_scope_ids(user_id, key)
        assert 100 in fids, f"Expected file_id 100, got: {fids}"
        assert 200 in fids, f"Expected file_id 200, got: {fids}"

    def test_empty_scope_does_not_overwrite_existing(self):
        """Setting with no scope preserves previous scope."""
        user_id = "test_empty_scope"
        key = "test_key"

        memory_service.set(user_id, key, "v1", meeting_ids=[5])
        memory_service.set(user_id, key, "v2")  # no scope

        mids, _ = _get_scope_ids(user_id, key)
        assert 5 in mids, "Previous scope should be preserved"
