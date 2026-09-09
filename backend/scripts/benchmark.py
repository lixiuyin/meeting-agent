"""End-to-end performance benchmark harness for meeting-agent.

Usage:
    uv run python -m scripts.benchmark chat --iterations 10
    uv run python -m scripts.benchmark ingest --iterations 5
    uv run python -m scripts.benchmark micro
    uv run python -m scripts.benchmark all
    uv run python -m scripts.benchmark chat --baseline
    uv run python -m scripts.benchmark chat --update-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from scripts._bench_rag_judge import DEFAULT_JUDGE_MODEL

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = SCRIPTS_DIR.parent.resolve()
REPO_DIR = BACKEND_DIR.parent.resolve()
RESULTS_DIR = BACKEND_DIR / "benchmark-results"
EVALUATION_DIR = BACKEND_DIR / "evaluation"
BASELINE_PATH = EVALUATION_DIR / "baselines" / "current.json"
RAG_SNAPSHOTS_DIR = EVALUATION_DIR / "snapshots"
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures" / "benchmark"
QUERIES_PATH = FIXTURE_DIR / "queries.json"
BASELINE_SCHEMA_VERSION = 3


def _declared_corpus_files(item: dict, available_files: list[str]) -> list[str]:
    """Return generator-visible files without leaking evaluator-only labels."""
    corpus = item.get("corpus_files") or available_files
    if (
        not isinstance(corpus, list)
        or not corpus
        or not all(isinstance(name, str) and name in available_files for name in corpus)
    ):
        raise ValueError(f"{item.get('id', '<unknown>')}: invalid declared corpus_files")
    expected = item.get("expected_files") or []
    if any(name not in corpus for name in expected):
        raise ValueError(f"{item.get('id', '<unknown>')}: expected file is outside corpus")
    return list(dict.fromkeys(corpus))


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------


def _load_baseline() -> dict:
    if BASELINE_PATH.exists():
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _baseline_payload_for(baseline: dict, command: str) -> dict | None:
    """Return the stored payload for *command* from v2 or legacy baselines."""
    payloads = baseline.get("payloads")
    if isinstance(payloads, list):
        matches = [item for item in payloads if item.get("command") == command]
        if len(matches) > 1:
            raise ValueError(f"baseline contains duplicate payloads for command {command!r}")
        return matches[0] if matches else None
    if baseline.get("command") == command:
        return baseline
    return None


def _build_baseline_document(payloads: list[dict], existing: dict | None = None) -> dict:
    """Replace matching commands while preserving independent command baselines."""
    serialized = _json_ready(payloads)
    assert isinstance(serialized, list)
    merged: dict[str, dict] = {}

    existing_payloads = (existing or {}).get("payloads", [])
    if isinstance(existing_payloads, list):
        merged.update(
            {
                item["command"]: item
                for item in existing_payloads
                if isinstance(item, dict) and item.get("command")
            }
        )

    merged.update(
        {
            item["command"]: item
            for item in serialized
            if isinstance(item, dict) and item.get("command")
        }
    )
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "payloads": [merged[key] for key in sorted(merged)],
    }


def _load_baseline_report(path: Path) -> dict:
    """Load one completed report for baseline archival without rerunning providers."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read benchmark report {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("benchmark report must be a JSON object")
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("benchmark report is missing command")
    if not isinstance(payload.get("stats"), dict) or not payload["stats"]:
        raise ValueError("benchmark report is missing non-empty stats")
    if (
        command in {"multi-turn", "memory", "rag-retrieval", "rag-answer"}
        and payload.get("valid") is not True
    ):
        raise ValueError(f"benchmark report for {command!r} must declare valid=true")
    if "valid" in payload and payload.get("valid") is not True:
        raise ValueError(f"benchmark report for {command!r} is not valid")

    if command == "e2e-smoke":
        assertions = payload.get("assertions")
        required_true = (
            "answer_has_citation",
            "answer_has_expected_fact",
            "ingest_required_spans_ok",
            "ingest_terminal_success",
            "readiness_checks_ok",
            "source_identity_ok",
            "terminal_done",
        )
        if not isinstance(assertions, dict) or any(
            assertions.get(name) is not True for name in required_true
        ):
            raise ValueError("e2e-smoke report has failed or missing assertions")
        if assertions.get("dead_letter_jobs") != 0:
            raise ValueError("e2e-smoke report contains dead-letter jobs")

    metadata = payload.get("run_metadata")
    if not isinstance(metadata, dict) and command == "e2e-smoke":
        # Playwright's v1 report predates the shared benchmark envelope and
        # stores provenance at the top level.  Normalize it on import while
        # preserving the original fields for existing report consumers.
        metadata = {
            key: payload.get(key)
            for key in (
                "captured_at",
                "source_revision",
                "dataset_fingerprint_sha256",
                "harness_fingerprint_sha256",
                "implementation_fingerprint_sha256",
            )
            if key in payload
        }
        payload["run_metadata"] = metadata
    if not isinstance(metadata, dict):
        raise ValueError("benchmark report is missing run_metadata")
    missing = [
        key
        for key in (
            "dataset_fingerprint_sha256",
            "harness_fingerprint_sha256",
            "implementation_fingerprint_sha256",
        )
        if not metadata.get(key)
    ]
    if missing:
        raise ValueError("benchmark report is missing fingerprints: " + ", ".join(missing))
    return payload


def _as_mapping(value: object) -> dict:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return result if isinstance(result, dict) else {}
    return {}


def _json_ready(value: object) -> object:
    """Recursively convert benchmark dataclasses into JSON-safe values."""
    mapping = _as_mapping(value)
    if mapping:
        return {str(key): _json_ready(child) for key, child in mapping.items()}
    if isinstance(value, list):
        return [_json_ready(child) for child in value]
    if isinstance(value, tuple):
        return [_json_ready(child) for child in value]
    return value


def _tree_fingerprint(paths: list[Path], *, root: Path = BACKEND_DIR) -> str:
    """Hash file names and bytes deterministically, including untracked files."""
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and "__pycache__" not in candidate.parts
                and candidate.suffix not in {".pyc", ".pyo"}
            )
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = Path(path.name)
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _capture_e2e_fingerprints() -> dict[str, str]:
    """Fingerprint the isolated full-stack smoke inputs and implementation."""
    dataset = [BACKEND_DIR / "tests" / "fixtures" / "benchmark" / "e2e-smoke.txt"]
    harness = [
        REPO_DIR / "frontend" / "e2e" / "full-stack" / "upload-and-chat.spec.ts",
        REPO_DIR / "frontend" / "e2e" / "full-stack" / "message-lifecycle.spec.ts",
        REPO_DIR / "scripts" / "run-isolated-e2e-smoke.sh",
        REPO_DIR / "Makefile",
        BACKEND_DIR / "scripts" / "production_holdout_benchmark.py",
        BACKEND_DIR / "scripts" / "production_pipeline_benchmark.py",
        BACKEND_DIR / "scripts" / "business_review.py",
        BACKEND_DIR / "scripts" / "build_production_quality_evidence.py",
        BACKEND_DIR / "scripts" / "check_release_readiness.py",
        BACKEND_DIR / "scripts" / "_holdout_identity.py",
    ]
    implementation = [
        BACKEND_DIR / "src",
        BACKEND_DIR / "skills",
        BACKEND_DIR / "config" / "main.yaml",
        BACKEND_DIR / "pyproject.toml",
        BACKEND_DIR / "uv.lock",
        REPO_DIR / "frontend" / "src",
        REPO_DIR / "frontend" / "vite.config.ts",
        REPO_DIR / "frontend" / "package.json",
        REPO_DIR / "frontend" / "package-lock.json",
        REPO_DIR / "frontend" / "nginx.conf",
    ]
    return {
        "dataset_fingerprint_sha256": _tree_fingerprint(dataset, root=REPO_DIR),
        "harness_fingerprint_sha256": _tree_fingerprint(harness, root=REPO_DIR),
        "implementation_fingerprint_sha256": _tree_fingerprint(
            implementation,
            root=REPO_DIR,
        ),
    }


def _git_text(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=BACKEND_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _capture_run_metadata(args: argparse.Namespace) -> dict:
    """Capture non-secret provenance needed to reproduce a score record."""
    benchmark_sources = [path for path in SCRIPTS_DIR.glob("_bench*.py") if path.is_file()] + [
        Path(__file__).resolve()
    ]
    # Deliberately exclude generated baselines/snapshots: including outputs in
    # the input fingerprint would make every freshly written baseline drift on
    # its very next comparison.
    datasets = [
        FIXTURE_DIR,
        EVALUATION_DIR / "protocol.json",
        EVALUATION_DIR / "datasets",
    ]
    implementation_sources = [
        BACKEND_DIR / "src",
        BACKEND_DIR / "skills",
        BACKEND_DIR / "config" / "main.yaml",
        BACKEND_DIR / "pyproject.toml",
        BACKEND_DIR / "uv.lock",
    ]
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "argv": [str(item) for item in sys.argv[1:]],
        "arguments": {key: str(value) for key, value in sorted(vars(args).items())},
        "source_revision": _git_text("rev-parse", "HEAD"),
        "dataset_fingerprint_sha256": _tree_fingerprint(datasets),
        "harness_fingerprint_sha256": _tree_fingerprint(benchmark_sources),
        "implementation_fingerprint_sha256": _tree_fingerprint(implementation_sources),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }


def _assess_quality_evidence(
    *,
    observed_cases: int,
    dataset_kind: str,
    judge_repeats: int | None = None,
    same_model_as_system: bool | None = None,
    reranker_expected: bool = False,
    reranker_evaluated: int | None = None,
) -> dict:
    """Describe whether one valid benchmark is strong enough for a release claim.

    Benchmark validity and evidence strength are deliberately separate: a
    small synthetic run can be internally valid while still being unsuitable
    as the sole production-quality gate.
    """
    limitations: list[str] = []
    if dataset_kind != "production_holdout":
        limitations.append("dataset_is_not_a_production_holdout")
    if observed_cases < 30:
        limitations.append("fewer_than_30_cases")
    if judge_repeats is not None and judge_repeats < 3:
        limitations.append("fewer_than_3_judge_repeats")
    if same_model_as_system:
        limitations.append("judge_uses_the_system_model")
    if reranker_expected and (reranker_evaluated or 0) < observed_cases:
        limitations.append("reranker_not_executed_for_every_query")
    return {
        "grade": "release_candidate" if not limitations else "diagnostic",
        "release_ready": not limitations,
        "dataset_kind": dataset_kind,
        "observed_cases": observed_cases,
        "judge_repeats": judge_repeats,
        "reranker_evaluated_queries": reranker_evaluated,
        "limitations": limitations,
    }


_LOWER_IS_BETTER = {"diffs", "judge_parse_retries", "parse_failures", "p95"}
_HIGHER_IS_BETTER = {
    "accurate_retrieval",
    "answer_relevance",
    "answer_similarity",
    "appropriateness",
    "citation_quality",
    "completeness",
    "context_precision",
    "context_recall",
    "corpus_isolation",
    "correctness",
    "distractor_control_margin",
    "evidence_recall",
    "artifact_coverage",
    "faithfulness",
    "first_error_accuracy",
    "long_range_understanding",
    "memory_gain",
    "mrr",
    "naturalness",
    "ndcg",
    "phase1_combined_recall",
    "phase2_weighted_score",
    "precision",
    "recall",
    "rouge_l_f1",
    "source_identity_recall",
    "selective_forgetting",
    "session_continuity",
    "step_accuracy",
    "test_time_learning",
    "terminal_accuracy",
    "weighted_score",
}


