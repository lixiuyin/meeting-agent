"""Dynamic ground-truth mapping: compute expected_chunks per query for a given chunk strategy."""

from __future__ import annotations

import math
import re
from typing import Any


def _extract_keywords(text: str, min_len: int = 2) -> list[str]:
    """Extract likely content-bearing keywords from a string."""
    # Keep alphanumeric tokens longer than min_len, excluding common stopwords
    stopwords = {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "day",
        "get",
        "has",
        "him",
        "his",
        "how",
        "man",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "boy",
        "did",
        "its",
        "let",
        "put",
        "say",
        "she",
        "too",
        "use",
        "that",
        "this",
        "with",
        "from",
        "they",
        "know",
        "want",
        "been",
        "good",
        "much",
        "some",
        "time",
        "very",
        "when",
        "come",
        "here",
        "just",
        "like",
        "long",
        "make",
        "over",
        "such",
        "take",
        "than",
        "them",
        "well",
        "were",
        "what",
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "那",
        "这些",
        "那些",
        "这个",
        "那个",
    }
    # Preserve currency symbols, numbers, and CJK characters
    tokens = re.findall(r"[€$£¥]?\d+(?:\.\d+)?|[\w一-鿿]+", text.lower())
    return [t for t in tokens if len(t) >= min_len and t not in stopwords]


def _keyword_coverage(answer: str, chunk_text: str, keywords: list[str] | None = None) -> float:
    """Return fraction of answer keywords found in chunk_text."""
    if not answer or not chunk_text:
        return 0.0
    if keywords is None:
        keywords = _extract_keywords(answer)
    if not keywords:
        return 0.0
    chunk_lower = chunk_text.lower()
    hits = sum(1 for kw in keywords if kw in chunk_lower)
    return hits / len(keywords)


def _embedding_similarities(answer: str, chunk_texts: list[str]) -> list[float]:
    """Compute cosine similarities between answer and each chunk text."""
    try:
        from src.services.embedder import get_embeddings

        texts = [answer, *chunk_texts]
        embs = get_embeddings().embed_documents(texts)
        answer_emb = embs[0]
        chunk_embs = embs[1:]

        def _norm(v: list[float]) -> float:
            return math.sqrt(sum(x * x for x in v))

        norm_a = _norm(answer_emb)
        if norm_a == 0:
            return [0.0] * len(chunk_texts)

        sims = []
        for ce in chunk_embs:
            norm_c = _norm(ce)
            if norm_c == 0:
                sims.append(0.0)
                continue
            dot = sum(x * y for x, y in zip(answer_emb, ce, strict=False))
            sims.append(dot / (norm_c * norm_a))
        return sims
    except Exception:
        return [0.0] * len(chunk_texts)


def compute_expected_chunks(
    query_item: dict[str, Any],
    chunks: list[dict[str, Any]],
    method: str = "hybrid",
    keyword_threshold: float = 0.25,
    top_k_embedding: int = 5,
    min_keyword_matches: int | None = None,
    use_llm: bool = True,
    llm_candidate_top_n: int = 20,
) -> list[str]:
    """Compute which chunk IDs contain the information needed to answer the query.

    Args:
        query_item: dict with "expected_answer" and "query".
        chunks: list of chunk dicts, each with "chunk_id" and "text" keys.
        method: "keyword", "embedding", or "hybrid" (default).
        keyword_threshold: minimum fraction of keywords that must be present.
        top_k_embedding: number of top embedding-similar chunks to include.
        min_keyword_matches: minimum number of distinct keywords to match.
            If None, adapts automatically based on keyword count.
        use_llm: whether to use LLM-as-judge to refine the candidate set.
        llm_candidate_top_n: max number of heuristic candidates to send to LLM.

    Returns:
        List of chunk_id strings.
    """
    query = query_item.get("query", "")
    answer = query_item.get("expected_answer", "")
    combined_text = f"{query} {answer}".strip()
    if not combined_text or not chunks:
        return []

    keywords = _extract_keywords(combined_text)
    # Adaptive floor: at least 1, at most 3, never more than total keywords
    adaptive_min = (
        min_keyword_matches if min_keyword_matches is not None else max(1, min(3, len(keywords)))
    )
    selected: set[str] = set()

    if method in ("keyword", "hybrid"):
        for c in chunks:
            chunk_text = c.get("text", "")
            coverage = _keyword_coverage(combined_text, chunk_text, keywords)
            matched = sum(1 for kw in keywords if kw in chunk_text.lower())
            if coverage >= keyword_threshold and matched >= adaptive_min:
                selected.add(c["chunk_id"])

    if method in ("embedding", "hybrid"):
        chunk_texts = [c.get("text", "") for c in chunks]
        similarities = _embedding_similarities(combined_text, chunk_texts)
        # Get top-k indices by similarity
        indexed = list(enumerate(similarities))
        indexed.sort(key=lambda x: x[1])
        top_indices = [idx for idx, _ in indexed[-top_k_embedding:]]
        for idx in top_indices:
            if similarities[idx] > 0.0:
                selected.add(chunks[idx]["chunk_id"])

    heuristic_ids = list(selected)

    # --- LLM refinement stage ---
    if use_llm and heuristic_ids:
        # Build candidate pool from heuristic results (capped)
        candidate_pool = [c for c in chunks if c["chunk_id"] in heuristic_ids]
        # If heuristic produced too many, keep embedding-top ones first
        if len(candidate_pool) > llm_candidate_top_n:
            # Re-score by similarity within heuristic set and truncate
            pool_texts = [c.get("text", "") for c in candidate_pool]
            pool_sims = _embedding_similarities(combined_text, pool_texts)
            scored = sorted(
                zip(candidate_pool, pool_sims, strict=True),
                key=lambda x: x[1],
                reverse=True,
            )
            candidate_pool = [c for c, _ in scored[:llm_candidate_top_n]]

        try:
            from ._bench_llm_ground_truth import llm_filter_relevant_chunks

            llm_ids = llm_filter_relevant_chunks(query, answer, candidate_pool)
            if llm_ids:
                return llm_ids
        except Exception:
            # Fallback to heuristic on any failure
            pass

    return heuristic_ids


def load_chunks_from_vectorstore(meeting_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Fetch all chunks from the vectorstore and return lightweight dicts."""
    from src.services.rag._vectorstore import get_vectorstore

    vectorstore = get_vectorstore()
    where = {"meeting_id": {"$in": meeting_ids}} if meeting_ids else None
    results = vectorstore.get(where=where, include=["documents", "metadatas"])

    chunks = []
    for idx, doc_id in enumerate(results["ids"]):
        meta = results["metadatas"][idx]
        chunks.append(
            {
                "chunk_id": doc_id,
                "text": results["documents"][idx],
                "meeting_id": meta.get("meeting_id"),
                "file_id": meta.get("file_id"),
                "chunk_index": meta.get("chunk_index"),
                "chunk_type": meta.get("chunk_type"),
                "parent_id": meta.get("parent_id"),
            }
        )
    return chunks
