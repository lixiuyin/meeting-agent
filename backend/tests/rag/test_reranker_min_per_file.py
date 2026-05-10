"""Tests for per-file reranker guarantee."""

import os
import tempfile
from pathlib import Path

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.rag._reranker import _apply_per_file_guarantee  # noqa: E402


class TestPerFileGuarantee:
    """Verify min_per_file forces file representation in results."""

    def test_each_file_gets_at_least_one(self):
        """3 files, top_n=5, min_per_file=1 → each file represented."""
        docs = [
            {"content": "a", "score": 0.9, "metadata": {"file_id": 1}},
            {"content": "b", "score": 0.8, "metadata": {"file_id": 1}},
            {"content": "c", "score": 0.7, "metadata": {"file_id": 1}},
            {"content": "d", "score": 0.6, "metadata": {"file_id": 2}},
            {"content": "e", "score": 0.5, "metadata": {"file_id": 3}},
        ]
        result = _apply_per_file_guarantee(docs, top_n=5, min_per_file=1)
        file_ids = {d["metadata"]["file_id"] for d in result}
        assert file_ids == {1, 2, 3}
        assert len(result) <= 5

    def test_dominant_file_cannot_monopolize(self):
        """File with many high-score chunks can't push out other files."""
        docs = [
            {"content": f"doc{i}", "score": 0.9 - i * 0.01, "metadata": {"file_id": 1}}
            for i in range(10)
        ] + [
            {"content": "other1", "score": 0.5, "metadata": {"file_id": 2}},
            {"content": "other2", "score": 0.4, "metadata": {"file_id": 3}},
        ]
        result = _apply_per_file_guarantee(docs, top_n=5, min_per_file=1)
        file_ids = {d["metadata"]["file_id"] for d in result}
        assert 2 in file_ids
        assert 3 in file_ids

    def test_min_per_file_zero_disables(self):
        """min_per_file=0 → pure score-based selection."""
        docs = [
            {"content": "a", "score": 0.9, "metadata": {"file_id": 1}},
            {"content": "b", "score": 0.8, "metadata": {"file_id": 1}},
            {"content": "c", "score": 0.1, "metadata": {"file_id": 2}},
        ]
        result = _apply_per_file_guarantee(docs, top_n=2, min_per_file=0)
        assert len(result) == 2
        assert result[0]["content"] == "a"
        assert result[1]["content"] == "b"

    def test_empty_docs(self):
        result = _apply_per_file_guarantee([], top_n=5, min_per_file=1)
        assert result == []

    def test_expands_when_distinct_files_exceed_top_n(self):
        """When distinct files > top_n, the result must cover every file —
        no file may be dropped just because top_n is small."""
        docs = [{"content": f"doc{i}", "score": 0.9, "metadata": {"file_id": i}} for i in range(20)]
        result = _apply_per_file_guarantee(docs, top_n=5, min_per_file=1)
        assert len(result) == 20  # every file represented
        assert {d["metadata"]["file_id"] for d in result} == set(range(20))
