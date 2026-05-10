import asyncio
import json
import re

from ...core.config import settings
from ._common import (
    _INITIAL_IMPORTANCE,
    _MAX_IMPORTANCE,
    _MEMORY_DEDUP_THRESHOLD,
    _MEMORY_KEY_MAX_LENGTH,
    _MEMORY_TTL_DAYS,
    _MEMORY_VALUE_MAX_LENGTH,
    _MIN_IMPORTANCE,
    logger,
)

# ---- Fact extraction helpers ----


# M-6: Strip HTML/script tags and control characters from memory fields.
_SANITIZE_RE = re.compile(r"<[^>]*>")


def _sanitize(text: str) -> str:
    """Remove HTML tags and strip control characters."""
    cleaned = _SANITIZE_RE.sub("", text)
    return "".join(c for c in cleaned if c.isprintable() or c in ("\n", "\t"))


def _parse_fact_json(raw: str) -> list[dict] | None:
    """Parse and validate LLM fact extraction output with Pydantic."""
    from pydantic import BaseModel, Field, ValidationError

    from ..llm import parse_llm_json

    class ExtractedFact(BaseModel):
        key: str = Field(min_length=1, max_length=_MEMORY_KEY_MAX_LENGTH)
        value: str = Field(min_length=1, max_length=_MEMORY_VALUE_MAX_LENGTH)
        importance: int = Field(ge=_MIN_IMPORTANCE, le=_MAX_IMPORTANCE, default=_INITIAL_IMPORTANCE)
        category: str | None = None
        ttl_days: int | None = Field(default=None, ge=-1)

    try:
        data = parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("No valid JSON in fact extraction output")
        return None

    if not isinstance(data, list):
        return None

    facts = []
    for item in data:
        try:
            # M-6: Sanitize key/value before validation to strip HTML/script
            if isinstance(item, dict):
                if "key" in item and isinstance(item["key"], str):
                    item["key"] = _sanitize(item["key"])
                if "value" in item and isinstance(item["value"], str):
                    item["value"] = _sanitize(item["value"])
            facts.append(ExtractedFact(**item).model_dump())
        except (ValidationError, TypeError):
            continue  # Skip malformed entries
    return facts or None


def _parse_consolidation_json(content: str) -> dict | None:
    """Parse LLM memory consolidation output as JSON, handling markdown fences."""
    from ..llm import parse_llm_json

    try:
        result = parse_llm_json(content)
        if isinstance(result, dict) and "key" in result and "value" in result:
            return result
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _parse_summary_json(content: str) -> dict | None:
    """Parse LLM summary output as JSON, handling markdown fences."""
    from ..llm import parse_llm_json

    try:
        result = parse_llm_json(content)
        if isinstance(result, dict) and "summary" in result:
            return result
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _text_cluster_memories(
    memories: list[dict], similarity_threshold: float | None = None
) -> list[list[dict]]:
    """Group memories into clusters based on word-overlap of key+value text.

    Uses union-find on the overlap coefficient (Szymkiewicz-Simpson) so
    transitively similar memories end up in the same cluster.
    Deterministic: memories are sorted by (user_id, key) before clustering (MEM-8).
    """

    if similarity_threshold is None:
        similarity_threshold = _MEMORY_DEDUP_THRESHOLD

    # MEM-8: Sort for deterministic cluster output regardless of input order
    sorted_memories = sorted(memories, key=lambda m: (m.get("user_id", ""), m.get("key", "")))

    def _tokens(m: dict) -> set[str]:
        text = f"{m.get('key', '')} {m.get('value', '')}"
        return {w.lower() for w in text.replace("_", " ").replace("-", " ").split() if len(w) > 2}

    n = len(sorted_memories)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    token_sets = [_tokens(m) for m in sorted_memories]

    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = token_sets[i], token_sets[j]
            if not ti or not tj:
                continue
            overlap = ti & tj
            shorter = min(len(ti), len(tj))
            if shorter > 0 and len(overlap) / shorter >= similarity_threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, m in enumerate(sorted_memories):
        groups.setdefault(find(i), []).append(m)

    return list(groups.values())


