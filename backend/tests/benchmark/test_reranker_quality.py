import json
from pathlib import Path

import pytest

from scripts.benchmark import _validate_reranker_dataset


def test_repository_reranker_dataset_is_valid() -> None:
    path = Path(__file__).resolve().parents[2] / "evaluation/datasets/reranker_cases.json"
    dataset = json.loads(path.read_text())
    _validate_reranker_dataset(dataset)


def test_reranker_dataset_rejects_too_small_candidate_pool() -> None:
    dataset = {
        "schema_version": 1,
        "cases": [
            {
                "id": "small",
                "query": "query",
                "relevant_ids": ["one"],
                "candidates": [{"id": "one", "content": "answer"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="at least 12 candidates"):
        _validate_reranker_dataset(dataset)
