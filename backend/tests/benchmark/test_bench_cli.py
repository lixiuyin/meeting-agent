"""CLI parser and regression-baseline coverage for benchmark commands."""

import json
from dataclasses import dataclass

import pytest

from scripts.benchmark import (
    DEFAULT_JUDGE_MODEL,
    _assess_quality_evidence,
    _baseline_payload_for,
    _benchmark_source_artifact,
    _build_baseline_document,
    _build_parser,
    _compare_baseline,
    _load_baseline_report,
    _tree_fingerprint,
    run_rag_all_benchmark,
)

FINGERPRINTS = {
    "dataset_fingerprint_sha256": "dataset",
    "harness_fingerprint_sha256": "harness",
    "implementation_fingerprint_sha256": "implementation",
}


def test_cli_invalidates_a_run_when_implementation_changes_mid_execution(monkeypatch, tmp_path):
    import sys
    from unittest.mock import Mock

    from scripts import benchmark

    monkeypatch.setattr(sys, "argv", ["benchmark", "memory-pipeline"])
    monkeypatch.setattr(
        benchmark,
        "_capture_run_metadata",
        Mock(
            side_effect=[
                {**FINGERPRINTS, "captured_at": "start"},
                {
                    **FINGERPRINTS,
                    "captured_at": "end",
                    "implementation_fingerprint_sha256": "changed",
                },
            ]
        ),
    )
    payload = {"command": "memory-pipeline", "valid": True, "complete": True, "stats": {}}
    monkeypatch.setattr(benchmark, "run_memory_pipeline_benchmark", lambda _args: payload)
    monkeypatch.setattr(
        benchmark, "_write_report", lambda *_: (tmp_path / "report.json", tmp_path / "report.md")
    )
    assert benchmark.main() == 1
    assert not payload["valid"] and not payload["complete"]
    assert payload["run_metadata"]["implementation_fingerprint_sha256"] == "implementation"
    assert not payload["run_metadata"]["inputs_unchanged"]


def test_rag_all_parser_has_rag_quality_args() -> None:
    args = _build_parser().parse_args(["rag-all"])

    assert args.top_k == 10
    assert args.judge_repeats == 3
    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.update_snapshots is False


def test_quality_evidence_separates_validity_from_release_readiness() -> None:
    assessment = _assess_quality_evidence(
        observed_cases=10,
        dataset_kind="synthetic",
        judge_repeats=1,
        same_model_as_system=True,
        reranker_expected=True,
        reranker_evaluated=0,
    )

    assert assessment["grade"] == "diagnostic"
    assert assessment["release_ready"] is False
    assert set(assessment["limitations"]) == {
        "dataset_is_not_a_production_holdout",
        "fewer_than_30_cases",
        "fewer_than_3_judge_repeats",
        "judge_uses_the_system_model",
        "reranker_not_executed_for_every_query",
    }


def test_all_parser_has_rag_quality_args() -> None:
    args = _build_parser().parse_args(["all", "--process-report", "e2e.json"])

    assert args.top_k == 10
    assert args.judge_repeats == 3
    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.reasoner_model is None
    assert args.update_snapshots is False
    assert args.report.name == "e2e.json"


def test_multi_turn_parser_has_judge_and_baseline_args() -> None:
    args = _build_parser().parse_args(["multi-turn"])

    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.judge_repeats == 3
    assert args.baseline is False
    assert args.update_baseline is False


def test_rag_answer_defaults_to_repeated_judging() -> None:
    args = _build_parser().parse_args(["rag-answer"])

    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.judge_repeats == 3


def test_memory_parser_has_reasoner_judge_and_baseline_args() -> None:
    args = _build_parser().parse_args(["memory"])

    assert args.reasoner_model is None
    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.judge_repeats == 3
    assert args.baseline is False
    assert args.update_baseline is False


def test_memory_pipeline_parser_has_baseline_args() -> None:
    args = _build_parser().parse_args(["memory-pipeline"])

    assert args.baseline is False
    assert args.update_baseline is False


def test_process_parser_requires_captured_report() -> None:
    args = _build_parser().parse_args(["process", "--report", "capture.json", "--baseline"])

    assert args.report.name == "capture.json"
    assert args.baseline is True


def test_baseline_import_parser_requires_report() -> None:
    args = _build_parser().parse_args(["baseline-import", "--report", "report.json"])

    assert args.report.name == "report.json"


def test_load_baseline_report_accepts_valid_fingerprinted_payload(tmp_path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "command": "multi-turn",
                "valid": True,
                "stats": {"faithfulness": 1.0},
                "run_metadata": FINGERPRINTS,
            }
        ),
        encoding="utf-8",
    )

    assert _load_baseline_report(report)["command"] == "multi-turn"


