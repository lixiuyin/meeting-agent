"""Tests for memory database columns and decay."""

from datetime import datetime, timedelta
from unittest.mock import patch

from src.core import database as db
from src.core.database import get_write_connection
from src.services.memory import memory_service


class TestPhase2DatabaseColumns:
    def test_mark_memory_superseded(self):
        user_id = "supersede_test_user"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="old_fact", value="old value")
            db.set_memory(conn, user_id=user_id, key="new_fact", value="new value")
            db.mark_memory_superseded(
                conn, user_id=user_id, key="old_fact", superseded_by="new_fact"
            )

        with db.get_connection() as conn:
            mem = db.get_memory_full(conn, user_id=user_id, key="old_fact")
        assert mem is not None
        assert mem["superseded_by"] == "new_fact"

    def test_superseded_memory_excluded_from_search(self):
        user_id = "superseded_search_user"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="old_pref", value="old value", importance=4)
            db.set_memory(conn, user_id=user_id, key="new_pref", value="new value", importance=4)
            db.mark_memory_superseded(
                conn, user_id=user_id, key="old_pref", superseded_by="new_pref"
            )

        with db.get_connection() as conn:
            results = db.search_memories_by_importance(
                conn, user_id=user_id, min_importance=1, limit=10
            )
        keys = [r["key"] for r in results]
        assert "old_pref" not in keys
        assert "new_pref" in keys

    def test_update_memory_relevance_score(self):
        user_id = "relevance_score_user"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="scored_fact", value="some value")
            db.update_memory_relevance_score(
                conn, user_id=user_id, key="scored_fact", relevance_score=2.75
            )

        with db.get_connection() as conn:
            mem = db.get_memory_full(conn, user_id=user_id, key="scored_fact")
        assert mem is not None
        assert abs(mem["relevance_score"] - 2.75) < 0.001

    def test_search_orders_by_relevance_score(self):
        """Memories with higher relevance_score appear first."""
        user_id = "order_by_relevance_user"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="low_score", value="low", importance=3)
            db.set_memory(conn, user_id=user_id, key="high_score", value="high", importance=3)
            db.update_memory_relevance_score(
                conn, user_id=user_id, key="low_score", relevance_score=1.5
            )
            db.update_memory_relevance_score(
                conn, user_id=user_id, key="high_score", relevance_score=4.0
            )

        with db.get_connection() as conn:
            results = db.search_memories_by_importance(
                conn, user_id=user_id, min_importance=1, limit=10
            )
        keys = [r["key"] for r in results]
        assert keys.index("high_score") < keys.index("low_score")

    def test_get_memories_for_consolidation_excludes_superseded(self):
        user_id = "consolidation_query_user"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="active_mem", value="active")
            db.set_memory(conn, user_id=user_id, key="superseded_mem", value="old")
            db.set_memory(conn, user_id=user_id, key="current_mem", value="new")
            db.mark_memory_superseded(
                conn,
                user_id=user_id,
                key="superseded_mem",
                superseded_by="current_mem",
            )

        with db.get_connection() as conn:
            mems = db.get_memories_for_consolidation(conn, user_id=user_id)
        keys = [m["key"] for m in mems]
        assert "active_mem" in keys
        assert "current_mem" in keys
        assert "superseded_mem" not in keys


class TestFloatDecay:
    def test_decay_lowers_freshness_without_mutating_salience(self):
        """Decay updates freshness while preserving the user's salience judgment."""
        user_id = "float_decay_user"
        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id=user_id,
                key="decay_test_fact",
                value="some value",
                importance=4,
            )
            # Simulate old last_accessed (30 days ago) so decay is non-trivial
            past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE user_memories SET last_accessed=? WHERE user_id=? AND key=?",
                (past, user_id, "decay_test_fact"),
            )

        memory_service.decay_memories(user_id)

        with db.get_connection() as conn:
            mem = db.get_memory_full(conn, user_id=user_id, key="decay_test_fact")

        assert mem is not None
        assert float(mem["importance"]) == 4.0
        assert float(mem["salience"]) == 4.0
        assert float(mem["freshness_score"]) < 1.0

    def test_decay_respects_decay_enabled_setting(self):
        """When MEMORY_DECAY_ENABLED is False, decay_memories returns 0."""
        with patch("src.services.memory._service._decay_sync.settings") as mock_settings:
            mock_settings.MEMORY_DECAY_ENABLED = False
            result = memory_service.decay_memories("any_user")
        assert result == 0

    def test_second_decay_only_applies_new_elapsed_interval(self):
        """A second immediate run must not charge the full memory age again."""
        user_id = "incremental_decay_user"
        past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="fact", value="value", importance=4)
            conn.execute(
                "UPDATE user_memories SET created_at=?, last_accessed=? "
                "WHERE user_id=? AND key='fact'",
                (past, past, user_id),
            )

        memory_service.decay_memories(user_id)
        with db.get_connection() as conn:
            first = float(db.get_memory_full(conn, user_id=user_id, key="fact")["importance"])
        memory_service.decay_memories(user_id)
        with db.get_connection() as conn:
            second = float(db.get_memory_full(conn, user_id=user_id, key="fact")["importance"])

        assert abs(second - first) < 0.01

    def test_gc_scans_beyond_first_page_and_queues_vector_delete(self):
        user_id = "full_scan_gc_user"
        old = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        with get_write_connection() as conn:
            for index in range(100):
                db.set_memory(
                    conn,
                    user_id=user_id,
                    key=f"important_{index}",
                    value="keep",
                    importance=5,
                )
            db.set_memory(conn, user_id=user_id, key="stale", value="delete", importance=1)
            conn.execute(
                "UPDATE user_memories SET importance=0, salience=0, freshness_score=0, "
                "last_accessed=?, embedding_id='vec-stale' "
                "WHERE user_id=? AND key='stale'",
                (old, user_id),
            )

        assert memory_service.purge_stale_memories(user_id) == 1
        with db.get_connection() as conn:
            assert db.get_memory_full(conn, user_id=user_id, key="stale")["archived_at"] is not None
            assert db.list_memory_versions(conn, user_id=user_id, key="stale")
            queued = conn.execute(
                "SELECT 1 FROM pending_vector_deletions "
                "WHERE collection='memory' AND embedding_id='vec-stale'"
            ).fetchone()
        assert queued is not None


class TestPhase2ConfigSettings:
    def test_consolidation_settings_exist(self):
        from src.core.config import settings

        assert hasattr(settings, "MEMORY_CONSOLIDATION_ENABLED")
        assert hasattr(settings, "MEMORY_CONSOLIDATION_MIN_CLUSTER")

    def test_consolidation_defaults(self):
        from src.core.config import settings

        assert settings.MEMORY_CONSOLIDATION_ENABLED is False
        assert settings.MEMORY_CONSOLIDATION_MIN_CLUSTER == 3
