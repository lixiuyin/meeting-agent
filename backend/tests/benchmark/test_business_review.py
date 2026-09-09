from scripts.business_review import case_digest, review_result


def decision(case, **changes):
    return {
        "case_id": case["id"],
        "case_sha256": case_digest(case),
        "decision": "accept",
        "reviewer": "Test reviewer",
        "reviewed_at": "2026-01-01T00:00:00Z",
        "domain": "meeting",
        "question_ok": "yes",
        "answer_supported": "yes",
        "evidence_sufficient": "yes",
        **changes,
    }


def test_pending_decision_cannot_approve_a_dataset():
    case = {"id": "one", "question": "Who owns this?", "reference_answer": "Maya"}
    result = review_result({"cases": [case]}, [decision(case, decision="pending")])
    assert not result["review_complete"] and not result["accepted_cases"]


def test_case_changes_invalidate_prior_human_review():
    case = {"id": "one", "reference_answer": "Maya"}
    result = review_result({"cases": [{**case, "reference_answer": "Jon"}]}, [decision(case)])
    assert not result["review_complete"] and not result["accepted_cases"]


def test_valid_small_review_does_not_claim_coverage_or_release():
    case = {"id": "one", "expected_meeting_ids": [1]}
    result = review_result({"cases": [case]}, [decision(case)])
    assert result["review_complete"] and len(result["accepted_cases"]) == 1
    assert not result["coverage_eligible"] and not result["release_ready"]


def test_dataset_specific_review_coverage_can_close() -> None:
    cases = [
        {
            "id": f"case-{index}",
            "domain": "meeting",
            "expected_meeting_ids": [index],
        }
        for index in range(1, 3)
    ]
    dataset = {
        "review_requirements": {
            "minimum_accepted_cases": 2,
            "minimum_cases_by_domain": {"meeting": 2},
            "minimum_meetings": 2,
            "minimum_cross_meeting_cases": 1,
        },
        "cases": cases,
    }
    cases[0]["expected_meeting_ids"] = [1, 2]
    cases[0]["case_type"] = "cross_meeting"
    decisions = [decision(case) for case in cases]
    decisions[0]["case_sha256"] = case_digest(cases[0])
    result = review_result(dataset, decisions)
    assert result["review_complete"]
    assert result["coverage_eligible"]
    assert result["cross_meeting_cases"] == 1


def test_reviewer_cannot_relabel_source_domain() -> None:
    case = {"id": "course", "domain": "course_research", "expected_meeting_ids": [1]}
    result = review_result({"cases": [case]}, [decision(case, domain="meeting")])
    assert not result["review_complete"]
    assert any("does not match source metadata" in error for error in result["errors"])


def test_missing_duplicate_or_extra_review_ids_are_rejected():
    case = {"id": "one"}
    for rows in [[], [decision(case), decision(case)], [decision(case), decision({"id": "extra"})]]:
        assert not review_result({"cases": [case]}, rows)["review_complete"]
