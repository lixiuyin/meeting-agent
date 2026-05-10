"""Tests for delete_meeting_chunks in the RAG indexer.

Covers the fix: ChromaDB compound filter uses $and operator instead of plain dict.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestDeleteMeetingChunks:
    """Verify delete_meeting_chunks constructs correct ChromaDB where filters."""

    @pytest.fixture(autouse=True)
    def _reset_vectorstore(self):
        """Reset vectorstore singleton before each test."""
        from src.services.rag import _vectorstore as _vs_module

        _vs_module._vectorstore = None
        yield
        _vs_module._vectorstore = None

    def test_delete_without_file_id_uses_simple_filter(self):
        """When file_id is None, filter should be a simple dict."""
        from src.services.rag._indexer import delete_meeting_chunks

        mock_vs = MagicMock()
        with patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs):
            delete_meeting_chunks(meeting_id=42, file_id=None)

        mock_vs.delete.assert_called_once()
        call_kwargs = mock_vs.delete.call_args
        where = call_kwargs.kwargs.get("where") or call_kwargs[1].get("where")
        assert where == {"meeting_id": 42}

    def test_delete_with_file_id_uses_compound_filter(self):
        """When file_id is provided, filter must use $and operator for ChromaDB."""
        from src.services.rag._indexer import delete_meeting_chunks

        mock_vs = MagicMock()
        with patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs):
            delete_meeting_chunks(meeting_id=42, file_id=99)

        mock_vs.delete.assert_called_once()
        call_kwargs = mock_vs.delete.call_args
        where = call_kwargs.kwargs.get("where") or call_kwargs[1].get("where")

        # Must use $and compound filter — plain dicts with 2 keys crash ChromaDB
        assert "$and" in where, f"Expected $and in where clause, got: {where}"
        assert {"meeting_id": 42} in where["$and"]
        assert {"file_id": 99} in where["$and"]
        assert len(where["$and"]) == 2

    def test_delete_filter_is_valid_chromadb_where(self):
        """The generated filter must have exactly one operator at the top level."""
        from src.services.rag._indexer import delete_meeting_chunks

        mock_vs = MagicMock()
        with patch("src.services.rag._indexer_store.get_vectorstore", return_value=mock_vs):
            delete_meeting_chunks(meeting_id=7, file_id=3)

        call_kwargs = mock_vs.delete.call_args
        where = call_kwargs.kwargs.get("where") or call_kwargs[1].get("where")

        # ChromaDB requires exactly one top-level key in where clause
        top_level_keys = list(where.keys())
        assert len(top_level_keys) == 1, (
            f"Expected exactly 1 top-level key in where, got {top_level_keys}"
        )
        assert top_level_keys[0] == "$and"
