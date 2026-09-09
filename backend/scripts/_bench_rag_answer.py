"""Fail-closed validation for archived RAG answer-quality reports."""

from __future__ import annotations

from collections import Counter
from typing import Any

REQUIRED_METRICS = (
    "faithfulness",
    "answer_relevance",
    "context_precision",
    "context_recall",
    "correctness",
    "citation_quality",
)


def _unit_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 <= float(value) <= 1.0
    )


def validate_rag_answer_rows(
    rows: list[dict],
    *,
    expected_query_ids: list[str],
    expected_files_by_query: dict[str, list[str]],
    corpus_files_by_query: dict[str, list[str]],
    judge_repeats: int,
) -> dict:
    """Validate case coverage and every judge artifact before baseline use."""
    errors: list[str] = []
    expected = list(expected_query_ids)
    observed = [row.get("query_id") for row in rows]
    duplicates = sorted(key for key, count in Counter(observed).items() if count > 1)
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected), key=str)
    if duplicates:
        errors.append(f"duplicate query ids: {', '.join(map(str, duplicates))}")
    if missing:
        errors.append(f"missing query ids: {', '.join(missing)}")
    if unexpected:
        errors.append(f"unexpected query ids: {', '.join(map(str, unexpected))}")
    if len(rows) != len(expected):
        errors.append(f"case count mismatch: expected {len(expected)}, observed {len(rows)}")

    for row in rows:
        query_id = str(row.get("query_id", "<missing>"))
        if not isinstance(row.get("answer"), str) or not row["answer"].strip():
            errors.append(f"{query_id}: missing generated answer")
        sources = row.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{query_id}: missing retrieved sources")
            sources = []
        expected_files = expected_files_by_query.get(query_id)
        if not isinstance(expected_files, list) or not expected_files:
            errors.append(f"{query_id}: missing expected source files")
            expected_files = []
        corpus_files = corpus_files_by_query.get(query_id)
        if not isinstance(corpus_files, list) or not corpus_files:
            errors.append(f"{query_id}: missing declared corpus files")
            corpus_files = []
        observed_files = sorted(
            {
                source.get("file_name")
                for source in sources
                if isinstance(source, dict)
                and isinstance(source.get("file_name"), str)
                and source["file_name"]
            }
        )
        expected_set = set(expected_files)
        corpus_set = set(corpus_files)
        observed_set = set(observed_files)
        source_identity_recall = (
            len(expected_set & observed_set) / len(expected_set) if expected_set else None
        )
        unexpected_files = sorted(observed_set - corpus_set)
        corpus_isolation = float(not unexpected_files)
        if row.get("expected_files") != expected_files:
            errors.append(f"{query_id}: expected source files do not match dataset")
        if row.get("corpus_files") != corpus_files:
            errors.append(f"{query_id}: corpus files do not match dataset")
        if row.get("observed_files") != observed_files:
            errors.append(f"{query_id}: observed source files do not match artifacts")
        if row.get("unexpected_source_files") != unexpected_files:
            errors.append(f"{query_id}: unexpected source files do not match artifacts")
        if row.get("source_identity_recall") != source_identity_recall:
            errors.append(f"{query_id}: source identity recall does not match artifacts")
        if row.get("corpus_isolation") != corpus_isolation:
            errors.append(f"{query_id}: corpus isolation does not match artifacts")
        trace = row.get("trace")
        if not isinstance(trace, dict) or not isinstance(trace.get("spans"), list):
            errors.append(f"{query_id}: missing process trace")

        diagnostics = row.get("judge_diagnostics")
        if not isinstance(diagnostics, dict):
            errors.append(f"{query_id}: missing judge diagnostics")
            diagnostics = {}
        for metric in REQUIRED_METRICS:
            if not _unit_score(row.get(metric)):
                errors.append(f"{query_id}: invalid {metric} score")
            attempts = diagnostics.get(metric)
            if not isinstance(attempts, list) or len(attempts) != judge_repeats:
                errors.append(
                    f"{query_id}: {metric} diagnostics must contain {judge_repeats} result(s)"
                )
            elif any(not isinstance(item, dict) for item in attempts):
                errors.append(f"{query_id}: {metric} judge parse failure")

    return {
        "valid": not errors,
        "complete": not errors,
        "validity_errors": errors,
        "counts": {
            "expected_cases": len(expected),
            "observed_cases": len(rows),
            "judge_repeats": judge_repeats,
        },
    }
