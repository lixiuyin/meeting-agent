"""Deterministic evaluation of meeting-evidence safety contracts."""

from __future__ import annotations

from typing import Any


def validate_evidence_governance_dataset(dataset: dict[str, Any]) -> None:
    if dataset.get("schema_version") != 1:
        raise ValueError("evidence governance schema_version must be 1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("evidence governance cases must be non-empty")
    seen: set[str] = set()
    supported = {"authority", "temporal", "revision", "formatting"}
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("evidence governance case ids must be unique non-empty strings")
        seen.add(case_id)
        if case.get("policy") not in supported:
            raise ValueError(f"{case_id}: unsupported policy")
        if not isinstance(case.get("expected_included"), bool):
            raise ValueError(f"{case_id}: expected_included must be boolean")


def execute_evidence_governance_cases(dataset: dict[str, Any]) -> dict[str, Any]:
    """Run policy cases through production helpers without external providers."""
    from src.core.source_revision_fence import meeting_file_source_matches
    from src.services.chain._formatting import _format_docs
    from src.services.chain._retrieve_filters import (
        _apply_content_type_bias,
        _apply_temporal_filter,
    )
    from src.services.rag._query_analysis import TemporalHint

    validate_evidence_governance_dataset(dataset)
    rows: list[dict[str, Any]] = []
    by_policy: dict[str, list[float]] = {}
    for case in dataset["cases"]:
        policy = str(case["policy"])
        if policy == "authority":
            docs = [
                {
                    "content": "Candidate decision",
                    "metadata": {
                        "material_role": "decision_log",
                        "approval_status": case["approval_status"],
                    },
                    "score": 0.9,
                }
            ]
            observed = bool(_apply_content_type_bias(str(case["query"]), docs))
        elif policy == "temporal":
            start, end = case["chunk_seconds"]
            range_start, range_end = case["range_seconds"]
            docs = [
                {
                    "content": "Timestamped evidence",
                    "metadata": {
                        "timestamp_start": start,
                        "timestamp_end": end,
                        "meeting_duration": case["meeting_duration"],
                    },
                    "score": 0.9,
                }
            ]
            observed = bool(
                _apply_temporal_filter(
                    docs,
                    TemporalHint(
                        ratio_min=range_start / case["meeting_duration"],
                        ratio_max=range_end / case["meeting_duration"],
                        absolute_seconds=(range_start, range_end),
                    ),
                )
            )
        elif policy == "revision":
            observed = meeting_file_source_matches(
                {"source_revision": case["current_revision"]},
                str(case["expected_revision"]),
            )
        else:
            formatted = _format_docs(
                [
                    {
                        "content": "Candidate decision",
                        "metadata": {
                            "meeting_id": 1,
                            "material_role": case["material_role"],
                            "approval_status": case["approval_status"],
                        },
                    }
                ]
            )
            observed = all(
                marker in formatted
                for marker in (
                    f"role={case['material_role']}",
                    f"approval={case['approval_status']}",
                )
            )
        correct = observed is case["expected_included"]
        by_policy.setdefault(policy, []).append(float(correct))
        rows.append(
            {
                "case_id": case["id"],
                "policy": policy,
                "expected_included": case["expected_included"],
                "observed_included": observed,
                "correct": correct,
            }
        )

    metric_names = {
        "authority": "evidence_authority_accuracy",
        "temporal": "explicit_temporal_scope_accuracy",
        "revision": "source_revision_fence_accuracy",
        "formatting": "authority_label_visibility",
    }
    stats = {
        metric_names[policy]: sum(values) / len(values)
        for policy, values in sorted(by_policy.items())
    }
    return {
        "command": "evidence-governance",
        "valid": all(row["correct"] for row in rows),
        "complete": len(rows) == len(dataset["cases"]),
        "stats": stats,
        "rows": rows,
    }