def test_load_baseline_report_normalizes_successful_e2e_v1_metadata(tmp_path) -> None:
    report = tmp_path / "e2e.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "command": "e2e-smoke",
                "stats": {"upload_to_ready": 100.0},
                **FINGERPRINTS,
                "assertions": {
                    "answer_has_citation": True,
                    "answer_has_expected_fact": True,
                    "dead_letter_jobs": 0,
                    "ingest_required_spans_ok": True,
                    "ingest_terminal_success": True,
                    "readiness_checks_ok": True,
                    "source_identity_ok": True,
                    "terminal_done": True,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = _load_baseline_report(report)

    assert payload["run_metadata"] == FINGERPRINTS


def test_load_baseline_report_rejects_failed_e2e_assertion(tmp_path) -> None:
    report = tmp_path / "e2e.json"
    report.write_text(
        json.dumps(
            {
                "command": "e2e-smoke",
                "stats": {"upload_to_ready": 100.0},
                **FINGERPRINTS,
                "assertions": {"answer_has_citation": False, "dead_letter_jobs": 0},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="failed or missing assertions"):
        _load_baseline_report(report)


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "command": "multi-turn",
                "stats": {"faithfulness": 1.0},
                "run_metadata": FINGERPRINTS,
            },
            "must declare valid=true",
        ),
        (
            {
                "command": "multi-turn",
                "valid": False,
                "stats": {"faithfulness": 1.0},
                "run_metadata": FINGERPRINTS,
            },
            "must declare valid=true",
        ),
        (
            {
                "command": "rag-answer",
                "valid": True,
                "stats": {"faithfulness": 1.0},
                "run_metadata": {
                    "dataset_fingerprint_sha256": "dataset",
                    "harness_fingerprint_sha256": "harness",
                },
            },
            "implementation_fingerprint_sha256",
        ),
        (
            {
                "command": "rag-answer",
                "stats": {"faithfulness": 1.0},
                "run_metadata": FINGERPRINTS,
            },
            "must declare valid=true",
        ),
    ],
)
def test_load_baseline_report_rejects_invalid_or_unfingerprinted_payload(
    tmp_path, payload, message
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _load_baseline_report(report)


@dataclass
class _FakeSpanStats:
    p95: float

    def to_dict(self) -> dict[str, float]:
        return {"p95": self.p95}


def test_baseline_payload_is_selected_by_command() -> None:
    baseline = {
        "schema_version": 2,
        "payloads": [
            {"command": "chat", "stats": {"pipeline": {"p95": 100.0}}},
            {"command": "ingest", "stats": {"pipeline": {"p95": 200.0}}},
        ],
    }

    selected = _baseline_payload_for(baseline, "ingest")

    assert selected is not None
    assert selected["stats"]["pipeline"]["p95"] == 200.0


def test_baseline_compare_handles_live_span_stats_and_detects_latency_regression() -> None:
    current = {
        "command": "chat",
        "run_metadata": FINGERPRINTS,
        "stats": {"pipeline": _FakeSpanStats(p95=130.0)},
    }
    baseline = {
        "command": "chat",
        "run_metadata": FINGERPRINTS,
        "stats": {"pipeline": {"p95": 100.0}},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.20)

    assert regressions == ["stats.pipeline.p95: 130.000000 > baseline 100.000000 (+30.0%)"]


def test_baseline_compare_detects_quality_regression() -> None:
    current = {
        "command": "rag-retrieval",
        "run_metadata": FINGERPRINTS,
        "stats": {"hybrid@10": {"recall": 0.70, "mrr": 0.65, "ndcg": 0.62}},
    }
    baseline = {
        "command": "rag-retrieval",
        "run_metadata": FINGERPRINTS,
        "stats": {"hybrid@10": {"recall": 0.90, "mrr": 0.80, "ndcg": 0.75}},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.10)

    assert any(item.startswith("stats.hybrid@10.recall") for item in regressions)
    assert any(item.startswith("stats.hybrid@10.mrr") for item in regressions)
    assert any(item.startswith("stats.hybrid@10.ndcg") for item in regressions)


def test_baseline_compare_detects_new_judge_parse_retries() -> None:
    current = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "stats": {"judge_parse_retries": 1},
    }
    baseline = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "stats": {"judge_parse_retries": 0},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.10)

    assert regressions == ["stats.judge_parse_retries: 1.000000 > baseline 0.000000 (from zero)"]


def test_benchmark_source_artifact_is_reproducible_and_excludes_runtime_ids() -> None:
    artifact = _benchmark_source_artifact(
        {
            "meeting_id": 123,
            "file_id": 456,
            "file_name": "sample.pdf",
            "chunk_index": 1,
            "content": "synthetic evidence",
            "image_path": "/private/tmp/secret-location.png",
        }
    )

    assert artifact["file_name"] == "sample.pdf"
    assert artifact["content"] == "synthetic evidence"
    assert len(artifact["content_sha256"]) == 64
    assert "meeting_id" not in artifact
    assert "file_id" not in artifact
    assert "image_path" not in artifact


