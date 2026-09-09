"""Incremental multi-turn RAG evaluation helpers.

The generator sees only the conversation and fixture corpus.  Gold answers and
expected evidence remain evaluator-only data, which prevents benchmark leakage.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

_METRICS = ("faithfulness", "appropriateness", "naturalness", "completeness")


def validate_multi_turn_dataset(dataset: dict) -> None:
    """Fail closed on malformed or under-specified multi-turn cases."""
    if dataset.get("schema_version") != 2:
        raise ValueError("multi-turn dataset schema_version must be 2")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("multi-turn dataset must contain non-empty cases")

    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("multi-turn case id must be a non-empty string")
        if case_id in seen:
            raise ValueError(f"duplicate multi-turn case id: {case_id}")
        seen.add(case_id)
        fixtures = case.get("fixture_files")
        if (
            not isinstance(fixtures, list)
            or not fixtures
            or not all(isinstance(item, str) and item for item in fixtures)
        ):
            raise ValueError(f"{case_id}: fixture_files must be non-empty strings")
        turns = case.get("turns")
        if not isinstance(turns, list) or len(turns) < 2:
            raise ValueError(f"{case_id}: at least two turns are required")
        for index, turn in enumerate(turns, start=1):
            if not isinstance(turn, dict) or not isinstance(turn.get("question"), str):
                raise ValueError(f"{case_id} turn {index}: question is required")
            answerability = turn.get("answerability")
            if answerability not in {"answerable", "unanswerable"}:
                raise ValueError(f"{case_id} turn {index}: invalid answerability")
            if answerability == "answerable" and not turn.get("expected_answer"):
                raise ValueError(f"{case_id} turn {index}: expected_answer is required")
            if answerability == "unanswerable" and not turn.get("expected_behavior"):
                raise ValueError(f"{case_id} turn {index}: expected_behavior is required")
            evidence = turn.get("expected_evidence", [])
            if answerability == "answerable" and not evidence:
                raise ValueError(f"{case_id} turn {index}: expected_evidence is required")
            if not isinstance(evidence, list):
                raise ValueError(f"{case_id} turn {index}: expected_evidence must be a list")
            for item in evidence:
                if not isinstance(item, dict):
                    raise ValueError(
                        f"{case_id} turn {index}: expected evidence must use stable objects"
                    )
                file_name = item.get("file_name")
                anchors = item.get("content_contains")
                if file_name not in fixtures:
                    raise ValueError(
                        f"{case_id} turn {index}: evidence file must be a declared fixture"
                    )
                if (
                    not isinstance(anchors, list)
                    or not anchors
                    or not all(isinstance(anchor, str) and anchor.strip() for anchor in anchors)
                ):
                    raise ValueError(
                        f"{case_id} turn {index}: content_contains must be non-empty strings"
                    )


def _source_artifact(source: dict) -> dict:
    """Keep reproducible synthetic-source fields and exclude runtime IDs/paths."""
    content = str(source.get("content", ""))
    artifact = {
        key: source.get(key)
        for key in (
            "file_name",
            "file_type",
            "chunk_index",
            "page_number",
            "slide_number",
            "source_kind",
            "score",
        )
        if source.get(key) is not None
    }
    artifact["content"] = content
    artifact["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    return artifact


def _evidence_ref(source: dict) -> str | None:
    file_name = source.get("file_name")
    chunk_index = source.get("chunk_index")
    if not isinstance(file_name, str) or not isinstance(chunk_index, int):
        return None
    return f"{file_name}#chunk_{chunk_index}"


def _matches_expected_evidence(source: dict, expected: dict) -> bool:
    """Match stable evidence identity without depending on runtime chunk numbers."""
    if source.get("file_name") != expected["file_name"]:
        return False
    content = str(source.get("content", "")).casefold()
    return all(anchor.casefold() in content for anchor in expected["content_contains"])


async def execute_multi_turn_cases(
    dataset: dict,
    *,
    ask_fn: Callable[..., Awaitable[Any]],
    judge_fn: Callable[..., dict | None],
    fixture_info: dict[str, dict[str, tuple[int, int]]],
    judge_repeats: int,
) -> dict:
    """Execute validated cases and return raw rows plus aggregate metrics."""
    validate_multi_turn_dataset(dataset)
    if judge_repeats < 1:
        raise ValueError("judge_repeats must be at least 1")

    rows: list[dict] = []
    parse_failures = 0
    judge_parse_retries = 0
    session_continuity: list[float] = []
    evidence_scores: list[float] = []
    corpus_isolation: list[float] = []
    validity_errors: list[str] = []

    for case in dataset["cases"]:
        declared_files = set(case["fixture_files"])
        # ``ingest_fixtures`` intentionally stores all benchmark files in one
        # meeting.  Meeting-level scoping would therefore expose undeclared
        # fixtures to every case and invalidate the benchmark.  File-level
        # scoping is the capability under test and gives each case exactly the
        # corpus declared in ``fixture_files``.
        case_fixture_info = fixture_info.get(case["id"])
        if case_fixture_info is None:
            raise ValueError(f"{case['id']}: fixture IDs were not ingested")
        file_ids = sorted({case_fixture_info[name][1] for name in case["fixture_files"]})
        session_id: str | None = None
        first_session_id: str | None = None
        history: list[dict[str, str]] = []
        case_rows: list[dict] = []

        for turn_index, turn in enumerate(case["turns"], start=1):
            result = await ask_fn(
                question=turn["question"],
                user_id=f"benchmark-multi-turn-{case['id']}",
                file_ids=file_ids,
                session_id=session_id,
            )
            session_id = result.session_id
            if first_session_id is None:
                first_session_id = session_id
            session_continuity.append(float(session_id == first_session_id))

            sources = list(result.sources or [])
            chunks = [str(source.get("content", "")) for source in sources]
            unexpected_source_files = sorted(
                {
                    str(source.get("file_name"))
                    for source in sources
                    if source.get("file_name") not in declared_files
                }
            )
            isolated = not unexpected_source_files
            corpus_isolation.append(float(isolated))
            if not isolated:
                validity_errors.append(
                    f"{case['id']} turn {turn_index}: undeclared sources: "
                    + ", ".join(unexpected_source_files)
                )
            expected_evidence = list(turn.get("expected_evidence", []))
            observed_evidence = {
                ref for source in sources if (ref := _evidence_ref(source)) is not None
            }
            matched_expected_evidence = [
                expected
                for expected in expected_evidence
                if any(_matches_expected_evidence(source, expected) for source in sources)
            ]
            if expected_evidence:
                evidence_recall = len(matched_expected_evidence) / len(expected_evidence)
                evidence_scores.append(evidence_recall)
                if turn["answerability"] == "answerable" and evidence_recall == 0:
                    validity_errors.append(
                        f"{case['id']} turn {turn_index}: answerable turn retrieved "
                        "none of its expected evidence"
                    )

            diagnostics: list[dict | None] = []
            scores: dict[str, list[float]] = {metric: [] for metric in _METRICS}
            for _ in range(judge_repeats):
                diagnostic = await asyncio.to_thread(
                    judge_fn,
                    history=history,
                    question=turn["question"],
                    answer=result.answer,
                    context="\n\n".join(chunks),
                    answerability=turn["answerability"],
                    reference_answer=turn.get("expected_answer"),
                    expected_behavior=turn.get("expected_behavior"),
                )
                diagnostics.append(diagnostic)
                if diagnostic is None:
                    parse_failures += len(_METRICS)
                    continue
                judge_parse_retries += int(diagnostic.get("parse_retries", 0))
                metric_payload = diagnostic.get("metrics", {})
                for metric in _METRICS:
                    value = metric_payload.get(metric, {}).get("score")
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        scores[metric].append(float(value))
                    else:
                        parse_failures += 1

            row = {
                "case_id": case["id"],
                "turn_index": turn_index,
                "turn_type": turn.get("turn_type"),
                "answerability": turn["answerability"],
                "question": turn["question"],
                "expected_answer": turn.get("expected_answer"),
                "expected_behavior": turn.get("expected_behavior"),
                "expected_evidence": expected_evidence,
                "matched_expected_evidence": matched_expected_evidence,
                "observed_evidence": sorted(observed_evidence),
                "unexpected_source_files": unexpected_source_files,
                "corpus_isolation": isolated,
                "evidence_recall": (
                    len(matched_expected_evidence) / len(expected_evidence)
                    if expected_evidence
                    else None
                ),
                "answer": result.answer,
                "sources": [_source_artifact(source) for source in sources],
                "trace": result.trace,
                "session_continuity": session_id == first_session_id,
                "judge_diagnostics": diagnostics,
            }
            for metric, values in scores.items():
                row[metric] = sum(values) / len(values) if values else None
            rows.append(row)
            case_rows.append(row)
            history.append({"question": turn["question"], "answer": result.answer})

        # A case-level flag is easier to audit than only an aggregate rate.
        for row in case_rows:
            row["case_session_continuity"] = all(
                bool(item["session_continuity"]) for item in case_rows
            )

    def _mean(key: str) -> float | None:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return sum(values) / len(values) if values else None

    stats = {metric: _mean(metric) for metric in _METRICS}
    stats.update(
        {
            "evidence_recall": (
                sum(evidence_scores) / len(evidence_scores) if evidence_scores else None
            ),
            "session_continuity": (
                sum(session_continuity) / len(session_continuity) if session_continuity else None
            ),
            "corpus_isolation": (
                sum(corpus_isolation) / len(corpus_isolation) if corpus_isolation else None
            ),
            "parse_failures": parse_failures,
            "judge_parse_retries": judge_parse_retries,
        }
    )
    return {
        "valid": not validity_errors,
        "validity_errors": validity_errors,
        "stats": stats,
        "rows": rows,
    }
