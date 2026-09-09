"""Build a compact release-gate attestation from a private production benchmark.

The private holdout and per-case report remain untracked.  This command emits
only hashes, aggregate eligibility fields, and current implementation
fingerprints; it refuses to turn an invalid or incomplete report into evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .benchmark import _capture_e2e_fingerprints


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_evidence(report: dict, holdout: dict, *, report_sha256: str, holdout_sha256: str) -> dict:
    quality = report.get("evidence_quality") or {}
    rows = report.get("rows") or []
    thresholds = report.get("quality_thresholds") or {}
    stats = report.get("stats") or {}
    thresholds_passed = bool(thresholds) and all(
        isinstance(stats.get(metric), (int, float)) and stats[metric] >= minimum
        for metric, minimum in thresholds.items()
    )
    observed_cases = quality.get("observed_cases")
    reranked = quality.get("reranker_evaluated_queries")
    eligible = bool(
        report.get("valid") is True
        and quality.get("release_ready") is True
        and quality.get("limitations") == []
        and holdout.get("human_reviewed") is True
        and (holdout.get("review_manifest") or {}).get("coverage_eligible") is True
        and isinstance(observed_cases, int)
        and observed_cases >= 30
        and len(rows) == observed_cases
        and isinstance(report.get("judge_repeats"), int)
        and report["judge_repeats"] >= 3
        and report.get("system_model") != report.get("judge_model")
        and reranked == observed_cases
        and report.get("judge_parse_failures") == 0
        and thresholds_passed
        and report.get("holdout_sha256") == holdout_sha256
    )
    if not eligible:
        raise ValueError("production benchmark is not complete release-grade evidence")
    return {
        "benchmark_valid": True,
        "benchmark_release_ready": True,
        "human_reviewed": True,
        "observed_cases": observed_cases,
        "judge_repeats": report["judge_repeats"],
        "independent_judge": True,
        "reranker_evaluated_queries": reranked,
        "quality_thresholds_passed": True,
        "benchmark_sha256": report_sha256,
        "holdout_sha256": holdout_sha256,
        "aggregate_metrics": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--expires-days", type=int, default=7)
    args = parser.parse_args()
    if args.expires_days < 1:
        parser.error("--expires-days must be positive")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    holdout = json.loads(args.holdout.read_text(encoding="utf-8"))
    details = build_evidence(
        report,
        holdout,
        report_sha256=_sha256(args.report),
        holdout_sha256=_sha256(args.holdout),
    )
    now = datetime.now(UTC)
    payload = {
        "schema_version": 1,
        "gate": "production_quality_passed",
        "passed": True,
        "reviewer": args.reviewer,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=args.expires_days)).isoformat(),
        "implementation_fingerprints": _capture_e2e_fingerprints(),
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
