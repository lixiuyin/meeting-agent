"""Tests for BM25 rebuild timeout in _bm25_maintenance.py."""

from unittest.mock import MagicMock, patch


class TestBm25RebuildTimeout:
    def test_rebuild_skips_when_already_loaded(self):
        """Rebuild should skip when FTS5 is not empty and force=False."""
        from src.services.rag._bm25_maintenance import rebuild_bm25_from_chroma

        with patch(
            "src.services.rag._bm25_maintenance.load_bm25_from_database",
            return_value=True,
        ):
            rebuild_bm25_from_chroma(force=False)

    def test_rebuild_with_no_data(self):
        """Rebuild should handle empty Chroma data gracefully."""
        from src.services.rag._bm25_maintenance import rebuild_bm25_from_chroma

        mock_vs = MagicMock()
        mock_vs.get.return_value = {"ids": [], "documents": [], "metadatas": []}

        with (
            patch("src.services.rag._bm25_maintenance.get_vectorstore", return_value=mock_vs),
            patch(
                "src.services.rag._bm25_maintenance.load_bm25_from_database",
                return_value=False,
            ),
        ):
            rebuild_bm25_from_chroma(force=True, timeout=10.0)

    def test_rebuild_handles_chroma_failure(self):
        """Rebuild should handle Chroma failure gracefully."""
        from src.services.rag._bm25_maintenance import rebuild_bm25_from_chroma

        mock_vs = MagicMock()
        mock_vs.get.side_effect = RuntimeError("chroma unavailable")

        with (
            patch("src.services.rag._bm25_maintenance.get_vectorstore", return_value=mock_vs),
            patch(
                "src.services.rag._bm25_maintenance.load_bm25_from_database",
                return_value=False,
            ),
        ):
            rebuild_bm25_from_chroma(force=True, timeout=10.0)

    def test_is_bm25_rebuilding_flag(self):
        """The _bm25_rebuilding flag should be accessible."""
        from src.services.rag._bm25_maintenance import is_bm25_rebuilding

        # Should return a boolean without error
        assert isinstance(is_bm25_rebuilding(), bool)
