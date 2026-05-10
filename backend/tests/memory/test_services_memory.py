"""Tests for memory service"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.memory import MemoryService, SQLiteChatMessageHistory, get_session_history


class TestSQLiteChatMessageHistory:
    def test_add_and_retrieve_messages(self):
        """Test adding and retrieving messages from session history"""
        # Use the actual database (temporary, from conftest.py setup)
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="test", title="Test Session")

        # Create history and add messages
        history = SQLiteChatMessageHistory(sid)

        from langchain_core.messages import AIMessage, HumanMessage

        history.add_message(HumanMessage(content="Hello"))
        history.add_message(AIMessage(content="Hi there"))

        # Load from DB and verify
        history._load()
        assert len(history.messages) == 2
        assert isinstance(history.messages[0], HumanMessage)
        assert history.messages[0].content == "Hello"
        assert isinstance(history.messages[1], AIMessage)
        assert history.messages[1].content == "Hi there"

    def test_clear_messages(self):
        """Test clearing session history"""
        from langchain_core.messages import HumanMessage

        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="test")
        history = SQLiteChatMessageHistory(sid)
        history.add_message(HumanMessage(content="Test message"))

        # Clear and verify
        history.clear()
        assert len(history.messages) == 0

        # Verify in DB too
        with get_write_connection() as conn:
            count = db.count_messages(conn, sid)
        assert count == 0


class TestMemoryService:
    def test_memory_service_crud(self):
        """Test MemoryService CRUD operations"""
        service = MemoryService()
        user_id = "test_user_crud"

        # Set
        service.set(user_id, "key1", "value1")

        # Get
        value = service.get(user_id, "key1")
        assert value == "value1"

        # List
        memories = service.list_all(user_id)
        assert len(memories) == 1
        assert memories[0]["key"] == "key1"

        # Delete
        service.delete(user_id, "key1")
        assert service.get(user_id, "key1") is None

    def test_memory_service_get_not_found(self):
        """Test getting non-existent memory returns None"""
        service = MemoryService()
        result = service.get("nonexistent_user_xyz", "nonexistent_key_xyz")
        assert result is None

    def test_memory_service_default_user(self):
        """Test MemoryService with default user"""
        service = MemoryService()
        user_id = "default_test_user"

        # Use specific user
        service.set(user_id, "pref", "value")

        # List with that user should return the memory
        memories = service.list_all(user_id)
        keys = [m["key"] for m in memories]
        assert "pref" in keys


class TestGetSessionHistory:
    def test_get_session_history_caching(self):
        """Test that get_session_history caches histories"""
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="test")

        # Get history twice for same session
        hist1 = get_session_history(sid)
        hist2 = get_session_history(sid)

        # Should be same object (cached)
        assert hist1 is hist2

    def test_invalidate_session(self):
        """Test invalidating session cache"""
        from src.core import database as db
        from src.core.database import get_write_connection
        from src.services.memory import invalidate_session

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="test")

        # Get and cache
        hist1 = get_session_history(sid)

        # Invalidate
        invalidate_session(sid)

        # Get again - should be new object
        hist2 = get_session_history(sid)
        assert hist1 is not hist2


class TestMemoryServiceDecay:
    def test_decay_memories_updates_importance(self):
        """decay_memories() persists lower importance for stale memories."""
        from datetime import datetime, timedelta

        from src.core import database as db
        from src.core.database import get_connection, get_write_connection

        service = MemoryService()
        user_id = "decay_svc_test_user"
        service.set(user_id, "fact_decay", "loves coffee", importance=3)
        past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with get_write_connection() as conn:
            conn.execute(
                "UPDATE user_memories SET last_accessed=? WHERE user_id=? AND key=?",
                (past, user_id, "fact_decay"),
            )

        count = service.decay_memories(user_id)
        assert count >= 1

        with get_connection() as conn:
            row = db.get_memory_full(conn, user_id=user_id, key="fact_decay")
        assert row is not None
        assert float(row["importance"]) < 3.0

    def test_decay_disabled_returns_zero(self):
        """decay_memories() respects MEMORY_DECAY_ENABLED=False."""
        from unittest.mock import patch

        service = MemoryService()
        user_id = "decay_disabled_svc_user"
        service.set(user_id, "f1", "v1")

        with patch("src.services.memory._service._decay_sync.settings") as mock_s:
            mock_s.MEMORY_DECAY_ENABLED = False
            mock_s.MEMORY_DECAY_INTERVAL_HOURS = 24
            count = service.decay_memories(user_id)
        assert count == 0


class TestMemoryServiceExpiration:
    def test_expired_memory_excluded_by_default(self):
        """list_all() hides expired memories unless include_expired=True."""
        import datetime as dt

        from src.core import database as db
        from src.core.database import get_write_connection

        service = MemoryService()
        user_id = "ttl_svc_test_user"
        past = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="expired_mem", value="old", expires_at=past)

        active = service.list_all(user_id, include_expired=False)
        assert not any(m["key"] == "expired_mem" for m in active)

    def test_expired_memory_visible_with_include_expired(self):
        """list_all(include_expired=True) includes expired memories."""
        import datetime as dt

        from src.core import database as db
        from src.core.database import get_write_connection

        service = MemoryService()
        user_id = "ttl_include_svc_user"
        past = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="exp_visible", value="old", expires_at=past)

        all_mems = service.list_all(user_id, include_expired=True)
        assert any(m["key"] == "exp_visible" for m in all_mems)


class TestMemoryServiceCategory:
    def test_list_by_category_filters_correctly(self):
        """list_all(category=...) returns only memories in that category."""
        service = MemoryService()
        user_id = "cat_svc_test_user"
        service.set(user_id, "dark_mode", "enabled", category="preferences")
        service.set(user_id, "location", "NYC", category="personal")

        prefs = service.list_all(user_id, category="preferences")
        assert len(prefs) == 1
        assert prefs[0]["key"] == "dark_mode"

        personal = service.list_all(user_id, category="personal")
        assert len(personal) == 1
        assert personal[0]["key"] == "location"


class TestMemorySemanticScope:
    @pytest.mark.asyncio
    async def test_search_semantic_filters_by_meeting_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.core.config import settings

        service = MemoryService()
        user_id = "scope_mem_user"

        vector_results = [
            {"key": "scoped", "score": 0.01, "meeting_ids": [101]},
            {"key": "other_scope", "score": 0.02, "meeting_ids": [202]},
            {"key": "global_1", "score": 0.03, "meeting_ids": None},
            {"key": "global_2", "score": 0.04, "meeting_ids": None},
        ]
        batch = {
            "scoped": {"value": "meeting 101 fact", "importance": 4, "updated_at": "2026-01-01"},
            "other_scope": {
                "value": "meeting 202 fact",
                "importance": 4,
                "updated_at": "2026-01-01",
            },
            "global_1": {"value": "global fact one", "importance": 3, "updated_at": "2026-01-01"},
            "global_2": {"value": "global fact two", "importance": 3, "updated_at": "2026-01-01"},
        }
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = vector_results

        monkeypatch.setattr(settings, "GLOBAL_MEMORY_LIMIT", 1)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore", return_value=mock_vs
            ),
            patch("src.services.memory._service._search.db.get_memories_batch", return_value=batch),
            patch.object(service, "search_important", return_value=[]),
        ):
            entries = await service.search_semantic(
                user_id,
                query="query",
                limit=10,
                meeting_ids=[101],
            )

        keys = [entry.key for entry in entries]
        assert "scoped" in keys
        assert "other_scope" not in keys
        assert sum(1 for entry in entries if not entry.meeting_ids) == 1