def test_baseline_compare_rejects_no_comparable_metrics() -> None:
    with pytest.raises(ValueError, match="no comparable metrics"):
        _compare_baseline(
            {"command": "micro", "note": "new"},
            {"command": "micro", "note": "old"},
            threshold=0.10,
        )


def test_baseline_compare_flags_judge_drift() -> None:
    current = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "judge_config": {"model": "judge-b"},
        "stats": {"faithfulness": 0.9},
    }
    baseline = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "judge_config": {"model": "judge-a"},
        "stats": {"faithfulness": 0.9},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.10)

    assert regressions[0].startswith("judge_config drift:")


def test_baseline_compare_flags_dataset_drift() -> None:
    current = {
        "command": "chat",
        "run_metadata": {**FINGERPRINTS, "dataset_fingerprint_sha256": "new"},
        "stats": {"pipeline": {"p95": 100.0}},
    }
    baseline = {
        "command": "chat",
        "run_metadata": {**FINGERPRINTS, "dataset_fingerprint_sha256": "old"},
        "stats": {"pipeline": {"p95": 100.0}},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.10)

    assert regressions[0].startswith("dataset_fingerprint_sha256 drift:")


def test_baseline_compare_fails_closed_on_missing_metric() -> None:
    current = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "judge_config": {"model": "judge"},
        "stats": {"recall": 0.9},
    }
    baseline = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "judge_config": {"model": "judge"},
        "stats": {"recall": 0.9, "faithfulness": 0.95},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.05)

    assert "stats.faithfulness missing from current benchmark" in regressions


def test_baseline_compare_fails_closed_on_non_finite_metric() -> None:
    current = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "stats": {"recall": float("nan")},
    }
    baseline = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "stats": {"recall": 0.9},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.05)

    assert regressions == ["stats.recall is non-finite in current benchmark: nan"]


def test_baseline_compare_detects_negative_signed_gain_regression() -> None:
    current = {
        "command": "memory",
        "run_metadata": FINGERPRINTS,
        "stats": {"memory_gain": -0.9},
    }
    baseline = {
        "command": "memory",
        "run_metadata": FINGERPRINTS,
        "stats": {"memory_gain": -0.1},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.05)

    assert len(regressions) == 1
    assert regressions[0].startswith("stats.memory_gain: -0.900000 < baseline -0.100000")


def test_baseline_compare_fails_closed_when_judge_config_disappears() -> None:
    current = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "stats": {"faithfulness": 0.9},
    }
    baseline = {
        "command": "rag-answer",
        "run_metadata": FINGERPRINTS,
        "judge_config": {"model": "judge"},
        "stats": {"faithfulness": 0.9},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.05)

    assert regressions == ["judge_config missing: baseline=True current=False"]


def test_baseline_compare_fails_closed_when_fingerprint_is_missing() -> None:
    current = {
        "command": "chat",
        "run_metadata": {
            "dataset_fingerprint_sha256": "dataset",
            "harness_fingerprint_sha256": "harness",
            "implementation_fingerprint_sha256": "implementation",
        },
        "stats": {"pipeline": {"p95": 100.0}},
    }
    baseline = {
        "command": "chat",
        "run_metadata": {
            "dataset_fingerprint_sha256": "dataset",
            "harness_fingerprint_sha256": "harness",
        },
        "stats": {"pipeline": {"p95": 100.0}},
    }

    regressions = _compare_baseline(current, baseline, threshold=0.10)

    assert regressions == ["implementation_fingerprint_sha256 missing: baseline=False current=True"]


def test_tree_fingerprint_is_stable_and_content_sensitive(tmp_path) -> None:
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    fixture = dataset_dir / "cases.json"
    fixture.write_text('{"version": 1}', encoding="utf-8")

    first = _tree_fingerprint([dataset_dir])
    second = _tree_fingerprint([dataset_dir])
    fixture.write_text('{"version": 2}', encoding="utf-8")
    changed = _tree_fingerprint([dataset_dir])

    assert first == second
    assert first != changed


def test_tree_fingerprint_preserves_relative_paths_for_same_named_files(tmp_path) -> None:
    first = tmp_path / "first" / "index.ts"
    second = tmp_path / "second" / "index.ts"
    renamed = tmp_path / "second" / "renamed.ts"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    renamed.write_text("same", encoding="utf-8")

    together = _tree_fingerprint([first, second], root=tmp_path)
    renamed_tree = _tree_fingerprint([first, renamed], root=tmp_path)

    assert together != renamed_tree


