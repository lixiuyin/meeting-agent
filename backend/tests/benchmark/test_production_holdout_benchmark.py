"""Tests for private production holdout validation helpers."""

import sqlite3

import pytest

from scripts.production_holdout_benchmark import (
    _load_corpus,
    _normalized,
    _resolve_principal,
    _select_balanced_cases,
    _validate_candidate,
)


def test_ablation_arguments_and_unexecuted_latencies():
    from scripts.production_holdout_benchmark import _latency_summary, _parser

    args = _parser().parse_args(
        [
            "run",
            "--holdout",
            "cases.json",
            "--output",
            "report.json",
            "--memory-mode",
            "balanced",
            "--domain",
            "meeting",
        ]
    )
    assert args.memory_mode == "balanced" and args.domain == "meeting"
    assert _latency_summary([{}]) == {
        "p50": None,
        "p95": None,
        "p99": None,
        "evaluated": 0,
        "skipped": 1,
    }
    result = _latency_summary([{"latency_seconds": number} for number in range(1, 21)] + [{}])
    assert result["p95"] == 19 and result["p99"] == 20
    assert result["evaluated"] == 20 and result["skipped"] == 1


def test_validate_candidate_requires_verbatim_evidence() -> None:
    chunks = {
        "chunk-1": {
            "chunk_id": "chunk-1",
            "file_id": 7,
            "meeting_id": 11,
            "content": "The launch date is 14 May after the security review.",
        }
    }
    valid = _validate_candidate(
        {
            "question": "When is the launch?",
            "reference_answer": "14 May, after the security review.",
            "supporting_chunk_ids": ["chunk-1"],
            "evidence_quotes": ["launch date is 14 May"],
            "difficulty": "easy",
        },
        file_id=7,
        chunks_by_id=chunks,
        meeting_id=11,
        domain="meeting",
    )
    invalid = _validate_candidate(
        {
            "question": "When is the launch?",
            "reference_answer": "15 May.",
            "supporting_chunk_ids": ["chunk-1"],
            "evidence_quotes": ["launch date is 15 May"],
        },
        file_id=7,
        chunks_by_id=chunks,
    )

    assert valid is not None
    assert valid["expected_meeting_ids"] == [11]
    assert valid["domain"] == "meeting"
    assert invalid is None


def test_normalized_evidence_ignores_whitespace_and_case() -> None:
    assert _normalized("  Launch\nDATE ") == "launch date"


def test_cross_meeting_candidate_requires_evidence_from_both_sources() -> None:
    chunks = {
        "old": {"file_id": 1, "meeting_id": 10, "content": "Alice owns release"},
        "new": {"file_id": 2, "meeting_id": 11, "content": "Bob now owns release"},
    }
    candidate = {
        "question": "How did ownership change?",
        "reference_answer": "It changed from Alice to Bob.",
        "supporting_chunk_ids": ["old", "new"],
        "evidence_quotes": ["Alice owns release", "Bob now owns release"],
    }
    result = _validate_candidate(
        candidate,
        file_ids=[1, 2],
        meeting_ids=[10, 11],
        chunks_by_id=chunks,
        domain="meeting",
    )
    assert result is not None
    assert result["case_type"] == "cross_meeting"
    assert result["expected_file_ids"] == [1, 2]
    assert (
        _validate_candidate(
            {**candidate, "supporting_chunk_ids": ["old"]},
            file_ids=[1, 2],
            meeting_ids=[10, 11],
            chunks_by_id=chunks,
            domain="meeting",
        )
        is None
    )


def test_balanced_selection_enforces_domain_meeting_and_cross_case_mix() -> None:
    candidates = [
        {
            "question": "Cross?",
            "domain": "meeting",
            "case_type": "cross_meeting",
            "expected_meeting_ids": [1, 2],
        },
        *[
            {
                "question": f"Meeting {index}?",
                "domain": "meeting",
                "case_type": "single_source",
                "expected_meeting_ids": [index],
            }
            for index in range(3, 6)
        ],
        *[
            {
                "question": f"Course {index}?",
                "domain": "course_research",
                "case_type": "single_source",
                "expected_meeting_ids": [index],
            }
            for index in range(6, 10)
        ],
    ]
    selected = _select_balanced_cases(
        candidates,
        total=8,
        required_domains=["meeting", "course_research"],
        minimum_domain_cases=4,
        minimum_meetings=8,
        minimum_cross_meeting_cases=1,
    )
    assert len(selected) == 8
    assert sum(case["domain"] == "meeting" for case in selected) == 4
    assert len({meeting for case in selected for meeting in case["expected_meeting_ids"]}) == 9


def test_cross_meeting_holdout_requires_evidence_for_every_expected_file():
    from scripts.production_holdout_benchmark import _validate_holdout_evidence

    chunks = {
        "old": {"file_id": 1, "meeting_id": 10, "content": "Alice owns release"},
        "new": {"file_id": 2, "meeting_id": 11, "content": "Bob now owns release"},
    }
    case = {
        "question": "How did the owner change?",
        "reference_answer": "Alice to Bob",
        "expected_file_ids": [1, 2],
        "expected_meeting_ids": [10, 11],
        "supporting_chunk_ids": ["old", "new"],
        "evidence_quotes": ["Alice owns release", "Bob now owns release"],
    }
    assert _validate_holdout_evidence(case, chunks)
    assert not _validate_holdout_evidence({**case, "expected_file_ids": [1, 2, 3]}, chunks)
    assert not _validate_holdout_evidence(
        {**case, "evidence_quotes": ["Carol owns release"]}, chunks
    )
    assert not _validate_holdout_evidence({**case, "expected_meeting_ids": [10]}, chunks)


def test_multi_user_corpus_requires_explicit_principal(tmp_path):
    database = tmp_path / "meetings.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE meeting_files (
                id INTEGER PRIMARY KEY,
                meeting_id INTEGER,
                file_name TEXT,
                file_type TEXT,
                user_id TEXT,
                business_domain TEXT,
                status TEXT
            );
            CREATE TABLE bm25_index (
                id INTEGER PRIMARY KEY,
                chunk_id TEXT,
                meeting_id INTEGER,
                content TEXT,
                metadata TEXT
            );
            INSERT INTO meeting_files VALUES
              (1, 10, 'a.pdf', 'pdf', 'alice', 'meeting', 'ready'),
              (2, 20, 'b.pdf', 'pdf', 'bob', 'meeting', 'ready');
            INSERT INTO bm25_index VALUES
              (1, 'a', 10, 'Alice evidence', '{"file_id": 1}'),
              (2, 'b', 20, 'Bob evidence', '{"file_id": 2}');
            """
        )

    with pytest.raises(ValueError, match="--user-id"):
        _resolve_principal(database, None)
    assert _resolve_principal(database, "alice") == "alice"
    chunks, files = _load_corpus(database, user_id="alice")
    assert [chunk["chunk_id"] for chunk in chunks] == ["a"]
    assert list(files) == ["1"]
