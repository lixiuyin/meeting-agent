"""Fail-closed validation for the versioned evaluation protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_REQUIRED_SUITES = {
    "performance",
    "rag_retrieval",
    "reranker_quality",
    "rag_answer",
    "multi_turn",
    "long_horizon_memory",
    "process_quality",
    "protocol_validity",
    "meeting_evidence_governance",
}
_REQUIRED_SOURCES = {
    "ragas",
    "knollmeyer_2026",
    "mtrag",
    "hu_2026",
    "memgym",
    "agentprocessbench",
    "shao_2026",
}
_REQUIRED_METRICS = {
    "performance": {
        "chat_ttft",
        "chat_total",
        "upload_to_ready",
        "degraded_rate",
        "category_latency",
    },
    "rag_retrieval": {"recall", "mrr", "ndcg"},
    "reranker_quality": {"mrr", "ndcg", "mrr_gain", "ndcg_gain"},
    "rag_answer": {
        "faithfulness",
        "answer_relevance",
        "context_precision",
        "context_recall",
        "correctness",
        "citation_quality",
        "corpus_isolation",
        "source_identity_recall",
    },
    "multi_turn": {"faithfulness", "appropriateness", "naturalness", "completeness"},
    "long_horizon_memory": {
        "accurate_retrieval",
        "knowledge_graph_gain",
        "knowledge_graph_multihop",
        "test_time_learning",
        "long_range_understanding",
        "selective_forgetting",
        "memory_gain",
    },
    "process_quality": {"step_accuracy", "first_error_accuracy", "error_type_accuracy"},
    "protocol_validity": {"exposure", "exploit", "mislead"},
    "meeting_evidence_governance": {
        "evidence_authority_accuracy",
        "explicit_temporal_scope_accuracy",
        "source_revision_fence_accuracy",
        "authority_label_visibility",
    },
}
_PROTOCOL_VALIDITY_CHECKS = {
    "exposure": {"withheld_information", "artifact_dataset_hashes"},
    "exploit": {"dataset_path_confinement", "immutable_dataset_hashes"},
    "mislead": {"reference_required_metrics", "source_mapping", "declared_runner_status"},
}
_REQUIRED_WITHHELD_RULES = {
    "gold evidence identifiers are never inserted into model prompts",
    "expected answer-bearing file IDs are evaluator-only and never used as retrieval scope",
    "reference answers are withheld from answer generation",
    "test fixtures and metric definitions are immutable during a comparison run",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_protocol(path: Path, *, backend_dir: Path) -> dict:
    """Return a deterministic audit report; never silently skips malformed input."""
    errors: list[str] = []
    if not path.is_file():
        return {
            "command": "protocol-audit",
            "valid": False,
            "errors": [f"protocol not found: {path}"],
        }

    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "command": "protocol-audit",
            "valid": False,
            "errors": [f"cannot read protocol: {exc}"],
        }

    if protocol.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in (
        "protocol_id",
        "intended_capabilities",
        "allowed_resources",
        "withheld_information",
        "artifact_capture",
    ):
        if not protocol.get(field):
            errors.append(f"missing non-empty field: {field}")

    withheld_rules = set(protocol.get("withheld_information", []))
    missing_withheld = sorted(_REQUIRED_WITHHELD_RULES - withheld_rules)
    if missing_withheld:
        errors.append(f"missing withheld-information rules: {', '.join(missing_withheld)}")
    artifact_capture = set(protocol.get("artifact_capture", []))
    if "protocol and dataset SHA-256 hashes" not in artifact_capture:
        errors.append("artifact capture must include protocol and dataset SHA-256 hashes")

    suites = protocol.get("suites", {})
    missing_suites = sorted(_REQUIRED_SUITES - suites.keys())
    if missing_suites:
        errors.append(f"missing suites: {', '.join(missing_suites)}")

    implementation_status: dict[str, str] = {}
    dataset_hashes: dict[str, str] = {}
    for suite_id in sorted(_REQUIRED_SUITES & suites.keys()):
        suite = suites[suite_id]
        status = suite.get("status")
        if status not in {"implemented", "partial", "specified"}:
            errors.append(f"{suite_id}: invalid status {status!r}")
        else:
            implementation_status[suite_id] = status

        metrics = set(suite.get("metrics", []))
        missing_metrics = sorted(_REQUIRED_METRICS[suite_id] - metrics)
        if missing_metrics:
            errors.append(f"{suite_id}: missing metrics: {', '.join(missing_metrics)}")

        for relative in suite.get("datasets", []):
            dataset_path = (backend_dir / relative).resolve()
            try:
                dataset_path.relative_to(backend_dir.resolve())
            except ValueError:
                errors.append(f"{suite_id}: dataset escapes backend directory: {relative}")
                continue
            if not dataset_path.is_file():
                errors.append(f"{suite_id}: dataset not found: {relative}")
                continue
            dataset_hashes[relative] = _sha256(dataset_path)

    sources = {source.get("id") for source in protocol.get("method_sources", [])}
    missing_sources = sorted(_REQUIRED_SOURCES - sources)
    if missing_sources:
        errors.append(f"missing method sources: {', '.join(missing_sources)}")

    metric_contracts = protocol.get("metric_contracts", {})
    context_recall = metric_contracts.get("context_recall", {})
    if context_recall.get("reference_required") is not True:
        errors.append("context_recall must be marked reference_required=true")
    reference_free = set(protocol.get("reference_free_metrics", []))
    if "context_recall" in reference_free or "correctness" in reference_free:
        errors.append("reference-required metrics cannot be declared reference-free")

    validity_contracts = protocol.get("protocol_validity_contracts", {})
    for metric, required_checks in _PROTOCOL_VALIDITY_CHECKS.items():
        declared_checks = set(validity_contracts.get(metric, {}).get("checks", []))
        missing_checks = sorted(required_checks - declared_checks)
        if missing_checks:
            errors.append(
                f"protocol_validity.{metric}: missing checks: {', '.join(missing_checks)}"
            )

    return {
        "command": "protocol-audit",
        "protocol_id": protocol.get("protocol_id"),
        "valid": not errors,
        "errors": errors,
        "implementation_status": implementation_status,
        "execution_ready": not errors
        and bool(implementation_status)
        and all(status == "implemented" for status in implementation_status.values()),
        "protocol_validity_checks": {
            metric: sorted(checks) for metric, checks in _PROTOCOL_VALIDITY_CHECKS.items()
        },
        "dataset_hashes": dict(sorted(dataset_hashes.items())),
    }