def test_rag_all_preserves_quality_payload_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.benchmark.run_rag_retrieval_benchmark",
        lambda _args: {
            "stats": {"hybrid@10": {"recall": 1.0}},
            "rag_quality": {"retrieval": {"stats": {"hybrid@10": {"recall": 1.0}}}},
        },
    )
    monkeypatch.setattr(
        "scripts.benchmark.run_rag_answer_benchmark",
        lambda _args: {
            "stats": {"faithfulness": 1.0},
            "judge_config": {"model": "judge"},
            "rag_quality": {"answer": {"stats": {"faithfulness": 1.0}}},
        },
    )
    monkeypatch.setattr(
        "scripts.benchmark.run_rag_snapshot_benchmark",
        lambda _args: {"stats": {"diffs": 0}, "snapshot_diffs": []},
    )

    payload = run_rag_all_benchmark(object())

    assert payload["rag_quality"]["retrieval"]["stats"]["hybrid@10"]["recall"] == 1.0
    assert payload["rag_quality"]["answer"]["stats"]["faithfulness"] == 1.0


def test_baseline_updates_preserve_independent_command_runs() -> None:
    metadata = {
        "dataset_fingerprint_sha256": "dataset-v1",
        "harness_fingerprint_sha256": "harness-v1",
        "implementation_fingerprint_sha256": "implementation-v1",
    }
    existing = {
        "payloads": [{"command": "chat", "run_metadata": metadata, "stats": {"pipeline": {}}}]
    }

    merged = _build_baseline_document(
        [{"command": "rag-answer", "run_metadata": metadata, "stats": {"faithfulness": 1}}],
        existing,
    )
    drifted = _build_baseline_document(
        [
            {
                "command": "rag-answer",
                "run_metadata": {**metadata, "dataset_fingerprint_sha256": "dataset-v2"},
                "stats": {"faithfulness": 1},
            }
        ],
        existing,
    )

    assert merged["schema_version"] == 3
    assert [item["command"] for item in merged["payloads"]] == ["chat", "rag-answer"]
    assert [item["command"] for item in drifted["payloads"]] == ["chat", "rag-answer"]
    assert drifted["payloads"][0]["run_metadata"]["dataset_fingerprint_sha256"] == "dataset-v1"


def test_chat_runner_decodes_fixture_dates_before_calling_chain(tmp_path, monkeypatch):
    from argparse import Namespace
    from datetime import date

    import scripts._bench_fixtures as fixtures
    import scripts.benchmark as benchmark
    import src.services.chain as chain

    query_file = tmp_path / "dated-queries.json"
    query_file.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "Summarize January meetings",
                        "date_from": "2026-01-01",
                        "date_to": "2026-01-31",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(benchmark, "QUERIES_PATH", query_file)

    async def ingest(_names):
        return {"sample.pdf": (1, 1)}

    async def stream(**kwargs):
        assert kwargs["date_from"] == date(2026, 1, 1)
        assert kwargs["date_to"] == date(2026, 1, 31)
        assert kwargs["retrieval_profile"] == "fast"
        yield {"type": "token", "content": "Evidence"}
        yield {"type": "trace", "trace": {"spans": []}}
        yield {"type": "done", "session_id": "benchmark"}

    monkeypatch.setattr(fixtures, "ingest_fixtures", ingest)
    monkeypatch.setattr(chain, "ask_stream", stream)
    result = benchmark.run_chat_benchmark(Namespace(iterations=1, profile="fast"))
    assert result["trace_count"] == 1
    assert result["retrieval_profile"] == "fast"
    assert result["answer_quality"] is None
    assert result["answer_quality_evaluated_count"] == 0
    assert result["answer_quality_skipped_count"] == 1
    assert result["category_stats"]["uncategorized"]["samples"] == 1
    assert result["degraded_rate"] == 0
    assert result["performance_gate"]["passed"] is True
    assert result["model_config"]["model"]


def test_declared_corpus_does_not_default_to_expected_answer_files() -> None:
    import scripts.benchmark as benchmark

    item = {"id": "q1", "expected_files": ["answer.pdf"]}
    corpus = benchmark._declared_corpus_files(item, ["answer.pdf", "distractor.pdf"])
    assert corpus == ["answer.pdf", "distractor.pdf"]


def test_declared_corpus_rejects_expected_file_outside_scope() -> None:
    import pytest

    import scripts.benchmark as benchmark

    item = {
        "id": "q1",
        "expected_files": ["answer.pdf"],
        "corpus_files": ["distractor.pdf"],
    }
    with pytest.raises(ValueError, match="expected file is outside corpus"):
        benchmark._declared_corpus_files(item, ["answer.pdf", "distractor.pdf"])
