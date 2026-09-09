"""RAG retrieval quality metrics (pure functions, no LLM)."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

_CHUNK_SUFFIX_RE = re.compile(r"_(?:chunk|parent)_(\d+)$")
_CHILD_SUFFIX_RE = re.compile(r"_child_(\d+)_\d+$")


def canonical_chunk_key(
    result: Mapping[str, object],
    *,
    file_names_by_id: Mapping[object, str],
    fallback_rank: int,
) -> str:
    """Return a stable, file-qualified key for one retrieved chunk.

    Runtime chunk IDs include database-assigned meeting/file IDs, so comparing
    them directly with fixture labels such as ``chunk_1`` makes every benchmark
    run look irrelevant. File qualification is also required because all
    fixtures share one synthetic meeting and may each contain ``chunk_1``.
    Parent/child hits are credited to their parent chunk, matching the golden
    dataset's document-level annotation granularity.
    """
    raw_metadata = result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    file_id = metadata.get("file_id")
    file_name = file_names_by_id.get(file_id)
    if file_name is None and file_id is not None:
        file_name = file_names_by_id.get(str(file_id))
    if file_name is None:
        metadata_name = metadata.get("file_name")
        file_name = metadata_name if isinstance(metadata_name, str) else "unknown-file"

    chunk_id = metadata.get("chunk_id")
    chunk_id_text = chunk_id if isinstance(chunk_id, str) else ""
    child_match = _CHILD_SUFFIX_RE.search(chunk_id_text)
    regular_match = _CHUNK_SUFFIX_RE.search(chunk_id_text)
    if child_match:
        chunk_index = int(child_match.group(1))
    elif regular_match:
        chunk_index = int(regular_match.group(1))
    else:
        raw_index = metadata.get("chunk_index")
        chunk_index = raw_index if isinstance(raw_index, int) else fallback_rank

    return f"{file_name}:chunk_{chunk_index}"


def validate_retrieval_rows(rows: list[dict], *, expected_query_ids: list[str]) -> dict:
    """Fail closed when a retrieval report lacks cases or ranking evidence."""
    errors: list[str] = []
    observed_ids = [str(row.get("query_id", "")) for row in rows]
    expected_set = set(expected_query_ids)
    observed_set = set(observed_ids)
    missing = sorted(expected_set - observed_set)
    unexpected = sorted(observed_set - expected_set)
    if missing:
        errors.append("missing query ids: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected query ids: " + ", ".join(unexpected))
    if len(observed_ids) != len(observed_set):
        errors.append("duplicate query ids")

    evidence_fields = (
        "expected_chunk_keys",
        "semantic_chunk_keys",
        "semantic_physical_ids",
        "hybrid_chunk_keys",
        "hybrid_physical_ids",
        "rerank_chunk_keys",
        "rerank_physical_ids",
    )
    metric_fields = (
        "semantic_recall_5",
        "semantic_recall_10",
        "semantic_mrr",
        "semantic_ndcg_10",
        "hybrid_recall_10",
        "hybrid_mrr",
        "hybrid_ndcg_10",
        "hybrid_rerank_recall_10",
        "hybrid_rerank_mrr",
        "hybrid_rerank_ndcg_10",
    )
    for row in rows:
        query_id = str(row.get("query_id", "<missing>"))
        for field in evidence_fields:
            value = row.get(field)
            if not isinstance(value, list) or (field == "expected_chunk_keys" and not value):
                errors.append(f"{query_id}: invalid {field}")
        for logical_field, physical_field in (
            ("semantic_chunk_keys", "semantic_physical_ids"),
            ("hybrid_chunk_keys", "hybrid_physical_ids"),
            ("rerank_chunk_keys", "rerank_physical_ids"),
        ):
            logical = row.get(logical_field)
            physical = row.get(physical_field)
            if (
                isinstance(logical, list)
                and isinstance(physical, list)
                and len(logical) != len(physical)
            ):
                errors.append(f"{query_id}: {logical_field} rank evidence is incomplete")
        for field in metric_fields:
            value = row.get(field)
            if field.startswith("hybrid_rerank_") and row.get("reranker_executed") is False:
                if value is not None:
                    errors.append(f"{query_id}: skipped reranker has a synthetic {field}")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                errors.append(f"{query_id}: invalid {field}")

    complete = not missing and len(rows) == len(expected_query_ids)
    return {
        "valid": complete and not errors,
        "complete": complete,
        "validity_errors": errors,
        "counts": {
            "expected_cases": len(expected_query_ids),
            "observed_cases": len(rows),
        },
    }


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
