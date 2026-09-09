"""Bind human review decisions to exact candidate cases; never approve pending rows."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_REVIEW_REQUIREMENTS = {
    "minimum_accepted_cases": 60,
    "minimum_cases_by_domain": {"meeting": 30, "course_research": 30},
    "minimum_meetings": 10,
    "minimum_cross_meeting_cases": 0,
}


def _review_requirements(dataset: dict) -> tuple[dict, list[str]]:
    raw = dataset.get("review_requirements", _DEFAULT_REVIEW_REQUIREMENTS)
    errors: list[str] = []
    if not isinstance(raw, dict):
        return _DEFAULT_REVIEW_REQUIREMENTS, ["review_requirements must be an object"]
    domains = raw.get("minimum_cases_by_domain")
    if not isinstance(domains, dict) or not domains:
        errors.append("review_requirements.minimum_cases_by_domain must be non-empty")
        domains = _DEFAULT_REVIEW_REQUIREMENTS["minimum_cases_by_domain"]
    normalized_domains: dict[str, int] = {}
    for domain, count in domains.items():
        if domain not in {"meeting", "course_research"} or not isinstance(count, int) or count < 1:
            errors.append(f"invalid domain coverage requirement: {domain}={count}")
            continue
        normalized_domains[domain] = count
    accepted = raw.get("minimum_accepted_cases")
    meetings = raw.get("minimum_meetings")
    cross_meeting = raw.get("minimum_cross_meeting_cases", 0)
    if not isinstance(accepted, int) or accepted < sum(normalized_domains.values()):
        errors.append("minimum_accepted_cases must cover every required domain count")
        accepted = max(
            _DEFAULT_REVIEW_REQUIREMENTS["minimum_accepted_cases"],
            sum(normalized_domains.values()),
        )
    if not isinstance(meetings, int) or meetings < 1:
        errors.append("minimum_meetings must be a positive integer")
        meetings = _DEFAULT_REVIEW_REQUIREMENTS["minimum_meetings"]
    if not isinstance(cross_meeting, int) or not 0 <= cross_meeting <= accepted:
        errors.append("minimum_cross_meeting_cases must be between zero and accepted cases")
        cross_meeting = 0
    return {
        "minimum_accepted_cases": accepted,
        "minimum_cases_by_domain": normalized_domains,
        "minimum_meetings": meetings,
        "minimum_cross_meeting_cases": cross_meeting,
    }, errors


def case_digest(case: dict) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def review_result(dataset: dict, decisions: list[dict]) -> dict:
    cases = dataset.get("cases", [])
    requirements, errors = _review_requirements(dataset)
    by_id = {}
    for row in decisions:
        case_id = row.get("case_id", "")
        if not case_id or case_id in by_id:
            errors.append(f"duplicate or empty review ID: {case_id}")
        by_id[case_id] = row
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids) or set(by_id) != set(ids):
        errors.append("review IDs must match the candidate dataset exactly")
    accepted = []
    rejected = 0
    for case in cases:
        row = by_id.get(case["id"], {})
        before = len(errors)
        if row.get("case_sha256") != case_digest(case):
            errors.append(f"{case['id']}: candidate changed or missing fingerprint")
        if row.get("decision") not in {"accept", "reject"}:
            errors.append(f"{case['id']}: decision pending")
        if not str(row.get("reviewer", "")).strip():
            errors.append(f"{case['id']}: reviewer missing")
        try:
            reviewed = datetime.fromisoformat(
                str(row.get("reviewed_at", "")).replace("Z", "+00:00")
            )
            if reviewed.tzinfo is None or reviewed > datetime.now(UTC):
                raise ValueError("review timestamp must be timezone-aware and not in the future")
        except ValueError:
            errors.append(f"{case['id']}: invalid review timestamp")
        if row.get("decision") == "accept":
            if row.get("domain") not in {"meeting", "course_research"}:
                errors.append(f"{case['id']}: domain not reviewed")
            elif case.get("domain") and row.get("domain") != case.get("domain"):
                errors.append(f"{case['id']}: reviewed domain does not match source metadata")
            for field in ("question_ok", "answer_supported", "evidence_sufficient"):
                if row.get(field) != "yes":
                    errors.append(f"{case['id']}: {field} not confirmed")
            if len(errors) == before:
                accepted.append({**case, "domain": row["domain"], "human_review": dict(row)})
        elif row.get("decision") == "reject":
            rejected += 1
    domains = Counter(case["domain"] for case in accepted)
    meeting_ids = {
        meeting_id for case in accepted for meeting_id in case.get("expected_meeting_ids", [])
    }
    cross_meeting_cases = sum(
        len(set(case.get("expected_meeting_ids", []))) > 1 for case in accepted
    )
    coverage = bool(
        len(accepted) >= requirements["minimum_accepted_cases"]
        and all(
            domains[domain] >= minimum
            for domain, minimum in requirements["minimum_cases_by_domain"].items()
        )
        and len(meeting_ids) >= requirements["minimum_meetings"]
        and cross_meeting_cases >= requirements["minimum_cross_meeting_cases"]
    )
    return {
        "review_complete": bool(cases) and not errors,
        "accepted_cases": accepted,
        "rejected_cases": rejected,
        "errors": errors,
        "domain_counts": dict(domains),
        "meeting_count": len(meeting_ids),
        "cross_meeting_cases": cross_meeting_cases,
        "review_requirements": requirements,
        "coverage_eligible": coverage,
        "release_ready": False,
        "limitations": [
            "reviewer entries are human attestations, not independent identity authentication",
            "review does not establish model quality, paired evaluation, usability or SLO",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "validate", "approve"])
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.holdout.read_text())
    if args.command == "prepare":
        # Exclusive creation prevents overwriting reviewers' in-progress work.
        with args.decisions.open("x", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "case_id",
                    "case_sha256",
                    "decision",
                    "reviewer",
                    "reviewed_at",
                    "domain",
                    "question_ok",
                    "answer_supported",
                    "evidence_sufficient",
                    "notes",
                ],
            )
            writer.writeheader()
            for case in dataset["cases"]:
                writer.writerow(
                    {"case_id": case["id"], "case_sha256": case_digest(case), "decision": "pending"}
                )
        print("Created pending review form; no cases were approved.")
        return 0
    with args.decisions.open(encoding="utf-8-sig", newline="") as handle:
        result = review_result(dataset, list(csv.DictReader(handle)))
    if args.command == "approve":
        if not result["review_complete"] or not result["coverage_eligible"]:
            print(json.dumps({k: v for k, v in result.items() if k != "accepted_cases"}, indent=2))
            return 1
        if args.output is None:
            parser.error("approve requires --output")
        reviewed = {
            **dataset,
            "human_reviewed": True,
            "cases": result["accepted_cases"],
            "review_manifest": {k: v for k, v in result.items() if k != "accepted_cases"},
        }
        with args.output.open("x") as handle:
            json.dump(reviewed, handle, ensure_ascii=False, indent=2)
    else:
        public = {k: v for k, v in result.items() if k != "accepted_cases"}
        if args.output:
            args.output.write_text(json.dumps(public, indent=2) + "\n")
        print(json.dumps(public, indent=2))
    return 0 if result["review_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
