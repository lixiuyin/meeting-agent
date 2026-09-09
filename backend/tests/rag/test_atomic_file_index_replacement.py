"""Failure-atomic file index replacement regression tests."""

from unittest.mock import MagicMock, patch

import pytest


def test_atomic_replacement_removes_failed_generation_on_index_failure():
    from src.services.rag._indexer_store import (
        _FileIndexSnapshot,
        atomic_file_index_replacement,
    )

    snapshot = _FileIndexSnapshot([], [])
    with (
        patch(
            "src.services.rag._indexer_store._snapshot_file_indexes",
            return_value=snapshot,
        ),
        patch("src.services.rag._indexer_store._rollback_generation") as rollback,
    ):
        with pytest.raises(RuntimeError, match="embedding failed"):
            with atomic_file_index_replacement(4, 9):
                raise RuntimeError("embedding failed")

    rollback.assert_called_once()
    assert rollback.call_args.args[:2] == (4, 9)


def test_atomic_replacement_prunes_only_after_success():
    from src.services.rag._indexer_store import (
        _FileIndexSnapshot,
        atomic_file_index_replacement,
    )

    snapshot = _FileIndexSnapshot([], [])
    with (
        patch(
            "src.services.rag._indexer_store._snapshot_file_indexes",
            return_value=snapshot,
        ),
        patch("src.services.rag._indexer_store._delete_stale_file_indexes") as prune,
    ):
        with atomic_file_index_replacement(4, 9) as generation:
            assert generation
            prune.assert_not_called()

    prune.assert_called_once_with(4, 9, generation, snapshot)


def test_rollback_deletes_only_failed_generation():
    from src.services.rag._indexer_store import _rollback_generation

    vectorstore = MagicMock()
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    connection_context.__exit__.return_value = False
    with (
        patch("src.services.rag._indexer_store.get_vectorstore", return_value=vectorstore),
        patch("src.services.rag._indexer_store.vectorstore_write_lock"),
        patch("src.core.database.get_write_connection", return_value=connection_context),
    ):
        _rollback_generation(4, 9, "failed-generation")

    vectorstore.delete.assert_called_once_with(
        where={
            "$and": [
                {"meeting_id": 4},
                {"file_id": 9},
                {"index_generation": "failed-generation"},
            ]
        }
    )
    vectorstore._collection.upsert.assert_not_called()
    connection.execute.assert_called_once()


def test_success_prunes_old_generation_after_new_generation_is_visible():
    from src.services.rag._indexer_store import (
        _delete_stale_file_indexes,
        _FileIndexSnapshot,
    )

    vectorstore = MagicMock()
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    connection_context.__exit__.return_value = False
    snapshot = _FileIndexSnapshot(
        vector_ids=["old-id"],
        bm25_ids=["old-id"],
    )

    with (
        patch("src.services.rag._indexer_store.get_vectorstore", return_value=vectorstore),
        patch("src.services.rag._indexer_store.vectorstore_write_lock"),
        patch("src.core.database.get_write_connection", return_value=connection_context),
    ):
        _delete_stale_file_indexes(4, 9, "new-generation", snapshot)

    vectorstore.delete.assert_called_once_with(ids=["old-id"])
    connection.executemany.assert_called_once_with(
        "DELETE FROM bm25_index WHERE chunk_id=?",
        [("old-id",)],
    )
