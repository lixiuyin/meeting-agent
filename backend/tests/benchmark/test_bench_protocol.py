import json
from pathlib import Path

from scripts._bench_protocol import audit_protocol


def test_repository_evaluation_protocol_is_valid() -> None:
    backend_dir = Path(__file__).resolve().parents[2]

    report = audit_protocol(backend_dir / "evaluation" / "protocol.json", backend_dir=backend_dir)

    assert report["valid"] is True, report["errors"]
    assert report["execution_ready"] is True
    assert report["implementation_status"]["performance"] == "implemented"
    assert report["implementation_status"]["rag_retrieval"] == "implemented"
    assert report["implementation_status"]["reranker_quality"] == "implemented"
    assert report["implementation_status"]["rag_answer"] == "implemented"
    assert report["implementation_status"]["multi_turn"] == "implemented"
    assert report["implementation_status"]["long_horizon_memory"] == "implemented"
    assert report["implementation_status"]["process_quality"] == "implemented"
    assert report["implementation_status"]["protocol_validity"] == "implemented"
    assert report["implementation_status"]["meeting_evidence_governance"] == "implemented"
    assert set(report["protocol_validity_checks"]) == {"exposure", "exploit", "mislead"}
    assert set(report["dataset_hashes"]) == {
        "evaluation/datasets/memory_cases.json",
        "evaluation/datasets/knowledge_graph_cases.json",
        "evaluation/datasets/multi_turn_cases.json",
        "evaluation/datasets/process_expectations.json",
        "evaluation/datasets/reranker_cases.json",
        "evaluation/datasets/evidence_governance_cases.json",
        "tests/fixtures/benchmark/e2e-smoke.txt",
        "tests/fixtures/benchmark/golden_set.json",
        "tests/fixtures/benchmark/queries.json",
    }


def test_protocol_audit_rejects_reference_free_context_recall(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    protocol = json.loads((backend_dir / "evaluation" / "protocol.json").read_text())
    protocol["reference_free_metrics"].append("context_recall")
    invalid_path = tmp_path / "protocol.json"
    invalid_path.write_text(json.dumps(protocol), encoding="utf-8")

    report = audit_protocol(invalid_path, backend_dir=backend_dir)

    assert report["valid"] is False
    assert "reference-required metrics cannot be declared reference-free" in report["errors"]


def test_protocol_audit_rejects_missing_validity_check(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    protocol = json.loads((backend_dir / "evaluation" / "protocol.json").read_text())
    protocol["protocol_validity_contracts"]["exposure"]["checks"].remove("artifact_dataset_hashes")
    invalid_path = tmp_path / "protocol.json"
    invalid_path.write_text(json.dumps(protocol), encoding="utf-8")

    report = audit_protocol(invalid_path, backend_dir=backend_dir)

    assert report["valid"] is False
    assert report["execution_ready"] is False
    assert "protocol_validity.exposure: missing checks: artifact_dataset_hashes" in report["errors"]
