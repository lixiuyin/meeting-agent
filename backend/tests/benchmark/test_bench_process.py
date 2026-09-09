"""Tests for deterministic process-quality evaluation."""

import json
from pathlib import Path
from types import SimpleNamespace

from scripts._bench_process import (
    evaluate_process_report,
    execute_failure_cases,
    validate_process_expectations,
)
from scripts.benchmark import run_process_benchmark

DATASET_PATH = Path(__file__).parents[2] / "evaluation" / "datasets" / "process_expectations.json"


def _expectations() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _success_report() -> dict:
    return {
        "diagnostics": {
            "chat_trace": {
                "trace_id": "chat-1",
                "terminal_status": "success",
                "session_id": "session-1",
                "answer": "Grounded answer [1]",
                "sources": [{"file_id": 1}],
                "spans": [
                    {"label": label, "status": "success", "sequence": index}
                    for index, label in enumerate(
                        [
                            "pipeline",
                            "ensure_session",
                            "retrieve",
                            "build_context",
                            "generate_answer",
                            "save_messages",
                        ]
                    )
                ],
            },
            "ingest_trace": {
                "trace_id": "ingest-1",
                "terminal_status": "success",
                "file_id": 1,
                "ready_status": "ready",
                "spans": [
                    {"label": label, "status": "success", "sequence": index}
                    for index, label in enumerate(
                        ["fetch_metadata", "parse", "index_meeting", "db_persist"]
                    )
                ],
            },
        }
    }


def test_repository_process_expectations_are_valid() -> None:
    validate_process_expectations(_expectations())


def test_success_process_report_scores_steps_and_artifacts() -> None:
    result = evaluate_process_report(_expectations(), _success_report())

    assert result["valid"] is True
    assert result["complete"] is False
    assert result["stats"]["step_accuracy"] == 1.0
    assert result["stats"]["artifact_coverage"] == 1.0
    assert result["stats"]["terminal_accuracy"] == 1.0
    assert result["stats"]["first_error_accuracy"] is None


def test_failure_trace_scores_earliest_causal_error() -> None:
    report = _success_report()
    report["diagnostics"]["failure_traces"] = [
        {
            "case_id": case["id"],
            "trace_id": f"failure-{index}",
            "expected_error_span": case["expected_error_span"],
            "expected_error_type": case["expected_error_type"],
            "spans": [
                {
                    "label": case["expected_error_span"],
                    "status": "error",
                    "sequence": 0,
                    "error_type": case["expected_error_type"],
                }
            ],
            "error_span": case["expected_error_span"],
            "error_type": case["expected_error_type"],
            "terminal_status": "error",
        }
        for index, case in enumerate(_expectations()["failure_cases"])
    ]

    result = evaluate_process_report(_expectations(), report)

    assert result["valid"] is True
    assert result["complete"] is True
    assert result["stats"]["first_error_accuracy"] == 1.0
    assert result["stats"]["error_type_accuracy"] == 1.0
    assert result["stats"]["artifact_coverage"] == 1.0
    assert result["stats"]["terminal_accuracy"] == 1.0


async def test_execute_failure_cases_attaches_withheld_expectations() -> None:
    async def missing_file_runner() -> dict:
        return {
            "trace_id": "failure",
            "error_span": "fetch_metadata",
            "error_type": "LookupError",
            "terminal_status": "error",
            "spans": [],
        }

    traces = await execute_failure_cases(
        _expectations(),
        runners={case["runner"]: missing_file_runner for case in _expectations()["failure_cases"]},
    )

    assert traces[0]["case_id"] == "ingest_missing_file_record"
    assert traces[0]["expected_error_span"] == "fetch_metadata"
    assert traces[0]["expected_error_type"] == "LookupError"
    assert len(traces) == 7


def test_missing_chat_trace_fails_validity() -> None:
    report = _success_report()
    del report["diagnostics"]["chat_trace"]

    result = evaluate_process_report(_expectations(), report)

    assert result["valid"] is False
    assert result["stats"]["success_trace_count"] == 1
    assert "missing chat_success trace" in result["validity_errors"]


def test_failure_trace_cannot_override_withheld_expectation() -> None:
    report = _success_report()
    case = _expectations()["failure_cases"][0]
    report["diagnostics"]["failure_traces"] = [
        {
            "case_id": case["id"],
            "trace_id": "tampered",
            "expected_error_span": "db_persist",
            "expected_error_type": case["expected_error_type"],
            "spans": [],
            "error_span": "db_persist",
            "error_type": case["expected_error_type"],
            "terminal_status": "error",
        }
    ]

    result = evaluate_process_report(_expectations(), report)

    assert result["valid"] is False
    assert result["complete"] is False
    assert any("does not match dataset" in error for error in result["validity_errors"])


def test_first_error_ignores_pipeline_propagation_wrapper() -> None:
    report = _success_report()
    cases = _expectations()["failure_cases"]
    report["diagnostics"]["failure_traces"] = [
        {
            "case_id": case["id"],
            "trace_id": f"wrapper-{index}",
            "expected_error_span": case["expected_error_span"],
            "expected_error_type": case["expected_error_type"],
            "spans": [
                {
                    "label": "pipeline",
                    "status": "error",
                    "sequence": 0,
                    "error_type": case["expected_error_type"],
                },
                {
                    "label": case["expected_error_span"],
                    "status": "error",
                    "sequence": 1,
                    "error_type": case["expected_error_type"],
                },
            ],
            "error_type": case["expected_error_type"],
            "terminal_status": "error",
        }
        for index, case in enumerate(cases)
    ]

    result = evaluate_process_report(_expectations(), report)

    assert result["valid"] is True
    assert result["stats"]["first_error_accuracy"] == 1.0


def test_process_benchmark_executes_every_declared_ingest_fault(tmp_path) -> None:
    report_path = tmp_path / "e2e.json"
    report_path.write_text(json.dumps(_success_report()), encoding="utf-8")

    result = run_process_benchmark(SimpleNamespace(report=report_path))

    assert result["valid"] is True, result["validity_errors"]
    assert result["complete"] is True
    assert result["stats"]["failure_trace_count"] == 7
    assert result["stats"]["first_error_accuracy"] == 1.0
    assert result["stats"]["error_type_accuracy"] == 1.0
    failure_rows = [
        row for row in result["process_quality"]["rows"] if row["trajectory"] == "failure"
    ]
    assert {row["case_id"] for row in failure_rows} == {
        case["id"] for case in _expectations()["failure_cases"]
    }
