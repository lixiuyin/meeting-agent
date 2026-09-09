"""Unit tests for RAG retrieval quality metrics."""

import pytest

from scripts._bench_rag_quality import (
    canonical_chunk_key,
    mrr,
    ndcg_at_k,
    recall_at_k,
    validate_retrieval_rows,
)


def test_recall_at_k_identity():
    retrieved = ["a", "b", "c", "d"]
    expected = {"a", "b"}
    assert recall_at_k(retrieved, expected, 2) == 1.0
    assert recall_at_k(retrieved, expected, 1) == 0.5


def test_recall_at_k_partial():
    retrieved = ["a", "b", "c"]
    expected = {"a", "d"}
    assert recall_at_k(retrieved, expected, 3) == 0.5


def test_recall_at_k_empty_expected():
    assert recall_at_k(["a"], set(), 5) == 0.0


def test_canonical_chunk_key_removes_runtime_ids_and_qualifies_file() -> None:
    file_names = {41: "sample.pdf", 42: "sample.pptx"}

    flat = canonical_chunk_key(
        {
            "metadata": {
                "file_id": 41,
                "chunk_id": "meeting_9_file_41_chunk_1",
                "chunk_index": 1,
            }
        },
        file_names_by_id=file_names,
        fallback_rank=0,
    )
    child = canonical_chunk_key(
        {
            "metadata": {
                "file_id": 42,
                "chunk_id": "meeting_9_file_42_child_2_3",
                "chunk_index": 2003,
            }
        },
        file_names_by_id=file_names,
        fallback_rank=0,
    )

    assert flat == "sample.pdf:chunk_1"
    assert child == "sample.pptx:chunk_2"
    assert flat != "sample.pptx:chunk_1"


def test_validate_retrieval_rows_requires_rank_evidence_without_score_threshold() -> None:
    row = {
        "query_id": "q1",
        "expected_chunk_keys": ["sample.pdf:chunk_1"],
        "semantic_chunk_keys": ["sample.pdf:chunk_0"],
        "semantic_physical_ids": ["meeting_1_file_2_chunk_0"],
        "hybrid_chunk_keys": ["sample.pdf:chunk_0"],
        "hybrid_physical_ids": ["meeting_1_file_2_chunk_0"],
        "rerank_chunk_keys": ["sample.pdf:chunk_0"],
        "rerank_physical_ids": ["meeting_1_file_2_chunk_0"],
        "reranker_executed": False,
        "semantic_recall_5": 0.0,
        "semantic_recall_10": 0.0,
        "semantic_mrr": 0.0,
        "semantic_ndcg_10": 0.0,
        "hybrid_recall_10": 0.0,
        "hybrid_mrr": 0.0,
        "hybrid_ndcg_10": 0.0,
        "hybrid_rerank_recall_10": None,
        "hybrid_rerank_mrr": None,
        "hybrid_rerank_ndcg_10": None,
    }

    valid = validate_retrieval_rows([row], expected_query_ids=["q1"])
    row["hybrid_physical_ids"] = []
    incomplete = validate_retrieval_rows([row], expected_query_ids=["q1"])

    assert valid["valid"] is True
    assert incomplete["valid"] is False
    assert "q1: hybrid_chunk_keys rank evidence is incomplete" in incomplete["validity_errors"]


def test_mrr_first_position():
    assert mrr(["a", "b"], {"a"}) == 1.0


def test_mrr_second_position():
    assert mrr(["b", "a"], {"a"}) == 0.5


def test_mrr_no_match():
    assert mrr(["b", "c"], {"a"}) == 0.0


def test_ndcg_at_k_perfect():
    retrieved = ["a", "b", "c"]
    expected = {"a", "b", "c"}
    assert ndcg_at_k(retrieved, expected, 3) == pytest.approx(1.0)


def test_ndcg_at_k_partial():
    retrieved = ["a", "x", "b"]
    expected = {"a", "b"}
    # DCG = 1/log2(2) + 1/log2(4) = 1 + 0.5 = 1.5
    # IDCG = 1/log2(2) + 1/log2(3) = 1 + 0.6309...
    idcg = 1.0 + 1.0 / __import__("math").log2(3)
    assert ndcg_at_k(retrieved, expected, 3) == pytest.approx(1.5 / idcg)


def test_ndcg_at_k_empty_expected():
    assert ndcg_at_k(["a"], set(), 5) == 0.0
