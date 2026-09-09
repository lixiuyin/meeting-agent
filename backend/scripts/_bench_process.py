"""Deterministic process-quality evaluation for captured E2E traces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


def validate_process_expectations(expectations: dict) -> None:
    """Fail closed when the process evaluation contract is incomplete."""
    if expectations.get("schema_version") != 1:
        raise ValueError("process expectations schema_version must be 1")
    trajectories = expectations.get("trajectories")
    if not isinstance(trajectories, dict):
        raise ValueError("process expectations must define trajectories")
    for name in ("chat_success", "ingest_success", "failure"):
        trajectory = trajectories.get(name)
        if not isinstance(trajectory, dict):
            raise ValueError(f"process expectations missing trajectory: {name}")
        artifacts = trajectory.get("required_artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError(f"{name}: required_artifacts must be non-empty")
    aliases = expectations.get("span_aliases")
    if not isinstance(aliases, dict) or not aliases.get("parse_or_transcribe"):
        raise ValueError("process expectations must define parse_or_transcribe alias")
    failure_cases = expectations.get("failure_cases")
    if not isinstance(failure_cases, list) or not failure_cases:
        raise ValueError("process expectations must define non-empty failure_cases")
    seen: set[str] = set()
    seen_runners: set[str] = set()
    for case in failure_cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("process failure case ids must be unique non-empty strings")
        seen.add(case_id)
        for field in ("runner", "expected_error_span", "expected_error_type"):
            if not isinstance(case.get(field), str) or not case[field]:
                raise ValueError(f"{case_id}: {field} is required")
        runner = case["runner"]
        if runner in seen_runners:
            raise ValueError(f"process failure case runners must be unique: {runner}")
        seen_runners.add(runner)


async def execute_failure_cases(
    expectations: dict,
    *,
    runners: dict[str, Callable[[], Awaitable[dict]]],
) -> list[dict]:
    """Run predeclared isolated faults and attach evaluator-only expectations."""
    validate_process_expectations(expectations)
    traces: list[dict] = []
    for case in expectations["failure_cases"]:
        runner = runners.get(case["runner"])
        if runner is None:
            raise ValueError(f"no failure runner registered for {case['runner']!r}")
        trace = dict(await runner())
        trace.update(
            {
                "case_id": case["id"],
                "expected_error_span": case["expected_error_span"],
                "expected_error_type": case["expected_error_type"],
            }
        )
        traces.append(trace)
    return traces


def _artifact_present(trace: dict, artifact: str) -> bool:
    value = trace.get(artifact)
    return value is not None and value != "" and value != []


def _matching_span(spans: list[dict], label: str, aliases: dict[str, list[str]]) -> dict | None:
    accepted = set(aliases.get(label, [label]))
    return next((span for span in spans if span.get("label") in accepted), None)


def _first_error(trace: dict) -> str | None:
    explicit = trace.get("error_span")
    if isinstance(explicit, str) and explicit:
        return explicit
    error_spans = [
        span
        for span in trace.get("spans", [])
        if isinstance(span, dict) and span.get("status") == "error"
    ]
    if not error_spans:
        return None
    # ``pipeline`` is a propagation wrapper, not a causal operation. Prefer
    # the first concrete failing span when both are present.
    causal_spans = [span for span in error_spans if span.get("label") != "pipeline"]
    candidates = causal_spans or error_spans
    candidates.sort(key=lambda span: (span.get("sequence", 10**9), span.get("start_offset_ms", 0)))
    label = candidates[0].get("label")
    return label if isinstance(label, str) else None


def evaluate_process_report(expectations: dict, report: dict) -> dict:
    """Score process conformance without invoking a model or external service."""
    validate_process_expectations(expectations)
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise ValueError("process report must contain diagnostics")

    aliases = expectations["span_aliases"]
    rows: list[dict[str, Any]] = []
    validity_errors: list[str] = []
    required_steps = 0
    effective_steps = 0
    required_artifacts = 0
    present_artifacts = 0
    terminal_checks = 0
    terminal_matches = 0

    success_traces = {
        "chat_success": diagnostics.get("chat_trace"),
        "ingest_success": diagnostics.get("ingest_trace"),
    }
    for trajectory_name, trace in success_traces.items():
        if not isinstance(trace, dict):
            validity_errors.append(f"missing {trajectory_name} trace")
            continue
        expected = expectations["trajectories"][trajectory_name]
        spans = trace.get("spans")
        if not isinstance(spans, list):
            validity_errors.append(f"{trajectory_name}: spans must be a list")
            continue

        step_rows = []
        for label in expected["required_spans"]:
            required_steps += 1
            span = _matching_span(spans, label, aliases)
            observed_status = span.get("status") if span else "missing"
            effective = span is not None and observed_status == "success"
            effective_steps += int(effective)
            step_rows.append(
                {
                    "expected_label": label,
                    "observed_label": span.get("label") if span else None,
                    "observed_status": observed_status,
                    "label": 1 if effective else -1,
                }
            )

        artifact_rows = []
        for artifact in expected["required_artifacts"]:
            required_artifacts += 1
            present = _artifact_present(trace, artifact)
            present_artifacts += int(present)
            artifact_rows.append({"artifact": artifact, "present": present})

        terminal_checks += 1
        terminal_match = trace.get("terminal_status") == expected["required_terminal_status"]
        terminal_matches += int(terminal_match)
        rows.append(
            {
                "trajectory": trajectory_name,
                "trace_id": trace.get("trace_id"),
                "steps": step_rows,
                "artifacts": artifact_rows,
                "terminal_match": terminal_match,
            }
        )

    failure_traces = diagnostics.get("failure_traces", [])
    if not isinstance(failure_traces, list):
        validity_errors.append("failure_traces must be a list when provided")
        failure_traces = []
    expected_cases = {case["id"]: case for case in expectations["failure_cases"]}
    observed_case_ids: set[str] = set()
    first_error_matches = 0
    error_type_matches = 0
    scored_failure_traces = 0
    for index, trace in enumerate(failure_traces):
        if not isinstance(trace, dict):
            validity_errors.append(f"failure trace {index}: trace must be an object")
            continue
        case_id = trace.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            validity_errors.append(f"failure trace {index}: case_id is required")
            continue
        if case_id not in expected_cases:
            validity_errors.append(f"failure trace {index}: unknown case_id {case_id!r}")
            continue
        if case_id in observed_case_ids:
            validity_errors.append(f"failure trace {index}: duplicate case_id {case_id!r}")
            continue
        observed_case_ids.add(case_id)
        declared = expected_cases[case_id]
        if trace.get("expected_error_span") != declared["expected_error_span"]:
            validity_errors.append(
                f"{case_id}: attached expected_error_span does not match dataset"
            )
            continue
        if trace.get("expected_error_type") != declared["expected_error_type"]:
            validity_errors.append(
                f"{case_id}: attached expected_error_type does not match dataset"
            )
            continue
        spans = trace.get("spans")
        if not isinstance(spans, list):
            validity_errors.append(f"{case_id}: spans must be a list")
            continue
        observed = _first_error(trace)
        expected = declared["expected_error_span"]
        failure_expected = expectations["trajectories"]["failure"]
        artifact_rows = []
        for artifact in failure_expected["required_artifacts"]:
            required_artifacts += 1
            present = _artifact_present(trace, artifact)
            present_artifacts += int(present)
            artifact_rows.append({"artifact": artifact, "present": present})
        terminal_checks += 1
        terminal_match = (
            trace.get("terminal_status") == failure_expected["required_terminal_status"]
        )
        terminal_matches += int(terminal_match)
        scored_failure_traces += 1
        matched = observed == expected
        error_type_matched = trace.get("error_type") == declared["expected_error_type"]
        first_error_matches += int(matched)
        error_type_matches += int(error_type_matched)
        rows.append(
            {
                "trajectory": "failure",
                "case_id": case_id,
                "trace_id": trace.get("trace_id"),
                "expected_error_span": expected,
                "observed_error_span": observed,
                "first_error_match": matched,
                "expected_error_type": declared["expected_error_type"],
                "observed_error_type": trace.get("error_type"),
                "error_type_match": error_type_matched,
                "artifacts": artifact_rows,
                "terminal_match": terminal_match,
            }
        )

    stats = {
        "step_accuracy": effective_steps / required_steps if required_steps else None,
        "first_error_accuracy": (
            first_error_matches / scored_failure_traces if scored_failure_traces else None
        ),
        "error_type_accuracy": (
            error_type_matches / scored_failure_traces if scored_failure_traces else None
        ),
        "artifact_coverage": (
            present_artifacts / required_artifacts if required_artifacts else None
        ),
        "terminal_accuracy": terminal_matches / terminal_checks if terminal_checks else None,
        "success_trace_count": sum(isinstance(trace, dict) for trace in success_traces.values()),
        "failure_trace_count": scored_failure_traces,
    }
    return {
        "valid": not validity_errors,
        "complete": not validity_errors and observed_case_ids == set(expected_cases),
        "validity_errors": validity_errors,
        "stats": stats,
        "rows": rows,
    }
