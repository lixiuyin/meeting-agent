import asyncio
import json
import re
import unicodedata

from ...core.config import settings
from ._common import _MEMORY_KEY_MAX_LENGTH, _MEMORY_VALUE_MAX_LENGTH, logger

# ---- Fact extraction helpers ----


# M-6: Strip HTML/script tags and control characters from memory fields.
_SANITIZE_RE = re.compile(r"<[^>]*>")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _multilingual_tokens(text: str, *, min_latin_len: int = 2) -> set[str]:
    """Tokenize Latin and no-whitespace CJK text for overlap checks."""
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("_", " ")
    tokens = {
        token
        for token in _WORD_RE.findall(_CJK_RUN_RE.sub(" ", normalized))
        if len(token) >= min_latin_len
    }
    for run in _CJK_RUN_RE.findall(normalized):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _sanitize(text: str) -> str:
    """Remove HTML tags and strip control characters."""
    cleaned = _SANITIZE_RE.sub("", text)
    return "".join(c for c in cleaned if c.isprintable() or c in ("\n", "\t"))


def _parse_fact_json(raw: str) -> list[dict] | None:
    """Parse and validate LLM fact extraction output with Pydantic."""
    from pydantic import (
        AwareDatetime,
        BaseModel,
        Field,
        ValidationError,
        field_validator,
        model_validator,
    )

    from ..llm import parse_llm_json

    class ExtractedFact(BaseModel):
        key: str = Field(min_length=1, max_length=_MEMORY_KEY_MAX_LENGTH)
        value: str = Field(min_length=1, max_length=_MEMORY_VALUE_MAX_LENGTH)
        importance: int = Field(
            ge=settings.MEMORY_MIN_IMPORTANCE,
            le=settings.MEMORY_MAX_IMPORTANCE,
            default=settings.MEMORY_INITIAL_IMPORTANCE,
        )
        category: str | None = None
        ttl_days: int | None = Field(default=None, ge=-1)
        confidence: float = Field(default=0.75, ge=0.0, le=1.0)
        fact_type: str = Field(
            default="fact",
            pattern=r"^(fact|preference|project_fact|decision|action_item)$",
        )
        project_id: str | None = Field(default=None, max_length=200)
        subject: str | None = Field(default=None, max_length=500)
        predicate: str | None = Field(default=None, max_length=500)
        object_value: str | None = Field(default=None, max_length=_MEMORY_VALUE_MAX_LENGTH)
        valid_from: AwareDatetime | None = None
        valid_to: AwareDatetime | None = None
        evidence_quote: str | None = Field(default=None, max_length=2000)
        action_status: str | None = Field(
            default=None,
            pattern=r"^(open|in_progress|blocked|done|cancelled)$",
        )
        assignee: str | None = Field(default=None, max_length=500)
        due_at: AwareDatetime | None = None

        @field_validator("project_id", mode="before")
        @classmethod
        def _canonical_project_id(cls, value):
            if value is None:
                return None
            normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
            normalized = re.sub(r"[^\w\u3400-\u9fff]+", "_", normalized, flags=re.UNICODE)
            normalized = normalized.strip("_")
            if normalized.startswith("project_"):
                normalized = normalized[len("project_") :]
            elif normalized.startswith("项目"):
                normalized = normalized[len("项目") :].lstrip("_")
            return normalized or None

        @model_validator(mode="after")
        def _valid_window(self):
            if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
                raise ValueError("valid_from must not be later than valid_to")
            return self

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
            facts.append(ExtractedFact(**item).model_dump(mode="json"))
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

    threshold = (
        settings.MEMORY_DEDUP_THRESHOLD if similarity_threshold is None else similarity_threshold
    )

    # MEM-8: Sort for deterministic cluster output regardless of input order
    sorted_memories = sorted(memories, key=lambda m: (m.get("user_id", ""), m.get("key", "")))

    def _tokens(m: dict) -> set[str]:
        return _multilingual_tokens(f"{m.get('key', '')} {m.get('value', '')}")

    n = len(sorted_memories)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    token_sets = [_tokens(m) for m in sorted_memories]

    # Collapse identical token sets before pairwise comparison. Consolidation
    # commonly sees repeated preferences/facts; comparing only unique sets
    # preserves exact overlap semantics while avoiding quadratic duplicate work.
    identical: dict[object, list[int]] = {}
    for index, tokens in enumerate(token_sets):
        # Empty token sets never matched in the original algorithm and must
        # therefore remain separate singleton clusters.
        key: object = frozenset(tokens) if tokens else ("empty", index)
        identical.setdefault(key, []).append(index)
    representatives: list[int] = []
    for indexes in identical.values():
        representative = indexes[0]
        representatives.append(representative)
        for duplicate in indexes[1:]:
            parent[find(duplicate)] = find(representative)

    for offset, i in enumerate(representatives):
        for j in representatives[offset + 1 :]:
            ti, tj = token_sets[i], token_sets[j]
            if not ti or not tj:
                continue
            overlap = ti & tj
            shorter = min(len(ti), len(tj))
            if shorter > 0 and len(overlap) / shorter >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, m in enumerate(sorted_memories):
        groups.setdefault(find(i), []).append(m)

    return list(groups.values())


