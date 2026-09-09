"""Regression tests for scoped retrieval behavior."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"


def test_build_filters_drops_chunk_type_for_scoped_ids(monkeypatch):
    from src.services.rag import _retriever

    monkeypatch.setattr("src.services.rag._retriever.settings.PARENT_CHILD_ENABLED", True)
    filters = _retriever._build_filters(meeting_ids=[1], file_ids=[22])

    assert isinstance(filters, dict)
    as_text = str(filters)
    assert "meeting_id" in as_text
    assert "file_id" in as_text
    assert "chunk_type" not in as_text


def test_build_filters_scopes_default_principal(monkeypatch):
    from src.services.rag import _retriever

    monkeypatch.setattr("src.services.rag._retriever.settings.PARENT_CHILD_ENABLED", False)
    filters = _retriever._build_filters(user_id="default")

    assert _retriever._extract_eq_filter(filters, "user_id") == "default"


def test_vector_retrieve_scoped_disables_distance_threshold(monkeypatch):
    from src.services.rag import _retriever

    monkeypatch.setattr("src.services.rag._retriever.settings.PARENT_CHILD_ENABLED", False)
    monkeypatch.setattr("src.services.rag._retriever.settings.DISTANCE_METRIC", "l2")

    with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
        mock_vs = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {"meeting_id": 1, "file_id": 22}
        mock_doc.page_content = "scoped hit"
        mock_vs.similarity_search_with_score.return_value = [(mock_doc, 2.8)]
        mock_get_vs.return_value = mock_vs

        out = _retriever._vector_retrieve("q", {"file_id": {"$in": [22]}}, 5, threshold=1.5)
        assert [row["content"] for row in out] == ["scoped hit"]


def test_vector_retrieve_logs_warning_on_scoped_zero_results(monkeypatch, caplog):
    from src.services.rag import _retriever

    monkeypatch.setattr("src.services.rag._retriever.settings.PARENT_CHILD_ENABLED", False)
    with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
        mock_vs = MagicMock()
        mock_vs.similarity_search_with_score.return_value = []
        mock_get_vs.return_value = mock_vs

        out = _retriever._vector_retrieve(
            "q",
            {"$and": [{"meeting_id": {"$in": [7]}}, {"file_id": {"$in": [9]}}]},
            6,
        )
        assert out == []

    warning_msgs = [
        record.getMessage() for record in caplog.records if record.levelname == "WARNING"
    ]
    assert any("scoped retrieval returned 0 chunks" in msg for msg in warning_msgs)
    assert any(
        "meeting_ids=[7]" in msg and "file_ids=[9]" in msg and "k=6" in msg for msg in warning_msgs
    )


def test_vector_retrieve_bm25_fallback_logs_scoped_zero_results(monkeypatch, caplog):
    from src.services.rag import _retriever

    monkeypatch.setattr("src.services.rag._retriever.settings.PARENT_CHILD_ENABLED", False)
    with (
        patch("src.services.rag._retriever.get_vectorstore", side_effect=RuntimeError("down")),
        patch("src.services.rag._retriever._bm25_retrieve", return_value=[]) as bm25_mock,
    ):
        out = _retriever._vector_retrieve(
            "q",
            {"$and": [{"meeting_id": {"$in": [3]}}, {"file_id": {"$in": [11]}}]},
            4,
        )
        assert out == []
        bm25_mock.assert_called_once_with("q", [3], [11], 4, speaker_names=None, user_id=None)

    warning_msgs = [
        record.getMessage() for record in caplog.records if record.levelname == "WARNING"
    ]
    assert any("scoped retrieval returned 0 chunks" in msg for msg in warning_msgs)
