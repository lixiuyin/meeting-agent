"""RAG retrieval quality metrics (pure functions, no LLM)."""

from __future__ import annotations

import math


def recall_at_k(retrieved_ids: list[str], expected: set[str], k: int) -> float:
    """Compute Recall@k: fraction of expected items found in top-k results."""
    if not expected:
        return 0.0
    top_k = set(retrieved_ids[:k])
    return len(top_k & expected) / len(expected)


def mrr(retrieved_ids: list[str], expected: set[str]) -> float:
    """Compute Mean Reciprocal Rank based on first relevant hit."""
    if not expected:
        return 0.0
    for i, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in expected:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], expected: set[str], k: int) -> float:
    """Compute nDCG@k with binary relevance.

    Relevance is 1 if the document ID is in expected, else 0.
    """
    if not expected:
        return 0.0

    def _dcg(relevances: list[float]) -> float:
        return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))

    relevances = [1.0 if doc_id in expected else 0.0 for doc_id in retrieved_ids]
    dcg = _dcg(relevances)

    ideal_len = min(len(expected), k)
    ideal_relevances = [1.0] * ideal_len + [0.0] * max(0, len(retrieved_ids) - ideal_len)
    idcg = _dcg(ideal_relevances)
    return dcg / idcg if idcg > 0 else 0.0


def file_precision_at_k(selected_files: list[int], expected: set[int], k: int) -> float:
    """Compute file-level Precision@k: fraction of top-k selected files that are expected."""
    if not expected or k <= 0:
        return 0.0
    top_k = set(selected_files[:k])
    hits = len(top_k & expected)
    return hits / len(top_k) if top_k else 0.0


def file_recall_at_k(selected_files: list[int], expected: set[int], k: int) -> float:
    """Compute file-level Recall@k: fraction of expected files found in top-k selections."""
    if not expected:
        return 0.0
    top_k = set(selected_files[:k])
    return len(top_k & expected) / len(expected)
