from scripts._bench_rag_answer import validate_rag_answer_rows


def _row(query_id: str = "q1") -> dict:
    diagnostics = {
        metric: [{"score": 1.0, "justification": "ok"}]
        for metric in (
            "faithfulness",
            "answer_relevance",
            "context_precision",
            "context_recall",
            "correctness",
            "citation_quality",
        )
    }
    return {
        "query_id": query_id,
        "answer": "Grounded answer [1]",
        "sources": [{"rank": 1, "file_name": "sample.pdf", "content_sha256": "hash"}],
        "expected_files": ["sample.pdf"],
        "corpus_files": ["sample.pdf"],
        "observed_files": ["sample.pdf"],
        "unexpected_source_files": [],
        "source_identity_recall": 1.0,
        "corpus_isolation": 1.0,
        "trace": {"spans": []},
        "judge_diagnostics": diagnostics,
        **dict.fromkeys(diagnostics, 1.0),
    }


def test_rag_answer_rows_are_valid_only_when_complete() -> None:
    result = validate_rag_answer_rows(
        [_row()],
        expected_query_ids=["q1"],
        expected_files_by_query={"q1": ["sample.pdf"]},
        corpus_files_by_query={"q1": ["sample.pdf"]},
        judge_repeats=1,
    )

    assert result["valid"] is True
    assert result["complete"] is True
    assert result["validity_errors"] == []
    assert result["counts"]["observed_cases"] == 1


def test_rag_answer_rows_fail_closed_on_missing_case_metric_and_trace() -> None:
    row = _row()
    row["faithfulness"] = None
    row["judge_diagnostics"]["faithfulness"] = [None]
    row["trace"] = None

    result = validate_rag_answer_rows(
        [row],
        expected_query_ids=["q1", "q2"],
        expected_files_by_query={"q1": ["sample.pdf"], "q2": ["sample.pptx"]},
        corpus_files_by_query={"q1": ["sample.pdf"], "q2": ["sample.pptx"]},
        judge_repeats=1,
    )

    assert result["valid"] is False
    assert result["complete"] is False
    assert "missing query ids: q2" in result["validity_errors"]
    assert "q1: invalid faithfulness score" in result["validity_errors"]
    assert "q1: faithfulness judge parse failure" in result["validity_errors"]
    assert "q1: missing process trace" in result["validity_errors"]


def test_rag_answer_rows_fail_closed_on_source_identity_tampering() -> None:
    row = _row()
    row["sources"].append({"rank": 2, "file_name": "sample.pptx", "content_sha256": "other-hash"})

    result = validate_rag_answer_rows(
        [row],
        expected_query_ids=["q1"],
        expected_files_by_query={"q1": ["sample.pdf"]},
        corpus_files_by_query={"q1": ["sample.pdf"]},
        judge_repeats=1,
    )

    assert result["valid"] is False
    assert "q1: observed source files do not match artifacts" in result["validity_errors"]
    assert "q1: unexpected source files do not match artifacts" in result["validity_errors"]
    assert "q1: corpus isolation does not match artifacts" in result["validity_errors"]


def test_rag_answer_rows_allow_declared_distractor_sources() -> None:
    row = _row()
    row["corpus_files"] = ["sample.pdf", "distractor.pdf"]
    row["sources"].append(
        {"rank": 2, "file_name": "distractor.pdf", "content_sha256": "distractor-hash"}
    )
    row["observed_files"] = ["distractor.pdf", "sample.pdf"]

    result = validate_rag_answer_rows(
        [row],
        expected_query_ids=["q1"],
        expected_files_by_query={"q1": ["sample.pdf"]},
        corpus_files_by_query={"q1": ["sample.pdf", "distractor.pdf"]},
        judge_repeats=1,
    )

    assert result["valid"] is True
    assert result["validity_errors"] == []