def _comparable_metrics(payload: dict) -> dict[str, tuple[float, str]]:
    """Flatten explicitly supported benchmark metrics and their direction."""
    metrics: dict[str, tuple[float, str]] = {}

    def _walk(value: object, path: tuple[str, ...]) -> None:
        mapping = _as_mapping(value)
        if mapping:
            for key, child in mapping.items():
                _walk(child, (*path, str(key)))
            return
        if not path or isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        leaf = path[-1]
        if leaf in _LOWER_IS_BETTER:
            metrics[".".join(path)] = (float(value), "lower")
        elif leaf in _HIGHER_IS_BETTER:
            metrics[".".join(path)] = (float(value), "higher")

    _walk(payload.get("stats", {}), ("stats",))
    return metrics


def _compare_baseline(current: dict, baseline: dict, threshold: float) -> list[str]:
    """Compare one current payload with its command-matched baseline payload."""
    if threshold < 0:
        raise ValueError("regression threshold must be non-negative")

    regressions: list[str] = []
    current_metadata = current.get("run_metadata", {})
    baseline_metadata = baseline.get("run_metadata", {})
    for fingerprint in (
        "dataset_fingerprint_sha256",
        "harness_fingerprint_sha256",
        "implementation_fingerprint_sha256",
    ):
        current_value = current_metadata.get(fingerprint)
        baseline_value = baseline_metadata.get(fingerprint)
        if not current_value or not baseline_value:
            regressions.append(
                f"{fingerprint} missing: baseline={bool(baseline_value)} "
                f"current={bool(current_value)}"
            )
        elif fingerprint != "implementation_fingerprint_sha256" and current_value != baseline_value:
            regressions.append(
                f"{fingerprint} drift: baseline={baseline_value} current={current_value}"
            )
    base_judge = baseline.get("judge_config")
    cur_judge = current.get("judge_config")
    if bool(base_judge) != bool(cur_judge):
        regressions.append(
            f"judge_config missing: baseline={bool(base_judge)} current={bool(cur_judge)}"
        )
    elif base_judge and cur_judge and base_judge != cur_judge:
        regressions.append(f"judge_config drift: baseline={base_judge} current={cur_judge}")

    current_metrics = _comparable_metrics(current)
    baseline_metrics = _comparable_metrics(baseline)
    for metric in sorted(baseline_metrics.keys() - current_metrics.keys()):
        regressions.append(f"{metric} missing from current benchmark")
    shared = sorted(current_metrics.keys() & baseline_metrics.keys())
    if not shared:
        raise ValueError(
            f"no comparable metrics for command {current.get('command', '<unknown>')!r}"
        )

    for metric in shared:
        current_value, direction = current_metrics[metric]
        baseline_value, baseline_direction = baseline_metrics[metric]
        if not math.isfinite(current_value):
            regressions.append(f"{metric} is non-finite in current benchmark: {current_value}")
            continue
        if not math.isfinite(baseline_value):
            regressions.append(f"{metric} is non-finite in baseline benchmark: {baseline_value}")
            continue
        if direction != baseline_direction:
            raise ValueError(f"metric direction mismatch for {metric}")
        if direction == "lower":
            regressed = (
                current_value > 0
                if baseline_value == 0
                else current_value > baseline_value * (1 + threshold)
            )
            if not regressed:
                continue
            change = (
                "from zero"
                if baseline_value == 0
                else f"+{(current_value / baseline_value - 1) * 100:.1f}%"
            )
            regressions.append(
                f"{metric}: {current_value:.6f} > baseline {baseline_value:.6f} ({change})"
            )
        elif direction == "higher":
            # Relative thresholds are meaningful for positive scores. Signed
            # gains need an absolute threshold; otherwise every negative
            # baseline silently bypasses the release gate.
            floor = (
                baseline_value * (1 - threshold)
                if baseline_value > 0
                else baseline_value - threshold
            )
            if current_value < floor:
                change = (
                    f"{(current_value / baseline_value - 1) * 100:.1f}%"
                    if baseline_value > 0
                    else f"absolute {current_value - baseline_value:+.6f}"
                )
                regressions.append(
                    f"{metric}: {current_value:.6f} < baseline {baseline_value:.6f} ({change})"
                )

    return regressions


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def _write_report(name: str, payload: dict) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat().replace(":", "-").replace("+", "_")
    payload["timestamp"] = ts
    payload["name"] = name

    from ._bench_aggregate import (
        SpanStats,
        format_evidence_quality_markdown,
        format_markdown,
    )

    class _Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, SpanStats):
                return o.to_dict()
            return super().default(o)

    md_path = RESULTS_DIR / f"{name}_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        if payload.get("phase") in (1, 2):
            from ._bench_aggregate import format_chunk_benchmark_markdown

            f.write(format_chunk_benchmark_markdown(payload))
        elif "phase1" in payload and "phase2" in payload:
            # Summary for rag-chunk-full
            p1 = payload["phase1"]
            p2 = payload["phase2"]
            from ._bench_aggregate import format_chunk_benchmark_markdown

            f.write("# RAG Chunk Benchmark — Full Run\n\n")
            f.write(format_chunk_benchmark_markdown(p1))
            f.write("\n")
            f.write(format_chunk_benchmark_markdown(p2))
        else:
            f.write(format_markdown(payload.get("stats", {})))
        if payload.get("category_stats"):
            from ._bench_aggregate import format_chat_performance_markdown

            f.write("\n")
            f.write(format_chat_performance_markdown(payload))
        if payload.get("evidence_quality"):
            f.write("\n")
            f.write(format_evidence_quality_markdown(payload["evidence_quality"]))
        if payload.get("rag_quality"):
            from ._bench_aggregate import format_rag_quality_markdown

            f.write("\n")
            f.write(format_rag_quality_markdown(payload["rag_quality"]))
        if payload.get("snapshot_diffs"):
            f.write("\n## Snapshot Diffs\n")
            f.write(json.dumps(payload["snapshot_diffs"], indent=2))
            f.write("\n")

    json_path = RESULTS_DIR / f"{name}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, cls=_Encoder)

    return json_path, md_path


# ---------------------------------------------------------------------------
# Chat benchmark
# ---------------------------------------------------------------------------


def run_chat_benchmark(args: argparse.Namespace) -> dict:
    """Run chat pipeline benchmark and return result payload."""

    from ._bench_aggregate import _percentile, aggregate
    from ._bench_env import bench_environment

    with bench_environment():
        # Safe to import src.* now
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask_stream

        from ._bench_fixtures import ingest_fixtures
        from ._bench_rag_judge import get_llm_runtime_config

        close_all_connections()
        model_config = get_llm_runtime_config()
        with open(QUERIES_PATH, encoding="utf-8") as f:
            queries_data = json.load(f)
        queries = queries_data.get("queries", [])

        traces: list[dict] = []
        runs: list[dict] = []
        profile = getattr(args, "profile", "balanced")
        order_seed = getattr(args, "order_seed", 0)
        work_items = [
            (query_item, iteration)
            for query_item in queries
            for iteration in range(args.iterations)
        ]
        random.Random(order_seed).shuffle(work_items)

        async def _run() -> None:
            init_db()
            fixture_info = await ingest_fixtures(["sample.pdf", "sample.pptx"])
            meeting_ids = list({mid for mid, _ in fixture_info.values()})
            for query_item, iteration in work_items:
                q = query_item["query"]
                file_types = query_item.get("file_types")
                date_from = (
                    date.fromisoformat(query_item["date_from"])
                    if query_item.get("date_from")
                    else None
                )
                date_to = (
                    date.fromisoformat(query_item["date_to"]) if query_item.get("date_to") else None
                )
                started = time.perf_counter()
                first_token_ms: float | None = None
                trace: dict | None = None
                completed = False
                degraded = False
                sources: list[dict] = []
                async for event in ask_stream(
                    question=q,
                    user_id="benchmark",
                    meeting_ids=meeting_ids,
                    file_types=file_types,
                    date_from=date_from,
                    date_to=date_to,
                    retrieval_profile=profile,
                ):
                    event_type = event.get("type")
                    if (
                        event_type == "token"
                        and str(event.get("content") or "").strip()
                        and first_token_ms is None
                    ):
                        first_token_ms = (time.perf_counter() - started) * 1000
                    elif event_type == "done":
                        completed = True
                    elif event_type == "status" and event.get("status") == "degraded":
                        degraded = True
                    elif event_type == "sources":
                        sources = event.get("items") or []
                    elif event_type == "trace":
                        trace = event.get("trace")
                    elif event_type == "error":
                        raise RuntimeError(
                            f"chat benchmark stream failed: {event.get('code', 'unknown')}"
                        )
                total_ms = (time.perf_counter() - started) * 1000
                if not completed:
                    raise RuntimeError("chat benchmark stream ended without done")
                if first_token_ms is None:
                    raise RuntimeError("chat benchmark stream completed without a visible token")
                if trace is None:
                    raise RuntimeError("chat benchmark stream completed without a trace event")
                trace.setdefault("spans", []).extend(
                    [
                        {
                            "label": "chat_ttft",
                            "phase": "client",
                            "duration_ms": first_token_ms,
                            "status": "success",
                        },
                        {
                            "label": "chat_total",
                            "phase": "client",
                            "duration_ms": total_ms,
                            "status": "success",
                        },
                    ]
                )
                traces.append(trace)
                degraded = degraded or any(
                    span.get("status") == "degraded"
                    and span.get("label") in {"llm_ttft", "llm_streaming"}
                    for span in trace.get("spans", [])
                )
                runs.append(
                    {
                        "query_id": query_item.get("id"),
                        "category": query_item.get("category", "uncategorized"),
                        "iteration": iteration + 1,
                        "query": q,
                        "degraded": degraded,
                        "source_count": len(sources),
                        "ttft_ms": first_token_ms,
                        "total_ms": total_ms,
                    }
                )

        asyncio.run(_run())

    stats = aggregate(traces)
    category_stats: dict[str, dict] = {}
    for category in sorted({str(run["category"]) for run in runs}):
        selected = [run for run in runs if run["category"] == category]
        ttft = sorted(float(run["ttft_ms"]) for run in selected)
        total = sorted(float(run["total_ms"]) for run in selected)
        degraded_count = sum(bool(run["degraded"]) for run in selected)
        category_stats[category] = {
            "samples": len(selected),
            "ttft_p50_ms": round(_percentile(ttft, 50), 2),
            "ttft_p95_ms": round(_percentile(ttft, 95), 2),
            "total_p50_ms": round(_percentile(total, 50), 2),
            "total_p95_ms": round(_percentile(total, 95), 2),
            "degraded_count": degraded_count,
            "degraded_rate": degraded_count / len(selected),
        }
    degraded_rate = sum(run["degraded"] for run in runs) / len(runs) if runs else None
    max_degraded_rate = getattr(args, "max_degraded_rate", 0.05)
    max_ttft_p95_ms = getattr(args, "max_ttft_p95_ms", 3000.0)
    max_total_p95_ms = getattr(args, "max_total_p95_ms", 5000.0)
    ttft_p95 = stats.get("chat_ttft").p95 if stats.get("chat_ttft") else None
    total_p95 = stats.get("chat_total").p95 if stats.get("chat_total") else None
    performance_gate_passed = bool(
        degraded_rate is not None
        and degraded_rate <= max_degraded_rate
        and ttft_p95 is not None
        and ttft_p95 <= max_ttft_p95_ms
        and total_p95 is not None
        and total_p95 <= max_total_p95_ms
    )
    payload = {
        "command": "chat",
        "iterations": args.iterations,
        "queries_count": len(queries),
        "trace_count": len(traces),
        "retrieval_profile": profile,
        "order_seed": order_seed,
        "model_config": model_config,
        "runs": runs,
        "category_stats": category_stats,
        "degraded_count": sum(run["degraded"] for run in runs),
        "degraded_rate": degraded_rate,
        "performance_gate": {
            "passed": performance_gate_passed,
            "enforced": bool(getattr(args, "enforce_slo", False)),
            "thresholds": {
                "max_degraded_rate": max_degraded_rate,
                "max_ttft_p95_ms": max_ttft_p95_ms,
                "max_total_p95_ms": max_total_p95_ms,
            },
            "observed": {
                "degraded_rate": degraded_rate,
                "ttft_p95_ms": ttft_p95,
                "total_p95_ms": total_p95,
            },
        },
        "answer_quality": None,
        "answer_quality_evaluated_count": 0,
        "answer_quality_skipped_count": len(runs),
        "stats": stats,
    }
    return payload


