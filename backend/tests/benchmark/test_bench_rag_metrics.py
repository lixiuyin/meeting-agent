"""Unit tests for RAG retrieval quality metrics."""

import pytest

from scripts._bench_rag_quality import mrr, ndcg_at_k, recall_at_k


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
