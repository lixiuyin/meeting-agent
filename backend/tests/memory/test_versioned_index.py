from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock

from src.core import database as db
from src.services.memory._service import _index_sync
from src.services.memory._vectorstore import MemoryVectorStore


def test_old_vector_cannot_publish_over_new_fact(monkeypatch):
    with db.get_write_connection() as conn:
        db.set_memory(conn, user_id="u", key="fact", value="v1")

    def old_upsert(*args, **kwargs):
        with db.get_write_connection() as conn:
            db.set_memory(conn, user_id="u", key="fact", value="v2")
        return "obsolete-vector"

    vs = MagicMock()
    vs.upsert.side_effect = old_upsert
    monkeypatch.setattr(_index_sync, "get_memory_vectorstore", lambda: vs)
    assert not _index_sync.index_current_memory("u", "fact")
    with db.get_connection() as conn:
        row = db.get_memory_full(conn, user_id="u", key="fact")
        assert row["value"] == "v2"
        assert row["revision"] == 2
        assert row["embedding_id"] is None
        assert conn.execute(
            "SELECT 1 FROM pending_vector_deletions WHERE embedding_id='obsolete-vector'"
        ).fetchone()


def test_reconciliation_preserves_scope_and_revision(monkeypatch):
    with db.get_write_connection() as conn:
        mid = db.create_meeting(conn, title="scope", user_id="u")
        db.set_memory(conn, user_id="u", key="fact", value="current", meeting_ids=[mid])
        original = db.get_memory_full(conn, user_id="u", key="fact")
        conn.execute("UPDATE user_memories SET vector_state='pending' WHERE user_id='u'")
    vs = MagicMock()
    vs.upsert.return_value = "current-vector"
    monkeypatch.setattr(_index_sync, "get_memory_vectorstore", lambda: vs)
    assert _index_sync.reconcile_memory_vectors() == 1
    kwargs = vs.upsert.call_args.kwargs
    assert kwargs["meeting_ids"] == [mid]
    assert kwargs["generation"] == f"{original['id']}:1"
    with db.get_connection() as conn:
        row = db.get_memory_full(conn, user_id="u", key="fact")
        assert row["revision"] == 1
        assert row["vector_state"] == "synced"


def test_failed_index_uses_backoff_not_age_based_discard(monkeypatch):
    with db.get_write_connection() as conn:
        db.set_memory(conn, user_id="u", key="fact", value="current")
        conn.execute(
            "UPDATE user_memories SET vector_state='pending',updated_at='2000-01-01' WHERE user_id='u'"
        )
    vs = MagicMock()
    vs.upsert.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(_index_sync, "get_memory_vectorstore", lambda: vs)
    assert _index_sync.reconcile_memory_vectors() == 0
    assert _index_sync.reconcile_memory_vectors() == 0
    assert vs.upsert.call_count == 1
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT vector_attempts,vector_retry_at FROM user_memories WHERE user_id='u'"
        ).fetchone()
        assert row["vector_attempts"] == 1
        assert row["vector_retry_at"]


def test_metadata_update_targets_the_published_versioned_embedding_id():
    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._collection_name = "memory-version-test"
    store._chromadb = MagicMock()
    embedding_id = store.upsert("u", "fact", "value", generation="17:3")

    assert embedding_id is not None
    assert store.bump_importance("u", "fact", 5, embedding_id=embedding_id)
    assert store._chromadb._collection.update.call_args.kwargs["ids"] == [embedding_id]
    assert embedding_id.endswith("_v17:3")


def test_metadata_update_without_published_id_is_deferred():
    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._collection_name = "memory-no-id-test"
    store._chromadb = MagicMock()

    assert not store.bump_importance("u", "fact", 5, embedding_id=None)
    store._chromadb._collection.update.assert_not_called()


def test_slow_embedding_does_not_hold_the_fact_mutation_lock(monkeypatch):
    from src.services.memory._service._crud import _get_key_lock

    with db.get_write_connection() as conn:
        db.set_memory(conn, user_id="u", key="fact", value="v1")
    entered, release = Event(), Event()

    def slow_upsert(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return "late-vector"

    vs = MagicMock()
    vs.upsert.side_effect = slow_upsert
    monkeypatch.setattr(_index_sync, "get_memory_vectorstore", lambda: vs)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_index_sync.index_current_memory, "u", "fact")
        try:
            assert entered.wait(2)
            lock = _get_key_lock("u", "fact")
            acquired = lock.acquire(timeout=0.2)
            try:
                assert acquired, "Network I/O blocked a fact mutation"
                with db.get_write_connection() as conn:
                    db.set_memory(conn, user_id="u", key="fact", value="v2")
            finally:
                if acquired:
                    lock.release()
        finally:
            release.set()
        assert not future.result(timeout=2)
    with db.get_connection() as conn:
        current = db.get_memory_full(conn, user_id="u", key="fact")
        assert current["value"] == "v2"
        assert current["embedding_id"] is None
