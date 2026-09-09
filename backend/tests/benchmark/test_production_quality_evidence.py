import pytest

from scripts.build_production_quality_evidence import build_evidence


def _eligible():
    holdout = {
        "human_reviewed": True,
        "review_manifest": {"coverage_eligible": True},
    }
    report = {
        "valid": True,
        "system_model": "system",
        "judge_model": "judge",
        "judge_repeats": 3,
        "judge_parse_failures": 0,
        "holdout_sha256": "h" * 64,
        "quality_thresholds": {"correctness": 0.7},
        "stats": {"correctness": 0.8},
        "evidence_quality": {
            "release_ready": True,
            "limitations": [],
            "observed_cases": 30,
            "reranker_evaluated_queries": 30,
        },
        "rows": [{"id": index} for index in range(30)],
    }
    return report, holdout


def test_build_evidence_binds_complete_release_grade_report():
    report, holdout = _eligible()
    details = build_evidence(
        report,
        holdout,
        report_sha256="r" * 64,
        holdout_sha256="h" * 64,
    )
    assert details["observed_cases"] == 30
    assert details["reranker_evaluated_queries"] == 30
    assert details["quality_thresholds_passed"] is True


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evidence_quality", "reranker_evaluated_queries"), 29),
        (("evidence_quality", "release_ready"), False),
        (("judge_parse_failures",), 1),
        (("stats", "correctness"), 0.6),
    ],
)
def test_build_evidence_rejects_incomplete_quality(path, value):
    report, holdout = _eligible()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="not complete release-grade"):
        build_evidence(
            report,
            holdout,
            report_sha256="r" * 64,
            holdout_sha256="h" * 64,
        )
