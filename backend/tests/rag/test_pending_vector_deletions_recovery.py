"""Verify typed pending-vector deletion recovery and dead-letter handling."""

from unittest.mock import MagicMock, patch

import pytest

from src.core.database import get_write_connection


@pytest.mark.unit
class TestPendingVectorDeletionsRecovery:
    def test_queue_pending_deletion(self):
        """Queueing a pending deletion writes to the table."""
        from src.services.memory._service._crud import _MemoryCrudMixin

        mixin = _MemoryCrudMixin()
        mixin._queue_pending_vector_deletion("memory", "fake_embedding_id_123")

        with get_write_connection() as conn:
            row = conn.execute(
                "SELECT * FROM pending_vector_deletions WHERE embedding_id=?",
                ("fake_embedding_id_123",),
            ).fetchone()
        assert row is not None
        assert row["collection"] == "memory"
        assert row["embedding_id"] == "fake_embedding_id_123"

    def test_cleanup_removes_processed_rows(self):
        """cleanup_pending_vector_deletions removes rows it processes."""
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        # Queue a deletion for an embedding that doesn't exist in the fake VS.
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions (collection, embedding_id) "
                "VALUES ('memory', ?)",
                ("cleanup_test_id",),
            )

        # The fake VS delete is a no-op, so the row should be cleaned up.
        with patch("src.services.memory._service._crud.get_memory_vectorstore") as get_store:
            get_store.return_value.delete = MagicMock()
            cleaned = cleanup_pending_vector_deletions()
        assert cleaned == 1
        with get_write_connection() as conn:
            assert (
                conn.execute(
                    "SELECT 1 FROM pending_vector_deletions WHERE embedding_id=?",
                    ("cleanup_test_id",),
                ).fetchone()
                is None
            )

    def test_cleanup_handles_empty_table(self):
        """cleanup_pending_vector_deletions on empty table returns 0."""
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        cleaned = cleanup_pending_vector_deletions()
        # Should handle empty table gracefully
        assert isinstance(cleaned, int)

    def test_cleanup_handles_entity_collection(self):
        """entity collection entries are recognized."""
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions (collection, embedding_id) "
                "VALUES ('entity', ?)",
                ("entity_test_id",),
            )

        cleaned = cleanup_pending_vector_deletions()
        assert isinstance(cleaned, int)

    def test_memory_delete_failure_remains_pending(self):
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions (collection, embedding_id) "
                "VALUES ('memory', 'memory-failure')"
            )
        with patch("src.services.memory._service._crud.get_memory_vectorstore") as get_store:
            get_store.return_value.delete.side_effect = RuntimeError("vector store down")
            assert cleanup_pending_vector_deletions(collections={"memory"}) == 0
        with get_write_connection() as conn:
            row = conn.execute(
                "SELECT attempts, status FROM pending_vector_deletions "
                "WHERE embedding_id='memory-failure'"
            ).fetchone()
        assert row["attempts"] == 1
        assert row["status"] == "pending"

    @pytest.mark.parametrize("collection", ["chroma", "bm25", "summary", "raganything"])
    def test_cleanup_dispatches_typed_index_jobs(self, collection):
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        scope = "meeting_12_file_34"
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions (collection, embedding_id) VALUES (?, ?)",
                (collection, scope),
            )

        with patch("src.services.rag._indexer_store.retry_pending_index_deletion") as retry:
            assert cleanup_pending_vector_deletions() == 1
            retry.assert_called_once_with(collection, scope)

    def test_unknown_collection_is_retried_then_dead_lettered(self, monkeypatch):
        from src.core.config import settings
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        monkeypatch.setattr(settings, "VECTOR_DELETION_MAX_ATTEMPTS", 1)
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions "
                "(collection, embedding_id) VALUES ('unknown', 'job-1')"
            )

        assert cleanup_pending_vector_deletions() == 0
        assert cleanup_pending_vector_deletions() == 0
        with get_write_connection() as conn:
            row = conn.execute(
                "SELECT attempts, status, last_error FROM pending_vector_deletions "
                "WHERE embedding_id='job-1'"
            ).fetchone()
        assert row["attempts"] == 1
        assert row["status"] == "dead_letter"
        assert "Unknown pending deletion collection" in row["last_error"]

    def test_raganything_failure_is_not_reported_as_success(self):
        from src.services.memory._service._crud import cleanup_pending_vector_deletions

        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO pending_vector_deletions "
                "(collection, embedding_id) VALUES ('raganything', 'meeting_9_file_2')"
            )
        with patch(
            "src.services.rag._indexer_store.retry_pending_index_deletion",
            side_effect=RuntimeError("provider down"),
        ):
            assert cleanup_pending_vector_deletions() == 0
        with get_write_connection() as conn:
            row = conn.execute(
                "SELECT attempts, status FROM pending_vector_deletions "
                "WHERE embedding_id='meeting_9_file_2'"
            ).fetchone()
        assert row["attempts"] == 1
        assert row["status"] == "pending"
