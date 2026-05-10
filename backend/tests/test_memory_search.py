"""Regression tests for memory search scoring."""

from src.core import database as db
from src.services.memory import memory_service
from src.services.memory._service._search import (
    _normalize_access_score,
    _normalize_importance,
)


def test_memory_score_components_are_normalized_to_same_range():
    assert _normalize_importance(1) == 0.0
    assert _normalize_importance(5) == 1.0
    assert _normalize_access_score(0, 1.0) == 0.0
    assert _normalize_access_score(1000, 1.0) == 1.0


def test_important_memory_search_is_not_dominated_by_access_count():
    user_id = "normalized_scoring_user"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key="high_importance",
            value="high importance",
            importance=5,
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key="high_access",
            value="high access",
            importance=1,
        )
        conn.execute(
            "UPDATE user_memories SET access_count=1000, last_accessed=CURRENT_TIMESTAMP "
            "WHERE user_id=? AND key=?",
            (user_id, "high_access"),
        )

    results = memory_service.search_important(user_id, min_importance=1, limit=2)

    assert [row["key"] for row in results] == ["high_importance", "high_access"]
