"""Release manifests must be current, clean, and explicitly approved."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from scripts.check_release_readiness import readiness_errors


def _eligible(tmp_path) -> tuple[dict, dict[str, str]]:
    fingerprints = {
        "dataset_fingerprint_sha256": "dataset",
        "harness_fingerprint_sha256": "harness",
        "implementation_fingerprint_sha256": "implementation",
    }
    references = {}
    details = {
        "production_quality_passed": {
            "benchmark_valid": True,
            "benchmark_release_ready": True,
            "human_reviewed": True,
            "observed_cases": 60,
            "judge_repeats": 3,
            "independent_judge": True,
            "reranker_evaluated_queries": 60,
            "quality_thresholds_passed": True,
            "benchmark_sha256": "b" * 64,
            "holdout_sha256": "h" * 64,
        },
        "human_business_review_passed": {
            "review_complete": True,
            "coverage_eligible": True,
        },
        "performance_slo_passed": {"history_complete": True, "chat_slo_passed": True},
        "security_review_passed": {
            "dependency_audit_completed": True,
            "container_scan_completed": True,
            "unresolved_blocking_findings": 0,
        },
    }
    now = datetime.now(UTC)
    for gate, gate_details in details.items():
        path = tmp_path / f"{gate}.json"
        content = json.dumps(
            {
                "schema_version": 1,
                "gate": gate,
                "passed": True,
                "reviewer": "release-reviewer",
                "generated_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
                "implementation_fingerprints": fingerprints,
                "details": gate_details,
            },
            sort_keys=True,
        ).encode()
        path.write_bytes(content)
        references[gate] = {"path": path.name, "sha256": hashlib.sha256(content).hexdigest()}
    payload = {
        "release_ready": True,
        "implementation_fingerprints": dict(fingerprints),
        "release_gates": {
            "production_quality_passed": True,
            "human_business_review_passed": True,
            "performance_slo_passed": True,
            "security_review_passed": True,
        },
        "evidence_references": references,
    }
    return payload, fingerprints


def test_complete_matching_release_evidence_passes(tmp_path) -> None:
    payload, fingerprints = _eligible(tmp_path)
    assert readiness_errors(payload, fingerprints, evidence_root=tmp_path) == []


def test_stale_or_unreviewed_release_evidence_fails(tmp_path) -> None:
    payload, fingerprints = _eligible(tmp_path)
    payload["implementation_fingerprints"]["implementation_fingerprint_sha256"] = "old"
    payload["release_gates"]["human_business_review_passed"] = False

    errors = readiness_errors(payload, fingerprints, evidence_root=tmp_path)
    assert any("implementation_fingerprint" in error for error in errors)
    assert "human_business_review_passed must be true" in errors


def test_missing_or_tampered_artifacts_fail_closed(tmp_path) -> None:
    payload, fingerprints = _eligible(tmp_path)
    missing = payload["evidence_references"]["human_business_review_passed"]
    missing["path"] = "missing.json"
    tampered = payload["evidence_references"]["performance_slo_passed"]
    (tmp_path / tampered["path"]).write_text("{}")

    errors = readiness_errors(payload, fingerprints, evidence_root=tmp_path)

    assert any("human_business_review_passed path is missing" in error for error in errors)
    assert any("performance_slo_passed sha256 does not match" in error for error in errors)


def test_dirty_repository_fails_without_public_dirty_flag(tmp_path) -> None:
    payload, fingerprints = _eligible(tmp_path)
    errors = readiness_errors(
        payload,
        fingerprints,
        evidence_root=tmp_path,
        worktree_is_clean=False,
    )
    assert "evidence must come from a clean worktree" in errors


def test_incomplete_production_quality_evidence_fails(tmp_path) -> None:
    payload, fingerprints = _eligible(tmp_path)
    reference = payload["evidence_references"]["production_quality_passed"]
    path = tmp_path / reference["path"]
    document = json.loads(path.read_text())
    document["details"]["reranker_evaluated_queries"] = 59
    content = json.dumps(document, sort_keys=True).encode()
    path.write_bytes(content)
    reference["sha256"] = hashlib.sha256(content).hexdigest()

    errors = readiness_errors(payload, fingerprints, evidence_root=tmp_path)
    assert any("release-grade production benchmark" in error for error in errors)