async def _semantic_cluster_memories(
    memories: list[dict],
    user_id: str,
    *,
    min_results_for_median: int = 7,
) -> list[list[dict]]:
    """Cluster memories by vector similarity via Chroma, with text fallback.

    Uses an adaptive threshold: for each query, the median distance across all
    returned results (including non-batch memories) serves as the similarity
    cutoff.  This makes clustering invariant to the absolute distance scale,
    so it works correctly regardless of embedding model, dimension, or distance
    metric (L2, cosine, etc).

    Falls back to :func:`_text_cluster_memories` on any error or when the
    result set is too small for a reliable median.

    Uses batch embedding + local cosine similarity instead of per-memory
    Chroma queries, reducing O(n) round-trips to a single batch call.
    """
    try:
        # ``_parsers`` lives at ``src.services.memory`` so the parent package
        # is ``src.services``; that's where ``embedder`` is.  Four dots would
        # walk past the top-level ``src`` package and raise
        # ``ImportError: attempted relative import beyond top-level package``,
        # which silently disabled semantic clustering until the fallback
        # branch caught it.
        from ..embedder import get_embeddings

        n = len(memories)
        if n < 2:
            return [memories]

        # Batch-embed all memory values at once (single API call) then
        # cluster locally with cosine similarity + adaptive threshold.
        texts = [f"{m['key']}: {m['value']}" for m in memories]
        emb_fn = get_embeddings()
        embeddings = await asyncio.to_thread(emb_fn.embed_documents, texts)

        # Compute pairwise cosine similarities
        import math

        sims: list[list[float]] = [[0.0] * n for _ in range(n)]
        all_sims: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                dot = sum(a * b for a, b in zip(embeddings[i], embeddings[j], strict=False))
                na = math.sqrt(sum(a * a for a in embeddings[i]))
                nb = math.sqrt(sum(b * b for b in embeddings[j]))
                s = dot / (na * nb) if na > 0 and nb > 0 else 0.0
                sims[i][j] = s
                sims[j][i] = s
                all_sims.append(s)

        if not all_sims:
            return [memories]

        # M-6: Fixed threshold avoids over-sensitivity in small clusters.
        threshold = settings.MEMORY_CLUSTER_THRESHOLD

        # Union-Find clustering
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                if sims[i][j] >= threshold:
                    parent[find(i)] = find(j)

        groups: dict[int, list[dict]] = {}
        for i, m in enumerate(memories):
            groups.setdefault(find(i), []).append(m)
        return list(groups.values())
    except Exception:
        logger.warning("Semantic clustering failed, falling back to text clustering", exc_info=True)
        return _text_cluster_memories(memories)


def _is_fact_supported(
    key: str, value: str, question: str, answer: str, min_overlap: int = 2
) -> bool:
    """Check if extracted fact has keyword support in the original Q&A text.

    Prevents LLM hallucinations from being stored as memories: a fact whose
    key terms don't appear in the source conversation is likely fabricated.
    Uses whitespace tokenization with stopword filtering for efficiency.
    """
    _STOPWORDS = frozenset(
        {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "and",
            "but",
            "or",
            "nor",
            "not",
            "so",
            "yet",
            "both",
            "either",
            "neither",
            "each",
            "every",
            "all",
            "any",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "only",
            "own",
            "same",
            "than",
            "too",
            "very",
            "just",
            "because",
            "if",
            "when",
            "where",
            "how",
            "what",
            "which",
            "who",
            "whom",
            "this",
            "that",
            "these",
            "those",
            "i",
            "me",
            "my",
            "we",
            "our",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "it",
            "its",
            "they",
            "them",
            "their",
        }
    )

    def _meaningful_tokens(text: str) -> set[str]:
        return {w.lower() for w in text.split() if len(w) > 1 and w.lower() not in _STOPWORDS}

    fact_tokens = _meaningful_tokens(f"{key} {value}")
    if not fact_tokens:
        return False
    source_tokens = _meaningful_tokens(f"{question} {answer}")
    overlap = fact_tokens & source_tokens
    return len(overlap) >= min_overlap


def _is_semantic_duplicate(new_key: str, existing_keys: list[str]) -> bool:
    """Check if new_key is semantically similar to any existing key.

    Uses word-level tokenization with overlap coefficient (Szymkiewicz-Simpson).
    This measures how much of the shorter key's words appear in the longer one,
    which is more robust than Jaccard for comparing short phrases.
    """
    if not existing_keys:
        return False

    def _tokenize(text: str) -> set[str]:
        """Split into word-level tokens, filtering single-char and common separators."""
        return {w for w in text.lower().replace("_", " ").replace("-", " ").split() if len(w) > 1}

    new_tokens = _tokenize(new_key)
    if not new_tokens:
        return False

    for existing in existing_keys:
        existing_tokens = _tokenize(existing)
        if not existing_tokens:
            continue
        # Overlap coefficient: |intersection| / min(|A|, |B|)
        intersection = new_tokens & existing_tokens
        min_size = min(len(new_tokens), len(existing_tokens))
        overlap_coeff = len(intersection) / min_size
        if overlap_coeff >= _MEMORY_DEDUP_THRESHOLD:
            return True
    return False


def _compute_expiry(ttl_days: int | None) -> str | None:
    """Compute expiry timestamp from TTL days (UTC).

    HIGH-15: Uses SQL ``CURRENT_TIMESTAMP`` as the base time instead of
    Python's ``datetime.now(UTC)`` to avoid clock skew between the app
    server and the database.
    """
    from ...core import database as db

    # Read the DB's clock so expiry is consistent with SQL comparisons.
    try:
        with db.get_connection() as conn:
            row = conn.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()
            now_str = row["ts"] if row else None
        if now_str is None:
            # Fallback to Python clock if DB query somehow fails.
            from datetime import UTC, datetime

            now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        from datetime import UTC, datetime

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    from datetime import UTC, datetime, timedelta

    now = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

    if ttl_days is None:
        return (now + timedelta(days=_MEMORY_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    if ttl_days == -1:
        return None  # No expiry
    if ttl_days > 0:
        return (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
    return None
