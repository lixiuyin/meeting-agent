"""Tests for partial failure recording in _indexer_store.py.

Tests verify that when vector or BM25 deletion fails during
delete_meeting_chunks, a pending_vector_deletions row is recorded
so the startup reconciler can retry later.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class TestIndexerDeletePartialFailure:
    @pytest.fixture(autouse=True)
    def _mock_vectorstore_write_lock(self):
        """Mock vectorstore_write_lock to avoid real Chroma interactions."""
        with patch("src.services.rag._indexer_store.vectorstore_write_lock"):
            yield

    def test_vector_delete_records_pending_deletion(self):
        """When vector delete fails, pending_vector_deletions is recorded."""
        from src.services.rag._indexer_store import _delete_meeting_chunks_inner

        mock_vs = MagicMock()
        mock_vs.delete.side_effect = RuntimeError("chroma down")
        mock_settings = SimpleNamespace(RAGANYTHING_ENABLED=False)

        execute_calls = []

        def fake_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return MagicMock()

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = fake_execute

        with (
            patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs),
            patch("src.core.database.get_write_connection") as mock_get_conn,
            patch("src.services.rag._indexer_store._remove_from_bm25"),
            patch("src.services.rag._indexer_store._remove_summary_vectors"),
        ):
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

            _delete_meeting_chunks_inner(1, None, mock_settings)

        pending_calls = [s for s in execute_calls if "pending_vector_deletions" in s]
        assert len(pending_calls) >= 1

    def test_bm25_delete_records_pending_deletion(self):
        """When BM25 delete fails, pending_vector_deletions is recorded."""
        from src.services.rag._indexer_store import _delete_meeting_chunks_inner

        mock_vs = MagicMock()
        mock_settings = SimpleNamespace(RAGANYTHING_ENABLED=False)

        execute_calls = []

        def fake_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return MagicMock()

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = fake_execute

        with (
            patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs),
            patch("src.core.database.get_write_connection") as mock_get_conn,
            patch(
                "src.services.rag._indexer_store._remove_from_bm25",
                side_effect=RuntimeError("bm25 down"),
            ),
            patch("src.services.rag._indexer_store._remove_summary_vectors"),
        ):
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

            _delete_meeting_chunks_inner(1, None, mock_settings)

        pending_calls = [s for s in execute_calls if "pending_vector_deletions" in s]
        assert len(pending_calls) >= 1

    def test_all_succeed_no_pending_deletions(self):
        """When all deletes succeed, no pending_vector_deletions recorded."""
        from src.services.rag._indexer_store import _delete_meeting_chunks_inner

        mock_vs = MagicMock()
        mock_settings = SimpleNamespace(RAGANYTHING_ENABLED=False)

        execute_calls = []

        def fake_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return MagicMock()

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = fake_execute

        with (
            patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs),
            patch("src.core.database.get_write_connection") as mock_get_conn,
            patch("src.services.rag._indexer_store._remove_from_bm25"),
            patch("src.services.rag._indexer_store._remove_summary_vectors"),
        ):
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

            _delete_meeting_chunks_inner(1, None, mock_settings)

        pending_calls = [s for s in execute_calls if "pending_vector_deletions" in s]
        assert len(pending_calls) == 0

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ("meeting_7", (7, None)),
            ("meeting_7_file_9", (7, 9)),
        ],
    )
    def test_pending_scope_parser(self, scope, expected):
        from src.services.rag._indexer_store import _parse_deletion_scope

        assert _parse_deletion_scope(scope) == expected

    def test_retry_chroma_file_scope_uses_typed_filter(self):
        from src.services.rag._indexer_store import retry_pending_index_deletion

        store = MagicMock()
        with patch("src.services.rag._indexer_store.get_vectorstore", return_value=store):
            retry_pending_index_deletion("chroma", "meeting_7_file_9")
        store.delete.assert_called_once_with(where={"$and": [{"meeting_id": 7}, {"file_id": 9}]})

    def test_retry_bm25_scope_propagates_failure(self):
        from src.services.rag._indexer_store import retry_pending_index_deletion

        with patch(
            "src.services.rag._indexer_store._remove_from_bm25",
            side_effect=RuntimeError("database unavailable"),
        ):
            with pytest.raises(RuntimeError, match="database unavailable"):
                retry_pending_index_deletion("bm25", "meeting_7")
