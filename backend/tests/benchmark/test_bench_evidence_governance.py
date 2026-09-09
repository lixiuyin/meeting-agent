import json
from pathlib import Path

from scripts._bench_evidence_governance import (
    execute_evidence_governance_cases,
    validate_evidence_governance_dataset,
)

DATASET = Path(__file__).parents[2] / "evaluation" / "datasets" / "evidence_governance_cases.json"


def test_repository_evidence_governance_dataset_executes_completely() -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    validate_evidence_governance_dataset(dataset)

    result = execute_evidence_governance_cases(dataset)

    assert result["valid"] is True
    assert result["complete"] is True
    assert all(value == 1.0 for value in result["stats"].values())