async def _semantic_cluster_memories(
    memories: list[dict],
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
    key: str,
    value: str,
    question: str,
    answer: str,
    min_overlap: int = 2,
) -> bool:
    """Check if extracted fact has keyword support in the original Q&A text.

    Prevents LLM hallucinations from being stored as memories: a fact whose
    key terms don't appear in the source conversation is likely fabricated.
    Uses Unicode word tokens plus CJK character bigrams so Chinese facts are
    validated with the same evidence requirement as whitespace languages.
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

    def _canonical_evidence_terms(text: str) -> str:
        # Closed-set bilingual aliases keep strict support checks useful when
        # the extraction model translates a language preference. This is not
        # semantic free-form matching: the verbatim evidence check still runs
        # before this validator.
        substitutions = (
            (r"(?i)\benglish\b|英文|英语", " language_en "),
            (r"(?i)\bchinese\b|中文|汉语|漢語", " language_zh "),
            (r"用户|使用者|用戶", " user "),
        )
        normalized = text
        for pattern, replacement in substitutions:
            normalized = re.sub(pattern, replacement, normalized)
        return normalized

    def _meaningful_tokens(text: str) -> set[str]:
        return {
            token
            for token in _multilingual_tokens(_canonical_evidence_terms(text))
            if token not in _STOPWORDS
        }

    fact_text = f"{key} {value}"
    fact_tokens = _meaningful_tokens(fact_text)
    value_tokens = _meaningful_tokens(value)
    if not fact_tokens:
        return False

    # Prefer literal value support before fuzzy token matching.  This preserves
    # corrections that span two adjacent clauses while preventing unrelated
    # entities on either side of a semicolon from being pooled into a made-up
    # relation ("Alice attended; Bob owns Orbit" != "Alice owns Orbit").
    normalized_value = "".join(
        char for char in _canonical_evidence_terms(value).casefold() if char.isalnum()
    )
    normalized_source = "".join(
        char
        for char in _canonical_evidence_terms(f"{question}\n{answer}").casefold()
        if char.isalnum()
    )

    english_negation = re.compile(
        r"\b(?:not|no|never|neither|nor|without|unknown|unclear|unconfirmed|"
        r"unassigned|withdrawn|rejected|isn't|aren't|wasn't|weren't|doesn't|"
        r"don't|didn't|cannot|can't|won't)\b",
        flags=re.IGNORECASE,
    )
    cjk_negations = ("不", "未", "没有", "并非", "不是", "未知", "不确定", "无法确认", "尚未")

    def _is_negative(text: str) -> bool:
        folded = text.casefold()
        return bool(english_negation.search(folded)) or any(term in text for term in cjk_negations)

    fact_negative = _is_negative(fact_text)
    if (
        normalized_value
        and normalized_value in normalized_source
        and _is_negative(value) == _is_negative(f"{question}\n{answer}")
    ):
        return True

    # Match one local evidence clause, instead of pooling unrelated tokens
    # across the entire turn.  Pooling made a question mentioning an entity
    # and an unrelated answer mentioning an attribute look like support.
    clauses = [
        part.strip()
        # A colon can be part of a time (16:00).  Semicolons are boundaries:
        # merging them allowed identity terms from one claim to validate a
        # different claim. Literal multi-clause corrections were handled above.
        for part in re.split(r"[\n.!?;\u3002\uff01\uff1f\uff1b]+", f"{question}\n{answer}")
        if part.strip()
    ]
    # Capitalized identifiers, codes and dates are identity-bearing in meeting
    # facts.  Requiring them in the evidence prevents a shared predicate/topic
    # from validating the wrong owner (for example Alice -> Bob).
    identity_tokens = {
        token.casefold()
        for token in re.findall(r"(?<![\w])(?:[A-Z][\w-]*|[A-Z0-9]*\d[A-Z0-9-]*)(?![\w])", value)
        if len(token) > 1
        and token.casefold()
        not in {
            "the",
            "a",
            "an",
            "not",
            "no",
            "unknown",
            "current",
            "currently",
            "english",
            "chinese",
            "user",
            "team",
            "project",
            "meeting",
            "we",
            "i",
        }
    }
    cjk_actor_match = re.match(r"^(.{2,8}?)(?:是|负责|担任|拥有)", value)
    cjk_actor = cjk_actor_match.group(1) if cjk_actor_match else None
    if cjk_actor in {"用户", "项目", "团队", "我们", "负责人"}:
        cjk_actor = None
    for clause in clauses:
        clause_tokens = _meaningful_tokens(clause)
        overlap = fact_tokens & clause_tokens
        if len(overlap) < min_overlap:
            continue
        # A positive candidate cannot be supported solely by a negative or
        # explicitly-unknown source clause (and vice versa).  This blocks
        # cases such as "Alice owns release" being accepted from "Alice does
        # not own release / the owner is unknown".
        if _is_negative(clause) != fact_negative:
            continue
        if identity_tokens and not identity_tokens.issubset(clause_tokens):
            continue
        if cjk_actor and cjk_actor not in clause:
            continue
        if value_tokens:
            coverage = len(value_tokens & clause_tokens) / len(value_tokens)
            if coverage < (0.35 if _CJK_RUN_RE.search(value) else 0.6):
                continue
        return True
    return False


def _is_semantic_duplicate(new_key: str, existing_keys: list[str]) -> bool:
    """Check if new_key is semantically similar to any existing key.

    Uses multilingual tokens with overlap coefficient (Szymkiewicz-Simpson).
    This measures how much of the shorter key's words appear in the longer one,
    which is more robust than Jaccard for comparing short phrases.
    """
    if not existing_keys:
        return False

    def _tokenize(text: str) -> set[str]:
        return _multilingual_tokens(text)

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
        if overlap_coeff >= settings.MEMORY_DEDUP_THRESHOLD:
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
        return (now + timedelta(days=settings.MEMORY_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    if ttl_days == -1:
        return None  # No expiry
    if ttl_days > 0:
        return (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
    return None
