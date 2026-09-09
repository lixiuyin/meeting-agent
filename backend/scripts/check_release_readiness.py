"""Fail closed unless release evidence matches the current implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import _capture_e2e_fingerprints

_REQUIRED_GATES = (
    "production_quality_passed",
    "human_business_review_passed",
    "performance_slo_passed",
    "security_review_passed",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repository_is_clean(root: Path = _REPO_ROOT) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _validate_evidence_reference(
    reference: object,
    *,
    gate: str,
    root: Path,
    current_fingerprints: dict[str, str],
) -> list[str]:
    prefix = f"evidence for {gate}"
    if not isinstance(reference, dict):
        return [f"{prefix} must be an object"]
    relative_path = reference.get("path")
    expected_digest = reference.get("sha256")
    if not isinstance(relative_path, str) or not relative_path.strip():
        return [f"{prefix} path is missing"]
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        return [f"{prefix} sha256 is missing or invalid"]
    candidate = root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return [f"{prefix} path is missing or outside the repository"]
    if candidate.is_symlink() or not resolved.is_file():
        return [f"{prefix} must reference a regular non-symlink file"]
    content = resolved.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest.lower():
        return [f"{prefix} sha256 does not match"]
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return [f"{prefix} is not valid JSON"]
    if not isinstance(document, dict):
        return [f"{prefix} must contain a JSON object"]

    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append(f"{prefix} schema_version must be 1")
    if document.get("gate") != gate or document.get("passed") is not True:
        errors.append(f"{prefix} does not attest the required passing gate")
    if not str(document.get("reviewer") or "").strip():
        errors.append(f"{prefix} reviewer is missing")
    recorded = document.get("implementation_fingerprints")
    if not isinstance(recorded, dict) or any(
        recorded.get(name) != digest for name, digest in current_fingerprints.items()
    ):
        errors.append(f"{prefix} fingerprints do not match the release implementation")
    generated_at = _parse_time(document.get("generated_at"))
    expires_at = _parse_time(document.get("expires_at"))
    now = datetime.now(UTC)
    if generated_at is None or generated_at > now:
        errors.append(f"{prefix} generated_at is invalid")
    if expires_at is None or expires_at <= now:
        errors.append(f"{prefix} is expired or has no valid expiry")
    details = document.get("details")
    if not isinstance(details, dict):
        errors.append(f"{prefix} details are missing")
    elif gate == "production_quality_passed" and not (
        details.get("benchmark_valid") is True
        and details.get("benchmark_release_ready") is True
        and details.get("human_reviewed") is True
        and isinstance(details.get("observed_cases"), int)
        and details["observed_cases"] >= 30
        and isinstance(details.get("judge_repeats"), int)
        and details["judge_repeats"] >= 3
        and details.get("independent_judge") is True
        and details.get("reranker_evaluated_queries") == details.get("observed_cases")
        and details.get("quality_thresholds_passed") is True
        and isinstance(details.get("benchmark_sha256"), str)
        and len(details["benchmark_sha256"]) == 64
        and isinstance(details.get("holdout_sha256"), str)
        and len(details["holdout_sha256"]) == 64
    ):
        errors.append(f"{prefix} lacks a complete release-grade production benchmark")
    elif gate == "human_business_review_passed" and not (
        details.get("review_complete") is True and details.get("coverage_eligible") is True
    ):
        errors.append(f"{prefix} lacks complete and coverage-eligible human review")
    elif gate == "performance_slo_passed" and not (
        details.get("history_complete") is True and details.get("chat_slo_passed") is True
    ):
        errors.append(f"{prefix} lacks a complete passing SLO window")
    elif gate == "security_review_passed" and not (
        details.get("dependency_audit_completed") is True
        and details.get("container_scan_completed") is True
        and details.get("unresolved_blocking_findings") == 0
    ):
        errors.append(f"{prefix} lacks complete dependency/container security evidence")
    return errors


def readiness_errors(
    payload: dict,
    current_fingerprints: dict[str, str],
    *,
    evidence_root: Path = _REPO_ROOT,
    worktree_is_clean: bool = True,
) -> list[str]:
    """Return every reason an evidence manifest is not release eligible."""
    errors: list[str] = []
    if payload.get("release_ready") is not True:
        errors.append("release_ready must be true")
    if not worktree_is_clean:
        errors.append("evidence must come from a clean worktree")

    recorded = payload.get("implementation_fingerprints")
    if not isinstance(recorded, dict):
        errors.append("implementation_fingerprints is missing")
    else:
        for name, current in current_fingerprints.items():
            if recorded.get(name) != current:
                errors.append(f"{name} does not match the release implementation")

    gates = payload.get("release_gates")
    if not isinstance(gates, dict):
        errors.append("release_gates is missing")
    else:
        for gate in _REQUIRED_GATES:
            if gates.get(gate) is not True:
                errors.append(f"{gate} must be true")
    references = payload.get("evidence_references")
    if not isinstance(references, dict):
        errors.append("evidence_references must map every release gate to a verified artifact")
    else:
        for gate in _REQUIRED_GATES:
            errors.extend(
                _validate_evidence_reference(
                    references.get(gate),
                    gate=gate,
                    root=evidence_root,
                    current_fingerprints=current_fingerprints,
                )
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read release evidence: {exc}")
        return 1
    if not isinstance(payload, dict):
        print("FAIL: release evidence must be a JSON object")
        return 1

    errors = readiness_errors(
        payload,
        _capture_e2e_fingerprints(),
        worktree_is_clean=_repository_is_clean(),
    )
    if errors:
        print("FAIL: release evidence is not eligible:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: release evidence matches the current implementation and all required gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
