"""Single-pass helpers for parsing and validating LLM-extracted memory facts."""

from __future__ import annotations

from dataclasses import dataclass

from ._common import _MAX_IMPORTANCE, _MIN_IMPORTANCE
from ._parsers import _compute_expiry, _is_fact_supported, _parse_fact_json


@dataclass(frozen=True)
class ExtractedFact:
    key: str
    value: str
    importance: int
    category: str | None
    expires_at: str | None


def extract_facts(
    *,
    content: str,
    question: str,
    answer: str,
    max_facts: int,
) -> list[ExtractedFact]:
    """Parse LLM output once and return validated memory candidates."""
    facts = _parse_fact_json(content)
    if not facts:
        return []

    candidates: list[ExtractedFact] = []
    for fact in facts[:max_facts]:
        key = (fact.get("key") or "").strip()
        value = (fact.get("value") or "").strip()
        if not key or not value:
            continue
        if not _is_fact_supported(key, value, question, answer):
            continue

        importance = min(_MAX_IMPORTANCE, max(_MIN_IMPORTANCE, int(fact.get("importance", 3))))
        category = fact.get("category") or None
        ttl_days = fact.get("ttl_days")
        candidates.append(
            ExtractedFact(
                key=key,
                value=value,
                importance=importance,
                category=category,
                expires_at=_compute_expiry(ttl_days),
            )
        )
    return candidates
