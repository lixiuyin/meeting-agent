"""Fact-bearing near duplicates must retain differences and provenance."""

from src.services.chain._retrieve_post import _merge_alternate_source, _near_duplicate


def test_numeric_difference_is_not_near_duplicate() -> None:
    common = "The approved launch budget for Project Atlas is {} after the finance review."
    assert not _near_duplicate(
        common.format("$120,000"), common.format("$150,000"), threshold=0.8, n=4
    )


def test_negation_difference_is_not_near_duplicate() -> None:
    positive = "The committee approved the deployment for Friday after final review."
    negative = "The committee did not approve the deployment for Friday after final review."
    assert not _near_duplicate(positive, negative, threshold=0.7, n=4)


def test_collapsed_duplicate_retains_both_source_identities() -> None:
    target = {"metadata": {"meeting_id": 1, "file_id": 10, "file_name": "a.txt"}}
    duplicate = {"metadata": {"meeting_id": 2, "file_id": 20, "file_name": "b.txt"}}

    _merge_alternate_source(target, duplicate)

    assert target["metadata"]["alternate_sources"] == [
        {"meeting_id": 1, "file_id": 10, "file_name": "a.txt"},
        {"meeting_id": 2, "file_id": 20, "file_name": "b.txt"},
    ]
