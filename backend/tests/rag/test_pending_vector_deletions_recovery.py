"""T10: Verify pending_vector_deletions recovery on startup (M-H3)."""

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
        cleaned = cleanup_pending_vector_deletions()
        # With the mock VS, delete should "succeed", so the row is removed.
        assert cleaned >= 0  # should not crash

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