# ---------------------------------------------------------------------------
# Ingest benchmark
# ---------------------------------------------------------------------------


def run_ingest_benchmark(args: argparse.Namespace) -> dict:
    """Run ingest pipeline benchmark and return result payload."""

    from ._bench_aggregate import aggregate
    from ._bench_env import bench_environment

    fixtures = ["sample.pdf", "scanned.pdf", "sample.pptx"]
    traces: list[dict] = []

    with bench_environment():
        from src.core.database import close_all_connections, init_db

        from ._bench_fixtures import _ingest_fixture_file

        close_all_connections()
        init_db()

        async def _run() -> None:
            for fixture in fixtures:
                for _ in range(args.iterations):
                    _, trace = await _ingest_fixture_file(fixture)
                    traces.append(trace.to_dict())

        asyncio.run(_run())

    stats = aggregate(traces)
    payload = {
        "command": "ingest",
        "iterations": args.iterations,
        "fixtures": fixtures,
        "trace_count": len(traces),
        "stats": stats,
    }
    return payload


# ---------------------------------------------------------------------------
# Micro benchmark
# ---------------------------------------------------------------------------


def run_micro_benchmark(_args: argparse.Namespace) -> dict:
    """Run pytest micro-benchmarks."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "benchmark",
        "--benchmark-json",
        str(RESULTS_DIR / "micro_latest.json"),
    ]
    subprocess.run(cmd, cwd=BACKEND_DIR, check=True)
    return {
        "command": "micro",
        "note": f"See {RESULTS_DIR / 'micro_latest.json'}",
    }


def run_protocol_audit(_args: argparse.Namespace) -> dict:
    """Validate the versioned evaluation contract without external calls."""
    from ._bench_protocol import audit_protocol

    return audit_protocol(EVALUATION_DIR / "protocol.json", backend_dir=BACKEND_DIR)


def run_evidence_governance_benchmark(_args: argparse.Namespace) -> dict:
    """Execute deterministic meeting-evidence policy contracts."""
    from ._bench_evidence_governance import execute_evidence_governance_cases

    dataset_path = EVALUATION_DIR / "datasets" / "evidence_governance_cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    return execute_evidence_governance_cases(dataset)


def _validate_reranker_dataset(dataset: dict) -> None:
    if dataset.get("schema_version") != 1:
        raise ValueError("reranker dataset schema_version must be 1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("reranker dataset must contain cases")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        candidates = case.get("candidates") if isinstance(case, dict) else None
        relevant = case.get("relevant_ids") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("reranker case IDs must be unique non-empty strings")
        seen.add(case_id)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError(f"{case_id}: query is required")
        if not isinstance(candidates, list) or len(candidates) < 12:
            raise ValueError(f"{case_id}: at least 12 candidates are required")
        candidate_ids = [item.get("id") for item in candidates if isinstance(item, dict)]
        if len(set(candidate_ids)) != len(candidates) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("content"), str)
            or not item["content"].strip()
            for item in candidates
        ):
            raise ValueError(f"{case_id}: candidates must have unique IDs and content")
        if (
            not isinstance(relevant, list)
            or not relevant
            or any(item not in candidate_ids for item in relevant)
        ):
            raise ValueError(f"{case_id}: relevant_ids must identify candidates")


def run_reranker_quality_benchmark(_args: argparse.Namespace) -> dict:
    """Measure ranking effect on a controlled pool, separate from production gating."""
    from src.core.config import settings

    from ._bench_rag_quality import mrr, ndcg_at_k

    dataset_path = EVALUATION_DIR / "datasets" / "reranker_cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    _validate_reranker_dataset(dataset)
    binding = str(settings.RERANKER_BINDING or "").strip()
    rows: list[dict] = []
    validity_errors: list[str] = []

    rerank_fn = None
    if binding:
        from src.services.rag._reranker import rerank as rerank_fn

    for case in dataset["cases"]:
        candidates = [
            {
                "content": item["content"],
                "metadata": {"chunk_id": item["id"]},
                "score": 1.0 - index / len(case["candidates"]),
                "score_kind": "relevance",
            }
            for index, item in enumerate(case["candidates"])
        ]
        baseline_ids = [item["metadata"]["chunk_id"] for item in candidates]
        reranked = (
            rerank_fn(case["query"], candidates, top_n=len(candidates))
            if rerank_fn is not None
            else []
        )
        executed = bool(reranked) and all(item.get("reranked") is True for item in reranked)
        if binding and not executed:
            validity_errors.append(f"{case['id']}: configured reranker did not execute")
        reranked_ids = [item.get("metadata", {}).get("chunk_id") for item in reranked]
        relevant = set(case["relevant_ids"])
        rows.append(
            {
                "case_id": case["id"],
                "executed": executed,
                "baseline_mrr": mrr(baseline_ids, relevant),
                "baseline_ndcg_at_10": ndcg_at_k(baseline_ids, relevant, 10),
                "reranked_mrr": mrr(reranked_ids, relevant) if executed else None,
                "reranked_ndcg_at_10": (
                    ndcg_at_k(reranked_ids, relevant, 10) if executed else None
                ),
            }
        )

    def _mean(key: str) -> float | None:
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
        return sum(values) / len(values) if values else None

    evaluated = sum(bool(row.get("executed")) for row in rows)
    baseline_mrr = _mean("baseline_mrr")
    baseline_ndcg = _mean("baseline_ndcg_at_10")
    reranked_mrr = _mean("reranked_mrr")
    reranked_ndcg = _mean("reranked_ndcg_at_10")
    stats = {
        "baseline_mrr": baseline_mrr,
        "baseline_ndcg_at_10": baseline_ndcg,
        "reranked_mrr": reranked_mrr,
        "reranked_ndcg_at_10": reranked_ndcg,
        "mrr_gain": (
            reranked_mrr - baseline_mrr
            if reranked_mrr is not None and baseline_mrr is not None
            else None
        ),
        "ndcg_at_10_gain": (
            reranked_ndcg - baseline_ndcg
            if reranked_ndcg is not None and baseline_ndcg is not None
            else None
        ),
        "evaluated_queries": evaluated,
        "skipped_queries": len(dataset["cases"]) - evaluated,
    }
    return {
        "command": "reranker-quality",
        "valid": not validity_errors,
        "complete": not validity_errors,
        "validity_errors": validity_errors,
        "reranker_binding": binding or None,
        "execution_status": "completed" if binding else "skipped_disabled",
        "evidence_quality": _assess_quality_evidence(
            observed_cases=len(dataset["cases"]),
            dataset_kind="synthetic",
            reranker_expected=True,
            reranker_evaluated=evaluated,
        ),
        "stats": stats,
        "rows": rows,
    }


def run_multi_turn_benchmark(args: argparse.Namespace) -> dict:
    """Run incremental same-session conversations against synthetic fixtures."""

    from ._bench_env import bench_environment
    from ._bench_multi_turn import execute_multi_turn_cases, validate_multi_turn_dataset

    dataset_path = EVALUATION_DIR / "datasets" / "multi_turn_cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_multi_turn_dataset(dataset)

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask
        from src.services.llm import create_llm

        from ._bench_fixtures import ingest_fixtures
        from ._bench_rag_judge import get_judge_config, judge_multi_turn_quality

        judge_llm = create_llm(args.judge_model) if args.judge_model else create_llm()
        judge_config = get_judge_config(args.judge_model)
        print(f"judge_config: {json.dumps(judge_config, indent=2)}")

        close_all_connections()

        async def _run() -> dict:
            init_db()
            fixture_info: dict[str, dict[str, tuple[int, int]]] = {}
            for case in dataset["cases"]:
                case_id = case["id"]
                user_id = f"benchmark-multi-turn-{case_id}"
                fixture_info[case_id] = await ingest_fixtures(
                    case["fixture_files"],
                    user_id=user_id,
                    meeting_title=f"Benchmark Multi-turn {case_id}",
                )

            def _judge(**kwargs):
                return judge_multi_turn_quality(**kwargs, llm=judge_llm)

            return await execute_multi_turn_cases(
                dataset,
                ask_fn=ask,
                judge_fn=_judge,
                fixture_info=fixture_info,
                judge_repeats=args.judge_repeats,
            )

        result = asyncio.run(_run())

    return {
        "command": "multi-turn",
        "valid": result["valid"],
        "validity_errors": result["validity_errors"],
        "judge_config": judge_config,
        "evidence_quality": _assess_quality_evidence(
            observed_cases=len(dataset["cases"]),
            dataset_kind="synthetic",
            judge_repeats=args.judge_repeats,
            same_model_as_system=judge_config["judge_isolation"]["same_model_as_generator"],
        ),
        "stats": result["stats"],
        "multi_turn_quality": {"stats": result["stats"], "rows": result["rows"]},
    }


def run_memory_benchmark(args: argparse.Namespace) -> dict:
    """Run paired memory-on/off/distractor controls in isolated storage."""
    from src.services.llm import create_llm

    from ._bench_env import bench_environment
    from ._bench_kg_memory import (
        build_knowledge_graph_answer_prompt,
        execute_knowledge_graph_cases,
        validate_knowledge_graph_dataset,
    )
    from ._bench_memory import (
        build_memory_answer_prompt,
        execute_memory_cases,
        execute_memory_pipeline_cases,
        validate_memory_dataset,
    )
    from ._bench_rag_judge import get_judge_config, judge_answer_correctness

    dataset_path = EVALUATION_DIR / "datasets" / "memory_cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_memory_dataset(dataset)
    kg_dataset_path = EVALUATION_DIR / "datasets" / "knowledge_graph_cases.json"
    kg_dataset = json.loads(kg_dataset_path.read_text(encoding="utf-8"))
    validate_knowledge_graph_dataset(kg_dataset)
    reasoner_llm = create_llm(args.reasoner_model) if args.reasoner_model else create_llm()
    judge_llm = create_llm(args.judge_model) if args.judge_model else create_llm()
    judge_config = get_judge_config(args.judge_model)
    reasoner_config = get_judge_config(args.reasoner_model)["llm"]
    same_model_as_reasoner = judge_config["llm"]["model"] == reasoner_config["model"]

    with bench_environment():
        from src.core.config import settings as benchmark_settings
        from src.core.database import close_all_connections, init_db
        from src.services.chain._extraction import run_combined_extraction
        from src.services.knowledge_graph import kg_service
        from src.services.knowledge_graph._storage import _store_entities, _store_relations
        from src.services.llm import invoke_llm_text
        from src.services.memory import memory_service

        from ._bench_rag_judge import get_llm_runtime_config

        close_all_connections()
        extraction_model = (
            benchmark_settings.MEMORY_EXTRACTION_MODEL or benchmark_settings.LLM_MODEL
        )
        extraction_model_config = get_llm_runtime_config(extraction_model)

        async def _run() -> dict:
            init_db()

            async def _remember(*, user_id, key, value, sequence) -> None:
                await asyncio.to_thread(
                    memory_service.set,
                    user_id=user_id,
                    key=key,
                    value=value,
                    source="benchmark",
                    importance=5,
                    turn_index=sequence,
                )

            async def _retrieve(*, user_id, query, limit) -> list[dict]:
                entries = await memory_service.search_semantic(
                    user_id=user_id,
                    query=query,
                    limit=limit,
                    min_importance=1,
                )
                return [
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "combined_score": round(entry.combined_score, 6),
                    }
                    for entry in entries
                ]

            async def _answer(*, query, memory_values) -> str:
                return await invoke_llm_text(
                    reasoner_llm,
                    build_memory_answer_prompt(query, memory_values),
                )

            def _judge(**kwargs):
                return judge_answer_correctness(**kwargs, llm=judge_llm)

            previous_multi_hop_enabled = benchmark_settings.MEMORY_MULTI_HOP_ENABLED
            benchmark_settings.MEMORY_MULTI_HOP_ENABLED = True
            try:
                memory_result = await execute_memory_cases(
                    dataset,
                    remember_fn=_remember,
                    retrieve_fn=_retrieve,
                    answer_fn=_answer,
                    judge_fn=_judge,
                    judge_repeats=args.judge_repeats,
                )
            finally:
                benchmark_settings.MEMORY_MULTI_HOP_ENABLED = previous_multi_hop_enabled

            async def _extract_production(*, user_id, event) -> dict:
                source = str(event["fact"])
                if benchmark_settings.COMBINED_EXTRACTION_ENABLED:
                    return await run_combined_extraction(
                        user_id=user_id,
                        question="Extract durable facts from this meeting evidence.",
                        answer=source,
                        evidence_text=source,
                    )
                else:
                    added = await memory_service.auto_extract_facts(
                        user_id=user_id,
                        question="Extract durable facts from this meeting evidence.",
                        answer=source,
                        evidence_text=source,
                    )
                    return {"facts_added": added}

            async def _list_pipeline(*, user_id) -> list[dict]:
                return await asyncio.to_thread(
                    memory_service.list_all,
                    user_id,
                    include_expired=True,
                    limit=1000,
                )

            pipeline_result = await execute_memory_pipeline_cases(
                dataset,
                extract_fn=_extract_production,
                list_fn=_list_pipeline,
            )

            # The paired executor implements graph-off by withholding graph
            # storage/context. Enable the production KG gate for graph-on so
            # the benchmark measures the real retrieval path rather than a
            # globally disabled feature.
            previous_kg_enabled = benchmark_settings.KNOWLEDGE_GRAPH_ENABLED
            benchmark_settings.KNOWLEDGE_GRAPH_ENABLED = True

            async def _store_graph(*, user_id, case_id, entities, relations) -> dict[str, int]:
                session_id = f"benchmark-kg-{case_id}"
                entities_added = await _store_entities(user_id, entities, session_id)
                relations_added = await _store_relations(user_id, relations, session_id)
                return {
                    "entities_added": entities_added,
                    "relations_added": relations_added,
                }

            async def _retrieve_graph(*, user_id, query, top_k) -> str:
                return await kg_service.get_entity_context(
                    user_id=user_id,
                    query=query,
                    top_k=top_k,
                )

            async def _answer_graph(*, query, entity_context) -> str:
                return await invoke_llm_text(
                    reasoner_llm,
                    build_knowledge_graph_answer_prompt(query, entity_context),
                )

            try:
                kg_result = await execute_knowledge_graph_cases(
                    kg_dataset,
                    store_graph_fn=_store_graph,
                    retrieve_context_fn=_retrieve_graph,
                    answer_fn=_answer_graph,
                    judge_fn=_judge,
                    judge_repeats=args.judge_repeats,
                )
            finally:
                benchmark_settings.KNOWLEDGE_GRAPH_ENABLED = previous_kg_enabled
            validity_errors = [
                *memory_result["validity_errors"],
                *kg_result["validity_errors"],
                *pipeline_result["validity_errors"],
            ]
            return {
                "valid": memory_result["valid"] and kg_result["valid"] and pipeline_result["valid"],
                "complete": kg_result["complete"] and pipeline_result["complete"],
                "validity_errors": validity_errors,
                "stats": {
                    **memory_result["stats"],
                    **kg_result["stats"],
                    **pipeline_result["stats"],
                },
                "memory_rows": memory_result["rows"],
                "memory_pipeline_rows": pipeline_result["rows"],
                "knowledge_graph_rows": kg_result["rows"],
            }

        result = asyncio.run(_run())

    return {
        "command": "memory",
        "valid": result["valid"],
        "complete": result["complete"],
        "validity_errors": result["validity_errors"],
        "extraction_model_config": extraction_model_config,
        "reasoner_config": reasoner_config,
        "judge_config": judge_config,
        "evidence_quality": _assess_quality_evidence(
            observed_cases=len(dataset["cases"]) + len(kg_dataset["cases"]),
            dataset_kind="synthetic",
            judge_repeats=args.judge_repeats,
            same_model_as_system=same_model_as_reasoner,
        ),
        "stats": result["stats"],
        "memory_quality": {
            "stats": result["stats"],
            "rows": result["memory_rows"],
            "pipeline_rows": result["memory_pipeline_rows"],
            "knowledge_graph_rows": result["knowledge_graph_rows"],
        },
    }


def run_memory_pipeline_benchmark(args: argparse.Namespace) -> dict:
    """Run only the production source-to-memory extraction/update evaluation."""
    from ._bench_env import bench_environment
    from ._bench_memory import execute_memory_pipeline_cases, validate_memory_dataset

    dataset_path = EVALUATION_DIR / "datasets" / "memory_cases.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_memory_dataset(dataset)

    with bench_environment():
        from src.core.config import settings as benchmark_settings
        from src.core.database import close_all_connections, init_db
        from src.services.chain._extraction import run_combined_extraction
        from src.services.memory import memory_service

        from ._bench_rag_judge import get_llm_runtime_config

        close_all_connections()
        extraction_model = (
            benchmark_settings.MEMORY_EXTRACTION_MODEL or benchmark_settings.LLM_MODEL
        )
        extraction_model_config = get_llm_runtime_config(extraction_model)

        async def _run() -> dict:
            init_db()

            async def _extract(*, user_id, event) -> dict:
                source = str(event["fact"])
                if benchmark_settings.COMBINED_EXTRACTION_ENABLED:
                    return await run_combined_extraction(
                        user_id=user_id,
                        question="Extract durable facts from this meeting evidence.",
                        answer=source,
                        evidence_text=source,
                    )
                else:
                    added = await memory_service.auto_extract_facts(
                        user_id=user_id,
                        question="Extract durable facts from this meeting evidence.",
                        answer=source,
                        evidence_text=source,
                    )
                    return {"facts_added": added}

            async def _list(*, user_id) -> list[dict]:
                return await asyncio.to_thread(
                    memory_service.list_all,
                    user_id,
                    include_expired=True,
                    limit=1000,
                )

            return await execute_memory_pipeline_cases(
                dataset,
                extract_fn=_extract,
                list_fn=_list,
            )

        result = asyncio.run(_run())

    return {
        "command": "memory-pipeline",
        "valid": result["valid"],
        "complete": result["complete"],
        "validity_errors": result["validity_errors"],
        "extraction_model_config": extraction_model_config,
        "evidence_quality": _assess_quality_evidence(
            observed_cases=len(dataset["cases"]),
            dataset_kind="synthetic",
            judge_repeats=1,
            same_model_as_system=False,
        ),
        "stats": result["stats"],
        "memory_pipeline": {"stats": result["stats"], "rows": result["rows"]},
    }


def run_process_benchmark(args: argparse.Namespace) -> dict:
    """Evaluate a captured E2E report against process expectations offline."""
    from ._bench_env import bench_environment
    from ._bench_process import (
        evaluate_process_report,
        validate_process_expectations,
    )
    from ._bench_process_faults import capture_process_failure_traces

    expectations_path = EVALUATION_DIR / "datasets" / "process_expectations.json"
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    validate_process_expectations(expectations)
    report = json.loads(args.report.read_text(encoding="utf-8"))

    with bench_environment() as benchmark_root:
        from src.core.database import close_all_connections

        close_all_connections()
        failure_traces = asyncio.run(capture_process_failure_traces(expectations, benchmark_root))

    report = dict(report)
    report["diagnostics"] = dict(report.get("diagnostics", {}))
    report["diagnostics"]["failure_traces"] = failure_traces
    result = evaluate_process_report(expectations, report)
    return {
        "command": "process",
        "valid": result["valid"],
        "complete": result["complete"],
        "validity_errors": result["validity_errors"],
        "source_report": str(args.report.resolve()),
        "stats": result["stats"],
        "process_quality": {"stats": result["stats"], "rows": result["rows"]},
    }


# ---------------------------------------------------------------------------
# RAG quality benchmarks
# ---------------------------------------------------------------------------


def _rouge_l_f1(reference: str, hypothesis: str) -> float:
    """Cheap token-level ROUGE-L F1 using longest-common-subsequence."""
    ref_tokens = reference.lower().split()
    hyp_tokens = hypothesis.lower().split()
    if not ref_tokens or not hyp_tokens:
        return 0.0

    # LCS dynamic programming
    m, n = len(ref_tokens), len(hyp_tokens)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    lcs_len = prev[n]
    precision = lcs_len / len(hyp_tokens)
    recall = lcs_len / len(ref_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _embedding_cosine_diagnostic(
    reference: str, hypothesis: str
) -> tuple[float | None, str | None]:
    """Return cosine similarity and a stable, non-secret failure category."""
    try:
        from src.services.embedder import get_embeddings

        embeddings = get_embeddings().embed_documents([reference, hypothesis])
        a, b = embeddings[0], embeddings[1]
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return None, "zero_norm"
        return dot / (norm_a * norm_b), None
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("Embedding cosine failed: %s", exc, exc_info=True)
        return None, type(exc).__name__


def _embedding_cosine(reference: str, hypothesis: str) -> float | None:
    """Compute cosine similarity between embedded reference and hypothesis.

    Returns None on any failure so the benchmark stays resilient.
    """
    score, _error = _embedding_cosine_diagnostic(reference, hypothesis)
    return score


def _benchmark_source_artifact(source: dict) -> dict:
    """Keep only reproducibility fields from a synthetic benchmark source."""
    content = str(source.get("content", ""))
    return {
        "rank": source.get("rank"),
        "file_name": source.get("file_name"),
        "file_type": source.get("file_type"),
        "chunk_index": source.get("chunk_index"),
        "page_number": source.get("page_number"),
        "slide_number": source.get("slide_number"),
        "source_kind": source.get("source_kind"),
        "score": source.get("score"),
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def run_rag_retrieval_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment
    from ._bench_rag_quality import (
        canonical_chunk_key,
        file_precision_at_k,
        file_recall_at_k,
        mrr,
        ndcg_at_k,
        recall_at_k,
        validate_retrieval_rows,
    )

    with bench_environment():
        from src.core.config import settings
        from src.core.database import close_all_connections, init_db
        from src.services.rag import retrieve
        from src.services.rag._reranker import rerank
        from src.services.rag._retriever import _build_filters, _vector_retrieve

        from ._bench_fixtures import ingest_fixtures

        async def _run() -> dict:
            close_all_connections()
            init_db()
            with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
                golden = json.load(f)
            fixture_names = list(
                dict.fromkeys(item["fixture_file"] for item in golden.get("items", []))
            )
            fixture_info = await ingest_fixtures(fixture_names)
            fixture_to_meeting = {name: mid for name, (mid, _) in fixture_info.items()}
            fixture_to_file = {name: fid for name, (_, fid) in fixture_info.items()}
            file_names_by_id = {file_id: name for name, file_id in fixture_to_file.items()}

            def _rank_evidence(results: list[dict]) -> tuple[list[str], list[str]]:
                physical_ids: list[str] = []
                logical_keys: list[str] = []
                for rank, result in enumerate(results):
                    metadata = result.get("metadata", {})
                    chunk_id = metadata.get("chunk_id") if isinstance(metadata, dict) else None
                    physical_ids.append(str(chunk_id or f"missing-id-at-rank-{rank + 1}"))
                    logical_keys.append(
                        canonical_chunk_key(
                            result,
                            file_names_by_id=file_names_by_id,
                            fallback_rank=rank,
                        )
                    )
                return physical_ids, logical_keys

            rows = []
            for item in golden.get("items", []):
                fixture = item["fixture_file"]
                corpus_files = _declared_corpus_files(item, fixture_names)
                corpus_meeting_ids = [
                    fixture_to_meeting[name] for name in corpus_files if name in fixture_to_meeting
                ]
                if fixture not in fixture_to_meeting or not corpus_meeting_ids:
                    continue

                expected_keys = {
                    f"{fixture}:{chunk_id}" for chunk_id in item.get("expected_chunks", [])
                }

                # Expected files/chunks are evaluator-only labels. Retrieval sees
                # the declared fixture corpus, not the answer-bearing file.
                filters = _build_filters(corpus_meeting_ids)
                query = item["query"]
                top_k = args.top_k

                # Semantic-only
                sem_results = _vector_retrieve(query, filters, top_k, threshold=None)
                sem_physical_ids, sem_keys = _rank_evidence(sem_results)

                # Hybrid
                hybrid_fetch = (
                    settings.RAG_RERANK_FETCH_MULTIPLIER if settings.RERANKER_BINDING else 1
                )
                hybrid_results, _qa = retrieve(
                    query,
                    meeting_ids=corpus_meeting_ids,
                    top_k=top_k,
                    fetch_multiplier=hybrid_fetch,
                )
                hybrid_physical_ids, hybrid_keys = _rank_evidence(hybrid_results)

                # Hybrid + rerank
                min_rerank_pool = max(top_k * 2, 12)
                reranker_executed = bool(
                    settings.RERANKER_BINDING and len(hybrid_results) >= min_rerank_pool
                )
                if reranker_executed:
                    reranked = rerank(query, hybrid_results, top_n=top_k)
                    # A configured provider may return the unchanged candidate pool
                    # on failure. Do not award reranker scores to that fallback.
                    reranker_executed = bool(reranked) and all(
                        doc.get("reranked") is True for doc in reranked
                    )
                    rerank_physical_ids, rerank_keys = _rank_evidence(reranked)
                else:
                    rerank_physical_ids, rerank_keys = hybrid_physical_ids, hybrid_keys

                row: dict = {
                    "query_id": item["id"],
                    "expected_chunk_keys": sorted(expected_keys),
                    "semantic_chunk_keys": sem_keys,
                    "semantic_physical_ids": sem_physical_ids,
                    "hybrid_chunk_keys": hybrid_keys,
                    "hybrid_physical_ids": hybrid_physical_ids,
                    "rerank_chunk_keys": rerank_keys,
                    "rerank_physical_ids": rerank_physical_ids,
                    "reranker_executed": reranker_executed,
                    "reranker_skip_reason": (
                        None
                        if reranker_executed
                        else (
                            "disabled" if not settings.RERANKER_BINDING else "small_candidate_set"
                        )
                    ),
                    "semantic_recall_5": recall_at_k(sem_keys, expected_keys, 5),
                    "semantic_recall_10": recall_at_k(sem_keys, expected_keys, 10),
                    "semantic_mrr": mrr(sem_keys, expected_keys),
                    "semantic_ndcg_10": ndcg_at_k(sem_keys, expected_keys, 10),
                    "hybrid_recall_10": recall_at_k(hybrid_keys, expected_keys, 10),
                    "hybrid_mrr": mrr(hybrid_keys, expected_keys),
                    "hybrid_ndcg_10": ndcg_at_k(hybrid_keys, expected_keys, 10),
                    "hybrid_rerank_recall_10": (
                        recall_at_k(rerank_keys, expected_keys, 10) if reranker_executed else None
                    ),
                    "hybrid_rerank_mrr": (
                        mrr(rerank_keys, expected_keys) if reranker_executed else None
                    ),
                    "hybrid_rerank_ndcg_10": (
                        ndcg_at_k(rerank_keys, expected_keys, 10) if reranker_executed else None
                    ),
                }

                # File-level metrics when expected_files is specified
                expected_file_names = item.get("expected_files")
                if expected_file_names:
                    expected_file_ids = {
                        fixture_to_file[name]
                        for name in expected_file_names
                        if name in fixture_to_file
                    }
                    if expected_file_ids:
                        hybrid_file_ids = list(
                            dict.fromkeys(
                                r.get("metadata", {}).get("file_id")
                                for r in hybrid_results
                                if r.get("metadata", {}).get("file_id") is not None
                            )
                        )
                        row["file_precision_8"] = file_precision_at_k(
                            hybrid_file_ids,
                            expected_file_ids,
                            8,
                        )
                        row["file_recall_8"] = file_recall_at_k(
                            hybrid_file_ids,
                            expected_file_ids,
                            8,
                        )

                rows.append(row)

            def _mean(key: str, *, selected_rows: list[dict] | None = None) -> float | None:
                source_rows = rows if selected_rows is None else selected_rows
                vals = [r[key] for r in source_rows if r.get(key) is not None]
                return sum(vals) / len(vals) if vals else None

            reranker_rows = [row for row in rows if row["reranker_executed"]]

            stats = {
                "semantic-only@5": {
                    "recall": _mean("semantic_recall_5"),
                },
                "semantic-only@10": {
                    "recall": _mean("semantic_recall_10"),
                    "mrr": _mean("semantic_mrr"),
                    "ndcg": _mean("semantic_ndcg_10"),
                },
                "hybrid@10": {
                    "recall": _mean("hybrid_recall_10"),
                    "mrr": _mean("hybrid_mrr"),
                    "ndcg": _mean("hybrid_ndcg_10"),
                },
                "hybrid+rerank@10": {
                    "recall": _mean("hybrid_rerank_recall_10", selected_rows=reranker_rows),
                    "mrr": _mean("hybrid_rerank_mrr", selected_rows=reranker_rows),
                    "ndcg": _mean("hybrid_rerank_ndcg_10", selected_rows=reranker_rows),
                    "evaluated_queries": len(reranker_rows),
                    "skipped_queries": len(rows) - len(reranker_rows),
                },
            }

            # File-level metrics (only populated when expected_files is present)
            file_rows = [r for r in rows if "file_precision_8" in r]
            if file_rows:
                stats["file-level@8"] = {
                    "precision": _mean("file_precision_8"),
                    "recall": _mean("file_recall_8"),
                    "queries_with_file_ground_truth": len(file_rows),
                }

            validity = validate_retrieval_rows(
                rows,
                expected_query_ids=[item["id"] for item in golden.get("items", [])],
            )
            return {
                "command": "rag-retrieval",
                "stats": stats,
                "evidence_quality": _assess_quality_evidence(
                    observed_cases=len(rows),
                    dataset_kind="synthetic",
                    reranker_expected=bool(settings.RERANKER_BINDING),
                    reranker_evaluated=len(reranker_rows),
                ),
                "rag_quality": {"retrieval": {"stats": stats, "rows": rows}},
                **validity,
            }

        return asyncio.run(_run())


def run_rag_answer_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment
    from ._bench_rag_answer import validate_rag_answer_rows

    # A distinct client instance prevents generator/judge conversation or
    # transport state from leaking across roles, even when they use one model.

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask
        from src.services.llm import create_llm

        from ._bench_fixtures import ingest_fixtures
        from ._bench_rag_judge import (
            RAG_ANSWER_METHOD,
            get_judge_config,
            judge_answer_correctness,
            judge_answer_relevance,
            judge_citation_quality,
            judge_context_precision,
            judge_context_recall,
            judge_faithfulness,
        )

        judge_llm = create_llm(args.judge_model) if args.judge_model else create_llm()
        judge_config = get_judge_config(args.judge_model)
        print(f"judge_config: {json.dumps(judge_config, indent=2)}")

        close_all_connections()
        with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
            golden = json.load(f)

        expected_query_ids = [item["id"] for item in golden.get("items", [])]
        expected_files_by_query = {
            item["id"]: item.get("expected_files", []) for item in golden.get("items", [])
        }
        all_fixture_names = list(
            dict.fromkeys(item["fixture_file"] for item in golden.get("items", []))
        )
        corpus_files_by_query = {
            item["id"]: _declared_corpus_files(item, all_fixture_names)
            for item in golden.get("items", [])
        }

        rows = []
        parse_failures = 0
        judge_parse_retries = 0

        async def _run() -> None:
            nonlocal judge_parse_retries, parse_failures
            close_all_connections()
            init_db()
            fixture_names = all_fixture_names
            fixture_info = await ingest_fixtures(fixture_names)
            for item in golden.get("items", []):
                query = item["query"]
                expected_files = expected_files_by_query[item["id"]]
                corpus_files = corpus_files_by_query[item["id"]]
                scoped_file_ids = [
                    fixture_info[file_name][1]
                    for file_name in corpus_files
                    if file_name in fixture_info
                ]
                result = await ask(
                    question=query,
                    user_id="benchmark",
                    file_ids=scoped_file_ids,
                )
                answer = result.answer
                sources = result.sources or []
                observed_files = sorted(
                    {
                        source.get("file_name")
                        for source in sources
                        if isinstance(source.get("file_name"), str) and source["file_name"]
                    }
                )
                expected_file_set = set(expected_files)
                corpus_file_set = set(corpus_files)
                observed_file_set = set(observed_files)
                source_identity_recall = (
                    len(expected_file_set & observed_file_set) / len(expected_file_set)
                    if expected_file_set
                    else None
                )
                unexpected_source_files = sorted(observed_file_set - corpus_file_set)
                chunks = [s.get("content", "") for s in sources]
                context = "\n\n".join(chunks)

                f_scores = []
                r_scores = []
                c_scores = []
                cr_scores = []
                correctness_scores = []
                citation_scores = []
                judge_diagnostics: dict[str, list[dict | None]] = {
                    "faithfulness": [],
                    "answer_relevance": [],
                    "context_precision": [],
                    "context_recall": [],
                    "correctness": [],
                    "citation_quality": [],
                }

                for _ in range(args.judge_repeats):
                    f = judge_faithfulness(answer, context, llm=judge_llm)
                    r = judge_answer_relevance(query, answer, llm=judge_llm)
                    c = judge_context_precision(query, chunks, llm=judge_llm)
                    citation = judge_citation_quality(answer, chunks, llm=judge_llm)
                    judged = {
                        "faithfulness": f,
                        "answer_relevance": r,
                        "context_precision": c,
                        "citation_quality": citation,
                    }
                    for metric, diagnostic in judged.items():
                        judge_diagnostics[metric].append(diagnostic)
                        if diagnostic is None:
                            parse_failures += 1
                        else:
                            judge_parse_retries += int(diagnostic.get("parse_retries", 0))
                    f_scores.append(f["score"] if f else None)
                    r_scores.append(r["score"] if r else None)
                    c_scores.append(c["score"] if c else None)
                    citation_scores.append(citation["score"] if citation else None)

                    expected_answer = item.get("expected_answer")
                    if expected_answer:
                        cr = judge_context_recall(query, expected_answer, chunks, llm=judge_llm)
                        correctness = judge_answer_correctness(
                            query, expected_answer, answer, llm=judge_llm
                        )
                        for metric, diagnostic in {
                            "context_recall": cr,
                            "correctness": correctness,
                        }.items():
                            judge_diagnostics[metric].append(diagnostic)
                            if diagnostic is None:
                                parse_failures += 1
                            else:
                                judge_parse_retries += int(diagnostic.get("parse_retries", 0))
                        cr_scores.append(cr["score"] if cr else None)
                        correctness_scores.append(correctness["score"] if correctness else None)
                    else:
                        cr_scores.append(None)
                        correctness_scores.append(None)
                        judge_diagnostics["context_recall"].append(None)
                        judge_diagnostics["correctness"].append(None)

                def _avg(scores):
                    valid = [s for s in scores if s is not None]
                    return sum(valid) / len(valid) if valid else None

                rouge = None
                cosine = None
                cosine_error = None
                if item.get("expected_answer"):
                    rouge = _rouge_l_f1(item["expected_answer"], answer)
                    cosine, cosine_error = await asyncio.to_thread(
                        _embedding_cosine_diagnostic,
                        item["expected_answer"],
                        answer,
                    )

                rows.append(
                    {
                        "query_id": item["id"],
                        "query": query,
                        "expected_answer": item.get("expected_answer"),
                        "expected_files": expected_files,
                        "corpus_files": corpus_files,
                        "answer": answer,
                        "observed_files": observed_files,
                        "unexpected_source_files": unexpected_source_files,
                        "source_identity_recall": source_identity_recall,
                        "corpus_isolation": float(not unexpected_source_files),
                        "sources": [
                            {**_benchmark_source_artifact(source), "rank": rank}
                            for rank, source in enumerate(sources, start=1)
                        ],
                        "trace": result.trace,
                        "judge_diagnostics": judge_diagnostics,
                        "faithfulness": _avg(f_scores),
                        "answer_relevance": _avg(r_scores),
                        "context_precision": _avg(c_scores),
                        "context_recall": _avg(cr_scores),
                        "correctness": _avg(correctness_scores),
                        "citation_quality": _avg(citation_scores),
                        "rouge_l_f1": rouge,
                        "answer_similarity": cosine,
                        "answer_similarity_error": cosine_error,
                    }
                )

        asyncio.run(_run())

    def _mean(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    stats = {
        "faithfulness": _mean("faithfulness"),
        "answer_relevance": _mean("answer_relevance"),
        "context_precision": _mean("context_precision"),
        "context_recall": _mean("context_recall"),
        "correctness": _mean("correctness"),
        "citation_quality": _mean("citation_quality"),
        "source_identity_recall": _mean("source_identity_recall"),
        "corpus_isolation": _mean("corpus_isolation"),
        "rouge_l_f1": _mean("rouge_l_f1"),
        "answer_similarity": _mean("answer_similarity"),
        "parse_failures": parse_failures,
        "judge_parse_retries": judge_parse_retries,
    }

    validity = validate_rag_answer_rows(
        rows,
        expected_query_ids=expected_query_ids,
        expected_files_by_query=expected_files_by_query,
        corpus_files_by_query=corpus_files_by_query,
        judge_repeats=args.judge_repeats,
    )

    return {
        "command": "rag-answer",
        "evaluation_method": RAG_ANSWER_METHOD,
        "judge_config": judge_config,
        "evidence_quality": _assess_quality_evidence(
            observed_cases=len(expected_query_ids),
            dataset_kind="synthetic",
            judge_repeats=args.judge_repeats,
            same_model_as_system=judge_config["judge_isolation"]["same_model_as_generator"],
        ),
        "stats": stats,
        "rag_quality": {"answer": {"stats": stats, "rows": rows}},
        **validity,
    }


_SNAPSHOT_MONTH_ALIASES = {
    "jan": "january",
    "feb": "february",
    "mar": "march",
    "apr": "april",
    "jun": "june",
    "jul": "july",
    "aug": "august",
    "sep": "september",
    "sept": "september",
    "oct": "october",
    "nov": "november",
    "dec": "december",
}
_SNAPSHOT_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "are",
        "is",
        "was",
        "were",
        "to",
        "of",
        "in",
        "on",
        "by",
        "for",
        "from",
        "this",
        "that",
        "with",
        "what",
        "who",
        "when",
        "does",
        "do",
        "did",
        "mentioned",
        "meeting",
        "notes",
        "presentation",
        "customer",
        "rate",
        "all",
        "time",
        "high",
        "reached",
        "attendees",
        "top",
        "engineering",
        "priorities",
        "action",
        "items",
        "responsible",
        "submitting",
        "blockers",
    ]
)
_SNAPSHOT_CJK_ALIASES: dict[str, tuple[str, ...]] = {
    "three": ("三", "三个"),
    "blocker": ("阻碍", "阻塞"),
    "raise": ("提出", "提出了"),
    "mobile": ("移动", "移动端"),
    "app": ("应用", "app"),
    "wait": ("等待",),
    "final": ("最终",),
    "design": ("设计",),
    "team": ("团队",),
    "eta": ("预计",),
    "january": ("一月", "1月"),
    "database": ("数据库",),
    "migration": ("迁移",),
    "need": ("需要",),
    "additional": ("额外",),
    "cloud": ("云",),
    "storage": ("存储",),
    "budget": ("预算",),
    "approval": ("审批",),
    "analytic": ("分析",),
    "pipeline": ("管道",),
    "depend": ("依赖",),
    "third": ("第三方",),
    "party": ("第三方",),
    "api": ("api", "API"),
    "provid": ("提供",),
    "production": ("生产",),
    "access": ("访问", "权限"),
}


def _snapshot_semantic_tokens(text: str) -> set[str]:
    """Tokenize fixture answers for deterministic claim-level comparison.

    Citation markers, list numbering, month abbreviations, and common inflections
    are presentation details.  The comparator keeps factual terms and numeric
    values so a reformatted answer can pass while a changed date/percentage or
    core entity still fails.
    """
    text = re.sub(r"\[\d{1,3}\]", " ", text or "").casefold()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z]+|\d+%?", text):
        token = _SNAPSHOT_MONTH_ALIASES.get(token, token)
        if token in _SNAPSHOT_STOPWORDS or token in {"1", "2", "3"}:
            continue
        if token.endswith("ing") and len(token) > 6:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 5:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 5:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _snapshot_semantic_compare(expected: str, current: str) -> dict[str, object]:
    """Compare required claims without making answer formatting a release gate."""
    expected_tokens = _snapshot_semantic_tokens(expected)
    current_tokens = _snapshot_semantic_tokens(current)
    if not expected_tokens:
        return {"pass": True, "coverage": 1.0, "missing_claim_tokens": []}

    # Dates and percentages are high-signal claims.  A changed numeric fact is
    # a semantic regression even when the surrounding prose is very similar.
    expected_numeric = {
        token for token in expected_tokens if token.endswith("%") or token.isdigit()
    }
    missing_numeric = sorted(expected_numeric - current_tokens)
    current_folded = current.casefold()
    matched_tokens = {
        token
        for token in expected_tokens
        if token in current_tokens
        or any(alias.casefold() in current_folded for alias in _SNAPSHOT_CJK_ALIASES.get(token, ()))
    }
    coverage = len(matched_tokens) / len(expected_tokens)
    passed = not missing_numeric and coverage >= 0.75
    missing = sorted(expected_tokens - matched_tokens)
    return {
        "pass": passed,
        "coverage": round(coverage, 4),
        "missing_claim_tokens": missing,
        "missing_numeric_claims": missing_numeric,
    }


def run_rag_snapshot_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask

        from ._bench_fixtures import ingest_fixtures

        close_all_connections()
        with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
            golden = json.load(f)

        snapshots: dict[str, dict] = {}
        for snap_path in RAG_SNAPSHOTS_DIR.glob("*.json"):
            with open(snap_path, encoding="utf-8") as f:
                data = json.load(f)
                snapshots[data.get("query_id")] = data

        current: list[dict] = []

        async def _run() -> None:
            close_all_connections()
            init_db()
            fixture_names = list(
                dict.fromkeys(item["fixture_file"] for item in golden.get("items", []))
            )
            fixture_info = await ingest_fixtures(fixture_names)
            meeting_ids = list({mid for mid, _ in fixture_info.values()})
            for item in golden.get("items", []):
                result = await ask(
                    question=item["query"],
                    user_id="benchmark",
                    meeting_ids=meeting_ids,
                    # Snapshot quality must exercise the stable full retrieval
                    # profile.  The production fast path is latency-tested
                    # separately and may intentionally return extractive
                    # evidence when its generation deadline is exceeded.
                    rag_mode="hybrid",
                )
                from ._bench_rag_quality import canonical_chunk_key

                source_ids = sorted(
                    {
                        canonical_chunk_key(
                            {"metadata": source.get("metadata") or source},
                            file_names_by_id={fid: name for name, (_, fid) in fixture_info.items()},
                            fallback_rank=rank,
                        )
                        for rank, source in enumerate(result.sources or [])
                        if (source.get("metadata") or source).get("chunk_id")
                    }
                )
                current.append(
                    {
                        "query_id": item["id"],
                        "answer": result.answer,
                        "source_ids": source_ids,
                        "retrieval_mode": "hybrid",
                    }
                )

        asyncio.run(_run())

    diffs = []
    semantic_diffs = []
    literal_diffs = []
    source_identity_diffs = []
    for cur in current:
        qid = cur["query_id"]
        if not cur["source_ids"]:
            diff = {"query_id": qid, "diff": "missing stable source identity"}
            diffs.append(diff)
            source_identity_diffs.append(diff)
        snap = snapshots.get(qid)
        if snap is None:
            diffs.append({"query_id": qid, "diff": "no existing snapshot"})
            continue
        if set(cur["source_ids"]) != set(snap.get("source_ids", [])):
            diff = {"query_id": qid, "diff": "source_ids changed"}
            diffs.append(diff)
            source_identity_diffs.append(diff)
        if cur["answer"] != snap.get("answer", ""):
            literal_diffs.append({"query_id": qid, "diff": "answer text changed"})

        item = next(
            (candidate for candidate in golden.get("items", []) if candidate["id"] == qid),
            None,
        )
        expected_answer = item.get("expected_answer") if isinstance(item, dict) else None
        if expected_answer:
            semantic = _snapshot_semantic_compare(expected_answer, cur["answer"])
            if semantic["pass"] is not True:
                diff = {
                    "query_id": qid,
                    "diff": "semantic answer claims changed",
                    **semantic,
                }
                diffs.append(diff)
                semantic_diffs.append(diff)

    if args.update_snapshots:
        RAG_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        for cur in current:
            qid = cur["query_id"]
            snap_path = RAG_SNAPSHOTS_DIR / f"{qid}.json"
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2)
        print(f"Updated {len(current)} snapshots in {RAG_SNAPSHOTS_DIR}")

    return {
        "command": "rag-snapshot",
        "valid": not diffs and bool(current),
        "complete": not diffs and bool(current),
        "validity_errors": [f"{item['query_id']}: {item['diff']}" for item in diffs],
        "evidence_quality": {
            "release_ready": False,
            "kind": "semantic_snapshot_regression",
            "literal_regression_gate": "diagnostic_only",
            "human_business_review": False,
        },
        "stats": {
            "diffs": len(diffs),
            "semantic_differences": len(semantic_diffs),
            "literal_differences": len(literal_diffs),
            "source_identity_differences": len(source_identity_diffs),
        },
        "snapshot_candidates": current,
        "snapshot_diffs": diffs,
        "semantic_diffs": semantic_diffs,
        "literal_diffs": literal_diffs,
        "source_identity_diffs": source_identity_diffs,
    }


def run_rag_all_benchmark(args: argparse.Namespace) -> dict:
    ret = run_rag_retrieval_benchmark(args)
    ans = run_rag_answer_benchmark(args)
    snap = run_rag_snapshot_benchmark(args)
    return {
        "command": "rag-all",
        "valid": all(part.get("valid") is True for part in (ret, ans, snap)),
        "complete": all(
            part.get("complete", part.get("valid")) is True for part in (ret, ans, snap)
        ),
        "validity_errors": [
            f"{name}: {error}"
            for name, part in (("retrieval", ret), ("answer", ans), ("snapshot", snap))
            if part.get("valid") is not True
            for error in (part.get("validity_errors") or ["component not validated"])
        ],
        "judge_config": ans.get("judge_config"),
        "evidence_quality": {
            "retrieval": ret.get("evidence_quality"),
            "answer": ans.get("evidence_quality"),
        },
        "stats": {
            "retrieval": ret.get("stats", {}),
            "answer": ans.get("stats", {}),
            "snapshot": snap.get("stats", {}),
        },
        "rag_quality": {
            **ret.get("rag_quality", {}),
            **ans.get("rag_quality", {}),
        },
        "snapshot_diffs": snap.get("snapshot_diffs", []),
    }


def run_rag_chunk_phase1(args: argparse.Namespace) -> dict:
    """Run Phase 1: compare chunk strategies."""
    from ._bench_rag_phase1 import AUDIO_CHUNK_CONFIGS, run_phase1, run_single_config

    if args.list_configs:
        print("Available chunk configs (use --config-index N to run a single one):")
        for i, cfg in enumerate(AUDIO_CHUNK_CONFIGS):
            print(
                f"  [{i}] {cfg.name} {cfg.preset} | "
                f"chunk_size={cfg.chunk_size} | method={cfg.method}"
            )
        return {"command": "rag-chunk-phase1", "stats": {}, "list_configs": True}

    scoped_path = Path(args.golden_scoped)
    unscoped_path = Path(args.golden_unscoped)

    if args.config_index is not None:
        if args.config_index < 0 or args.config_index >= len(AUDIO_CHUNK_CONFIGS):
            raise ValueError(f"--config-index must be between 0 and {len(AUDIO_CHUNK_CONFIGS) - 1}")
        cfg = AUDIO_CHUNK_CONFIGS[args.config_index]
        print(f"Running single config: [{args.config_index}] {cfg.name} {cfg.preset}")
        payload = run_single_config(cfg, scoped_path, unscoped_path, top_k=args.top_k)
        payload["command"] = "rag-chunk-phase1"
        return payload

    payload = run_phase1(scoped_path, unscoped_path, top_k=args.top_k)
    payload["command"] = "rag-chunk-phase1"
    return payload


def run_rag_chunk_phase2(args: argparse.Namespace) -> dict:
    """Run Phase 2: retrieval grid search on top-2 chunk configs."""
    from ._bench_rag_phase2 import run_phase2

    phase1_path = Path(args.phase1_result)
    with open(phase1_path, encoding="utf-8") as f:
        phase1_data = json.load(f)

    top_2 = phase1_data.get("top_2", [])
    if not top_2:
        raise ValueError("Phase 1 result contains no top_2 configs")

    scoped_path = Path(args.golden_scoped)
    unscoped_path = Path(args.golden_unscoped)
    payload = run_phase2(top_2, scoped_path, unscoped_path, top_k=args.top_k)
    payload["command"] = "rag-chunk-phase2"
    return payload


def run_rag_chunk_full(args: argparse.Namespace) -> dict:
    """Run Phase 1 + Phase 2 end-to-end."""
    p1 = run_rag_chunk_phase1(args)
    # Write Phase 1 report explicitly
    json_path1, md_path1 = _write_report("rag-chunk-phase1", p1)
    print(f"Wrote {json_path1} and {md_path1}")

    # Write Phase 1 result to a temp path so Phase 2 can read it
    tmp_path = RESULTS_DIR / "_phase1_temp.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(p1, f, indent=2)

    # Fabricate a Namespace for Phase 2
    p2_args = argparse.Namespace(
        phase1_result=str(tmp_path),
        golden_scoped=args.golden_scoped,
        golden_unscoped=args.golden_unscoped,
        top_k=args.top_k,
    )
    p2 = run_rag_chunk_phase2(p2_args)
    # Write Phase 2 report explicitly
    json_path2, md_path2 = _write_report("rag-chunk-phase2", p2)
    print(f"Wrote {json_path2} and {md_path2}")

    return {
        "command": "rag-chunk-full",
        "stats": {
            "phase1_combined_recall": p1.get("top_2", [{}])[0].get("combined_recall", 0.0)
            if p1.get("top_2")
            else 0.0,
            "phase2_weighted_score": p2.get("recommendation", {}).get("weighted_score", 0.0)
            if p2.get("recommendation")
            else 0.0,
        },
        "phase1": p1,
        "phase2": p2,
    }


def run_rag_matrix_benchmark(args: argparse.Namespace) -> dict:
    """Run rag-retrieval benchmark across a matrix of configuration combinations."""
    import csv
    import itertools

    scoping_modes = [m.strip() for m in args.scoping_modes.split(",")]
    merge_strategies = [s.strip() for s in args.merge_strategies.split(",")]
    multi_query_flags = [f.strip() for f in args.multi_query.split(",")]

    combinations = list(itertools.product(scoping_modes, merge_strategies, multi_query_flags))
    print(
        f"Matrix: {len(combinations)} combinations "
        f"({len(scoping_modes)} scopes x {len(merge_strategies)} merges "
        f"x {len(multi_query_flags)} multi-query)"
    )

    results: list[dict] = []
    for idx, (scoping, merge, mq_flag) in enumerate(combinations, 1):
        label = f"{scoping}/{merge}/mq={mq_flag}"
        print(f"[{idx}/{len(combinations)}] {label}")

        # Mutate settings for this combination
        _set_rag_matrix_settings(scoping, merge, mq_flag)

        # Reuse rag-retrieval benchmark
        payload = run_rag_retrieval_benchmark(args)

        stats = payload.get("stats", {})
        hybrid = stats.get("hybrid@10", {})

        row = {
            "scoping_mode": scoping,
            "merge_strategy": merge,
            "multi_query": mq_flag,
            "hybrid_recall_10": hybrid.get("recall", 0.0),
            "hybrid_mrr": hybrid.get("mrr", 0.0),
            "hybrid_ndcg_10": hybrid.get("ndcg", 0.0),
        }
        results.append(row)
        print(
            f"  recall@10={row['hybrid_recall_10']:.4f}  mrr={row['hybrid_mrr']:.4f}  "
            f"ndcg@10={row['hybrid_ndcg_10']:.4f}"
        )

    # Write CSV
    ts = datetime.now(UTC).isoformat()
    csv_path = RESULTS_DIR / f"matrix_{ts}.csv"
    if results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote {csv_path}")

    # Markdown summary
    md_path = RESULTS_DIR / f"matrix_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# RAG Matrix Benchmark\n\n")
        f.write("| scoping_mode | merge_strategy | multi_query | recall@10 | MRR | nDCG@10 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in results:
            f.write(
                f"| {r['scoping_mode']} | {r['merge_strategy']} | {r['multi_query']} "
                f"| {r['hybrid_recall_10']:.4f} | {r['hybrid_mrr']:.4f} "
                f"| {r['hybrid_ndcg_10']:.4f} |\n"
            )
    print(f"Wrote {md_path}")

    return {
        "command": "rag-matrix",
        "combinations": len(combinations),
        "results": results,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
    }


def _set_rag_matrix_settings(scoping: str, merge: str, mq_flag: str) -> None:
    """Patch global settings for a matrix combination."""
    from src.core.config import settings

    settings.RAG_FILE_SCOPING_MODE = scoping
    settings.RAG_FUNNEL_MERGE_STRATEGY = merge
    is_mq = mq_flag == "enabled"
    settings.MULTI_QUERY_ENABLED = is_mq
    settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = is_mq


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Performance benchmark harness for meeting-agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # chat
    chat_p = sub.add_parser("chat", help="Benchmark chat pipeline latency")
    chat_p.add_argument("--iterations", type=int, default=5)
    chat_p.add_argument("--profile", choices=("fast", "balanced", "thorough"), default="balanced")
    chat_p.add_argument("--order-seed", type=int, default=0)
    chat_p.add_argument("--max-degraded-rate", type=float, default=0.05)
    chat_p.add_argument("--max-ttft-p95-ms", type=float, default=3000.0)
    chat_p.add_argument("--max-total-p95-ms", type=float, default=5000.0)
    chat_p.add_argument("--enforce-slo", action="store_true")
    chat_p.add_argument("--baseline", action="store_true")
    chat_p.add_argument("--update-baseline", action="store_true")

    # ingest
    ingest_p = sub.add_parser("ingest", help="Benchmark ingest pipeline latency")
    ingest_p.add_argument("--iterations", type=int, default=3)
    ingest_p.add_argument("--baseline", action="store_true")
    ingest_p.add_argument("--update-baseline", action="store_true")

    # micro
    micro_p = sub.add_parser("micro", help="Run component micro-benchmarks")
    micro_p.add_argument("--baseline", action="store_true")
    micro_p.add_argument("--update-baseline", action="store_true")

    sub.add_parser(
        "protocol-audit",
        help="Validate evaluation methodology, datasets, and implementation status",
    )
    sub.add_parser(
        "evidence-governance",
        help="Evaluate approval, temporal-scope, revision-fence, and prompt-label policies",
    )
    reranker_p = sub.add_parser(
        "reranker-quality",
        help="Evaluate reranker ordering on controlled candidate pools",
    )
    reranker_p.add_argument("--baseline", action="store_true")
    reranker_p.add_argument("--update-baseline", action="store_true")

    multi_turn_p = sub.add_parser(
        "multi-turn",
        help="Evaluate grounded incremental conversations in one session",
    )
    multi_turn_p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    multi_turn_p.add_argument("--judge-repeats", type=int, default=3)
    multi_turn_p.add_argument("--baseline", action="store_true")
    multi_turn_p.add_argument("--update-baseline", action="store_true")

    memory_p = sub.add_parser(
        "memory",
        help="Evaluate paired long-horizon memory-on/off/distractor conditions",
    )
    memory_p.add_argument("--reasoner-model", type=str, default=None)
    memory_p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    memory_p.add_argument("--judge-repeats", type=int, default=3)
    memory_p.add_argument("--baseline", action="store_true")
    memory_p.add_argument("--update-baseline", action="store_true")

    memory_pipeline_p = sub.add_parser(
        "memory-pipeline",
        help="Evaluate the production source-to-memory extraction/update path",
    )
    memory_pipeline_p.add_argument("--baseline", action="store_true")
    memory_pipeline_p.add_argument("--update-baseline", action="store_true")

    process_p = sub.add_parser(
        "process",
        help="Evaluate captured ingest/chat traces without external provider calls",
    )
    process_p.add_argument("--report", type=Path, required=True)
    process_p.add_argument("--baseline", action="store_true")
    process_p.add_argument("--update-baseline", action="store_true")

    baseline_import_p = sub.add_parser(
        "baseline-import",
        help="Archive an existing valid report without rerunning external providers",
    )
    baseline_import_p.add_argument("--report", type=Path, required=True)

    # rag-retrieval
    rag_ret_p = sub.add_parser("rag-retrieval", help="Benchmark retrieval metrics")
    rag_ret_p.add_argument("--top-k", type=int, default=10)
    rag_ret_p.add_argument("--baseline", action="store_true")
    rag_ret_p.add_argument("--update-baseline", action="store_true")

    # rag-answer
    rag_ans_p = sub.add_parser("rag-answer", help="Benchmark answer quality (LLM-as-judge)")
    rag_ans_p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    rag_ans_p.add_argument("--judge-repeats", type=int, default=3)
    rag_ans_p.add_argument("--baseline", action="store_true")
    rag_ans_p.add_argument("--update-baseline", action="store_true")

    # rag-snapshot
    rag_snap_p = sub.add_parser("rag-snapshot", help="Compare against answer snapshots")
    rag_snap_p.add_argument("--update-snapshots", action="store_true")
    rag_snap_p.add_argument("--baseline", action="store_true")
    rag_snap_p.add_argument("--update-baseline", action="store_true")

    # rag-all
    rag_all_p = sub.add_parser("rag-all", help="Run all RAG quality benchmarks")
    rag_all_p.add_argument("--top-k", type=int, default=10)
    rag_all_p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    rag_all_p.add_argument("--judge-repeats", type=int, default=3)
    rag_all_p.add_argument("--update-snapshots", action="store_true")
    rag_all_p.add_argument("--baseline", action="store_true")
    rag_all_p.add_argument("--update-baseline", action="store_true")

    # rag-matrix
    matrix_p = sub.add_parser("rag-matrix", help="Run retrieval across config matrix")
    matrix_p.add_argument(
        "--scoping-modes",
        type=str,
        default="router_and_funnel,funnel_only,router_only",
        help="Comma-separated scoping modes",
    )
    matrix_p.add_argument(
        "--merge-strategies",
        type=str,
        default="rrf,zigzag",
        help="Comma-separated merge strategies",
    )
    matrix_p.add_argument(
        "--multi-query",
        type=str,
        default="disabled,enabled",
        help="Comma-separated multi-query flags",
    )
    matrix_p.add_argument("--top-k", type=int, default=10)

    # rag-chunk-phase1
    rag_p1 = sub.add_parser("rag-chunk-phase1", help="Phase 1: compare audio chunk strategies")
    rag_p1.add_argument(
        "--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json")
    )
    rag_p1.add_argument(
        "--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json")
    )
    rag_p1.add_argument("--top-k", type=int, default=10)
    rag_p1.add_argument(
        "--config-index",
        type=int,
        default=None,
        help="Run only a single config by index (0-based); omit to run all",
    )
    rag_p1.add_argument(
        "--list-configs", action="store_true", help="List all available chunk configs and exit"
    )
    rag_p1.add_argument("--baseline", action="store_true")
    rag_p1.add_argument("--update-baseline", action="store_true")

    # rag-chunk-phase2
    rag_p2 = sub.add_parser(
        "rag-chunk-phase2", help="Phase 2: retrieval grid search on top-2 chunk configs"
    )
    rag_p2.add_argument("--phase1-result", required=True)
    rag_p2.add_argument(
        "--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json")
    )
    rag_p2.add_argument(
        "--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json")
    )
    rag_p2.add_argument("--top-k", type=int, default=10)
    rag_p2.add_argument("--baseline", action="store_true")
    rag_p2.add_argument("--update-baseline", action="store_true")

    # rag-chunk-full
    rag_full = sub.add_parser("rag-chunk-full", help="End-to-end Phase 1 + Phase 2")
    rag_full.add_argument(
        "--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json")
    )
    rag_full.add_argument(
        "--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json")
    )
    rag_full.add_argument("--top-k", type=int, default=10)
    rag_full.add_argument("--baseline", action="store_true")
    rag_full.add_argument("--update-baseline", action="store_true")

    # all
    all_p = sub.add_parser(
        "all",
        help="Run every required performance, RAG, multi-turn, memory, and process suite",
    )
    all_p.add_argument("--iterations", type=int, default=5)
    all_p.add_argument("--profile", choices=("fast", "balanced", "thorough"), default="balanced")
    all_p.add_argument("--order-seed", type=int, default=0)
    all_p.add_argument("--max-degraded-rate", type=float, default=0.05)
    all_p.add_argument("--max-ttft-p95-ms", type=float, default=3000.0)
    all_p.add_argument("--max-total-p95-ms", type=float, default=5000.0)
    all_p.add_argument("--enforce-slo", action="store_true")
    all_p.add_argument("--top-k", type=int, default=10)
    all_p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    all_p.add_argument("--reasoner-model", type=str, default=None)
    all_p.add_argument("--judge-repeats", type=int, default=3)
    all_p.add_argument(
        "--process-report",
        dest="report",
        type=Path,
        required=True,
        help="Captured full-stack E2E report used by the process-quality suite",
    )
    all_p.add_argument("--update-snapshots", action="store_true")
    all_p.add_argument("--baseline", action="store_true")
    all_p.add_argument(
        "--baseline-commands",
        nargs="+",
        default=None,
        choices=[
            "chat",
            "ingest",
            "micro",
            "reranker-quality",
            "rag-retrieval",
            "rag-answer",
            "rag-snapshot",
            "multi-turn",
            "memory",
            "process",
        ],
        help=(
            "Explicit release gates; other suites still execute as diagnostics. "
            "Protocol validity is always enforced."
        ),
    )
    all_p.add_argument("--update-baseline", action="store_true")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    threshold = float(os.environ.get("BENCH_REGRESSION_THRESHOLD", "0.25"))

    compare_requested = bool(getattr(args, "baseline", False))
    update_requested = bool(getattr(args, "update_baseline", False)) or (
        args.command == "baseline-import"
    )
    if compare_requested and update_requested:
        parser.error("--baseline and --update-baseline are mutually exclusive")

    baseline = _load_baseline() if compare_requested else {}
    if compare_requested and not baseline:
        print(f"Baseline not found or empty: {BASELINE_PATH}", file=sys.stderr)
        return 2

    compared_commands = set(
        getattr(args, "baseline_commands", None)
        or (
            [
                "chat",
                "ingest",
                "micro",
                "rag-retrieval",
                "rag-answer",
                "rag-snapshot",
                "multi-turn",
                "memory",
                "process",
            ]
            if args.command == "all"
            else [args.command]
        )
    )
    if compare_requested:
        missing = sorted(
            command
            for command in compared_commands
            if _baseline_payload_for(baseline, command) is None
        )
        if missing:
            print(
                "Baseline preflight failed; missing commands: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2

    started_metadata = _capture_run_metadata(args) if args.command != "baseline-import" else None
    payloads: list[dict] = []

    if args.command == "chat":
        payload = run_chat_benchmark(args)
        payloads.append(payload)
    elif args.command == "ingest":
        payload = run_ingest_benchmark(args)
        payloads.append(payload)
    elif args.command == "micro":
        payload = run_micro_benchmark(args)
        payloads.append(payload)
    elif args.command == "protocol-audit":
        payload = run_protocol_audit(args)
        payloads.append(payload)
    elif args.command == "evidence-governance":
        payload = run_evidence_governance_benchmark(args)
        payloads.append(payload)
    elif args.command == "reranker-quality":
        payload = run_reranker_quality_benchmark(args)
        payloads.append(payload)
    elif args.command == "multi-turn":
        payload = run_multi_turn_benchmark(args)
        payloads.append(payload)
    elif args.command == "memory":
        payload = run_memory_benchmark(args)
        payloads.append(payload)
    elif args.command == "memory-pipeline":
        payload = run_memory_pipeline_benchmark(args)
        payloads.append(payload)
    elif args.command == "process":
        payload = run_process_benchmark(args)
        payloads.append(payload)
    elif args.command == "baseline-import":
        try:
            payloads.append(_load_baseline_report(args.report))
        except ValueError as exc:
            print(f"Baseline import rejected: {exc}", file=sys.stderr)
            return 2
    elif args.command == "rag-retrieval":
        payload = run_rag_retrieval_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-answer":
        payload = run_rag_answer_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-snapshot":
        payload = run_rag_snapshot_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-all":
        payload = run_rag_all_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-matrix":
        payload = run_rag_matrix_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-phase1":
        payload = run_rag_chunk_phase1(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-phase2":
        payload = run_rag_chunk_phase2(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-full":
        payload = run_rag_chunk_full(args)
        payloads.append(payload)
    elif args.command == "all":
        payloads.append(run_protocol_audit(args))
        payloads.append(run_evidence_governance_benchmark(args))
        payloads.append(run_reranker_quality_benchmark(args))
        payloads.append(run_chat_benchmark(args))
        payloads.append(run_ingest_benchmark(args))
        payloads.append(run_micro_benchmark(args))
        payloads.append(run_rag_retrieval_benchmark(args))
        payloads.append(run_rag_answer_benchmark(args))
        payloads.append(run_rag_snapshot_benchmark(args))
        payloads.append(run_multi_turn_benchmark(args))
        payloads.append(run_memory_benchmark(args))
        payloads.append(run_process_benchmark(args))
    else:
        parser.print_help()
        return 1

    if args.command != "baseline-import":
        ended_metadata = _capture_run_metadata(args)
        run_metadata = dict(started_metadata or ended_metadata)
        changed = [
            key
            for key in (
                "dataset_fingerprint_sha256",
                "harness_fingerprint_sha256",
                "implementation_fingerprint_sha256",
            )
            if run_metadata[key] != ended_metadata[key]
        ]
        run_metadata["verified_at"] = ended_metadata["captured_at"]
        run_metadata["inputs_unchanged"] = not changed
        for payload in payloads:
            payload["run_metadata"] = run_metadata
            if changed:
                payload["valid"] = False
                payload["complete"] = False
                payload.setdefault("validity_errors", []).append(
                    "Evaluation inputs changed during execution: " + ", ".join(changed)
                )

    # Write reports
    for payload in payloads if args.command != "baseline-import" else []:
        if "stats" in payload and payload.get("list_configs"):
            continue
        if (
            "stats" in payload
            or payload.get("phase") in (1, 2)
            or ("phase1" in payload and "phase2" in payload)
        ):
            json_path, md_path = _write_report(payload["command"], payload)
            print(f"Wrote {json_path} and {md_path}")

    for payload in payloads:
        if payload.get("command") == "protocol-audit":
            print(json.dumps(payload, indent=2))
            if not payload.get("valid", False):
                return 1

    invalid_payloads = [
        payload for payload in payloads if "valid" in payload and payload.get("valid") is not True
    ]
    if invalid_payloads:
        for payload in invalid_payloads:
            print(
                f"{payload['command']} validity failure: "
                + "; ".join(payload.get("validity_errors", [])),
                file=sys.stderr,
            )
        return 1

    failed_enforced_performance = [
        payload
        for payload in payloads
        if payload.get("command") == "chat"
        and payload.get("performance_gate", {}).get("enforced")
        and not payload.get("performance_gate", {}).get("passed")
    ]
    if failed_enforced_performance:
        print("chat performance/degradation gate failed", file=sys.stderr)
        return 1

    # Baseline handling
    if update_requested:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        combined = _build_baseline_document(payloads, existing=_load_baseline())
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2)
        print(f"Updated baseline: {BASELINE_PATH}")

    if compare_requested:
        all_regressions: list[str] = []
        for payload in payloads:
            if payload["command"] not in compared_commands:
                continue
            baseline_payload = _baseline_payload_for(baseline, payload["command"])
            if baseline_payload is None:
                all_regressions.append(
                    f"missing baseline payload for command {payload['command']!r}"
                )
                continue
            try:
                all_regressions.extend(_compare_baseline(payload, baseline_payload, threshold))
            except ValueError as exc:
                all_regressions.append(str(exc))
        if all_regressions:
            print(f"\nRegressions detected (threshold={threshold:.0%}):")
            for r in all_regressions:
                print(f"  - {r}")
            return 1
        print("\nNo regressions detected against baseline.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
