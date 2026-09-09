from dataclasses import dataclass

import pytest

from scripts._bench_multi_turn import execute_multi_turn_cases, validate_multi_turn_dataset


@dataclass
class _Result:
    answer: str
    sources: list[dict]
    session_id: str
    trace: dict


def _dataset() -> dict:
    return {
        "schema_version": 2,
        "cases": [
            {
                "id": "coreference",
                "fixture_files": ["sample.pdf"],
                "turns": [
                    {
                        "question": "Name the blocker.",
                        "answerability": "answerable",
                        "expected_answer": "Budget approval.",
                        "expected_evidence": [
                            {"file_name": "sample.pdf", "content_contains": ["Budget"]}
                        ],
                    },
                    {
                        "question": "Who owns it?",
                        "answerability": "answerable",
                        "expected_answer": "Bob.",
                        "expected_evidence": [
                            {"file_name": "sample.pdf", "content_contains": ["Budget"]}
                        ],
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_execute_multi_turn_cases_reuses_session_and_aggregates_scores():
    calls: list[dict] = []

    async def _ask(**kwargs):
        calls.append(kwargs)
        return _Result(
            answer="Grounded answer [1]",
            sources=[
                {
                    "meeting_id": 99,
                    "file_id": 88,
                    "file_name": "sample.pdf",
                    "chunk_index": 1,
                    "content": "Budget evidence",
                    "storage_path": "/private/runtime/path",
                }
            ],
            session_id=kwargs.get("session_id") or "stable-session",
            trace={"trace_id": "trace", "spans": []},
        )

    def _judge(**_kwargs):
        return {
            "metrics": {
                metric: {"score": 0.8, "justification": "ok"}
                for metric in (
                    "faithfulness",
                    "appropriateness",
                    "naturalness",
                    "completeness",
                )
            },
            "parse_retries": 0,
        }

    result = await execute_multi_turn_cases(
        _dataset(),
        ask_fn=_ask,
        judge_fn=_judge,
        fixture_info={"coreference": {"sample.pdf": (10, 20)}},
        judge_repeats=1,
    )

    assert calls[0]["session_id"] is None
    assert calls[1]["session_id"] == "stable-session"
    assert calls[0]["file_ids"] == [20]
    assert "meeting_ids" not in calls[0]
    assert result["stats"]["session_continuity"] == 1.0
    assert result["stats"]["evidence_recall"] == 1.0
    assert result["stats"]["corpus_isolation"] == 1.0
    assert result["valid"] is True
    assert result["stats"]["faithfulness"] == 0.8
    assert result["stats"]["parse_failures"] == 0
    assert all(row["case_session_continuity"] for row in result["rows"])
    source = result["rows"][0]["sources"][0]
    assert "meeting_id" not in source
    assert "file_id" not in source
    assert "storage_path" not in source
    assert len(source["content_sha256"]) == 64


@pytest.mark.asyncio
async def test_execute_multi_turn_cases_fails_validity_on_undeclared_source():
    async def _ask(**kwargs):
        return _Result(
            answer="Leaked answer",
            sources=[
                {
                    "file_name": "undeclared.pptx",
                    "chunk_index": 0,
                    "content": "must not be visible",
                }
            ],
            session_id=kwargs.get("session_id") or "stable-session",
            trace={"trace_id": "trace", "spans": []},
        )

    def _judge(**_kwargs):
        return {
            "metrics": {
                metric: {"score": 0.0, "justification": "leaked"}
                for metric in (
                    "faithfulness",
                    "appropriateness",
                    "naturalness",
                    "completeness",
                )
            },
            "parse_retries": 0,
        }

    result = await execute_multi_turn_cases(
        _dataset(),
        ask_fn=_ask,
        judge_fn=_judge,
        fixture_info={"coreference": {"sample.pdf": (10, 20)}},
        judge_repeats=1,
    )

    assert result["valid"] is False
    assert result["stats"]["corpus_isolation"] == 0.0
    assert "undeclared.pptx" in result["validity_errors"][0]


@pytest.mark.asyncio
async def test_execute_multi_turn_cases_rejects_zero_evidence_answerable_turn():
    async def _ask(**kwargs):
        return _Result(
            answer="I cannot find it.",
            sources=[],
            session_id=kwargs.get("session_id") or "stable-session",
            trace={"trace_id": "trace", "spans": []},
        )

    def _judge(**_kwargs):
        return {
            "metrics": {
                metric: {"score": 1.0, "justification": "safe abstention"}
                for metric in (
                    "faithfulness",
                    "appropriateness",
                    "naturalness",
                    "completeness",
                )
            },
            "parse_retries": 0,
        }

    result = await execute_multi_turn_cases(
        _dataset(),
        ask_fn=_ask,
        judge_fn=_judge,
        fixture_info={"coreference": {"sample.pdf": (10, 20)}},
        judge_repeats=1,
    )

    assert result["valid"] is False
    assert result["stats"]["evidence_recall"] == 0.0
    assert all("none of its expected evidence" in error for error in result["validity_errors"])


def test_validate_multi_turn_dataset_requires_gold_for_answerable_turn():
    dataset = _dataset()
    del dataset["cases"][0]["turns"][1]["expected_answer"]

    with pytest.raises(ValueError, match="expected_answer is required"):
        validate_multi_turn_dataset(dataset)
