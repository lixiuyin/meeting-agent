"""Shared evidence semantics for memory and knowledge-graph assertions.

LLM extraction output is an untrusted proposal.  This module provides the
small, deterministic checks that every persistence path must apply before a
proposal can become current state.
"""

from __future__ import annotations

import re
import unicodedata

_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?:[\n.!;\u3002\uff01\uff1b]+|(?<=[?\uff1f])|"
    r"\b(?:but|however|whereas)\b|但是|不过|然而|但(?=[^当]))",
    re.IGNORECASE,
)
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:who|what|when|where|why|how|which|is|are|was|were|do|does|did|"
    r"can|could|would|should|will)\b|(?:谁|什么|何时|什么时候|哪里|为何|为什么|"
    r"怎么|如何|是否))",
    re.IGNORECASE,
)
_CONDITIONAL_RE = re.compile(
    r"\b(?:if|unless|provided\s+that|assuming|subject\s+to|contingent\s+on)\b|"
    r"如果|若(?:是|果)?|除非|前提是|取决于|视.+而定",
    re.IGNORECASE,
)
_ATTRIBUTED_RE = re.compile(
    r"\b(?:said|stated|claimed|believes?|thinks?|suggested|according\s+to)\b|"
    r"表示|声称|认为|建议|据.+所说",
    re.IGNORECASE,
)
_UNCERTAIN_OR_PROPOSED_RE = re.compile(
    r"\b(?:may|might|could|would|plans?\s+to|proposes?|suggests?|candidate\s+for|"
    r"expected\s+to)\b|可能|也许|计划|拟|提议|建议|候选|预计",
    re.IGNORECASE,
)
_DEFINITE_FUTURE_RE = re.compile(
    r"\b(?:going\s+to|shall|will)\b|将(?:会|于|在|由|负责|完成|提交|交付|处理)?",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no\s+longer|neither|without)\b|"
    r"(?:并非|不是|没有|从未|不再|尚未|未曾|未)(?=\S)",
    re.IGNORECASE,
)


def normalize_assertion_text(value: object) -> str:
    """Normalize an entity/value for conservative literal containment."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value)).casefold()
        if character.isalnum()
    )


def split_assertion_clauses(text: str) -> list[str]:
    """Split evidence at sentence and contrast boundaries.

    Contrast boundaries matter for polarity: ``Alice did not use SQLite but
    uses Postgres`` contains two assertions with different polarity.
    """
    return [
        part.strip(" \t,:\uff0c\uff1a") for part in _CLAUSE_BOUNDARY_RE.split(text) if part.strip()
    ]


def clause_is_current_assertion(
    clause: str, *, positive: bool = True, allow_committed_future: bool = False
) -> bool:
    """Return whether a clause can support an asserted current fact."""
    stripped = clause.strip()
    from ..core.untrusted_material import has_embedded_directive

    if has_embedded_directive(clause):
        return False
    if (
        not stripped
        or "?" in stripped
        or "\uff1f" in stripped
        or _QUESTION_PREFIX_RE.match(stripped)
    ):
        return False
    if _CONDITIONAL_RE.search(stripped) or _ATTRIBUTED_RE.search(stripped):
        return False
    if _UNCERTAIN_OR_PROPOSED_RE.search(stripped):
        return False
    if not allow_committed_future and _DEFINITE_FUTURE_RE.search(stripped):
        return False
    return not (positive and _NEGATION_RE.search(stripped))


def current_supporting_clauses(
    evidence: str,
    *,
    terms: tuple[object, ...] = (),
    cue_pattern: str | None = None,
    positive: bool = True,
) -> list[str]:
    """Return same-clause, literal evidence for a current assertion."""
    normalized_terms = tuple(normalize_assertion_text(term) for term in terms if term)
    matches: list[str] = []
    for clause in split_assertion_clauses(evidence):
        normalized_clause = normalize_assertion_text(clause)
        if normalized_terms and not all(term in normalized_clause for term in normalized_terms):
            continue
        if cue_pattern and not re.search(cue_pattern, clause, re.IGNORECASE):
            continue
        if clause_is_current_assertion(clause, positive=positive):
            matches.append(clause)
    return matches
