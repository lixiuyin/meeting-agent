"""Typed retrieval plan that keeps user constraints separate from rewrites."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Literal

from ...core.memory_query import ActionConstraints, parse_action_constraints
from ._query import is_summary_intent
from ._query_analysis import QueryAnalysis, analyze_query

QueryIntent = Literal["factual", "summary", "comparison", "exhaustive"]

_SUMMARY_RE = re.compile(r"\b(?:summari[sz]e|overview|recap)\b|总结|概括|摘要", re.I)
_COMPARISON_RE = re.compile(
    r"\b(?:compare|difference|versus|vs\.?|contrast)\b|比较|区别|差异|对比", re.I
)
_EXHAUSTIVE_RE = re.compile(r"\b(?:all|every|complete|exhaustive)\b|全部|所有|完整|逐一", re.I)
_HISTORICAL_MARKER_RE = re.compile(r"\b(?:as\s+of|at\s+that\s+time)\b|截至|截止到|当时|那时", re.I)
_ABSOLUTE_DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"),
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?"),
)


def _absolute_date_matches(question: str) -> list[tuple[int, int, datetime.date]]:
    matches: list[tuple[int, int, datetime.date]] = []
    for pattern in _ABSOLUTE_DATE_PATTERNS:
        for match in pattern.finditer(question):
            try:
                value = datetime.date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            matches.append((match.start(), match.end(), value))
    return sorted(matches, key=lambda item: item[0])


def infer_query_intent(question: str) -> QueryIntent:
    if _EXHAUSTIVE_RE.search(question):
        return "exhaustive"
    if _COMPARISON_RE.search(question):
        return "comparison"
    if _SUMMARY_RE.search(question) or is_summary_intent(question):
        return "summary"
    return "factual"


def infer_historical_cutoffs(question: str) -> tuple[datetime.date, ...]:
    """Bind absolute dates to historical markers without choosing an unrelated date."""
    markers = list(_HISTORICAL_MARKER_RE.finditer(question))
    dates = _absolute_date_matches(question)
    if not dates:
        return ()

    # A comparison containing multiple explicit dates is intrinsically a
    # snapshot comparison even when the user does not repeat "as of" twice.
    # Intent labels are optimized for a primary execution path, but constraints
    # are compositional: "compare all" is both exhaustive and comparative.
    if _COMPARISON_RE.search(question) and len(dates) >= 2:
        return tuple(dict.fromkeys(value for _start, _end, value in dates))
    if not markers:
        return ()

    paired: list[datetime.date] = []
    for marker in markers:
        following = next(
            (
                value
                for start, _end, value in dates
                if start >= marker.end() and start - marker.end() <= 40
            ),
            None,
        )
        if following is not None:
            paired.append(following)

    # Comparison questions commonly state the first snapshot as a bare date
    # and mark only the second with "as of"/"截至". Both dates are required to
    # reconstruct the change, while the latest remains the document upper bound.
    return tuple(dict.fromkeys(paired))


def infer_historical_date_to(question: str) -> datetime.date | None:
    """Return the latest explicit historical cutoff for document retrieval."""
    cutoffs = infer_historical_cutoffs(question)
    return max(cutoffs) if cutoffs else None


@dataclass(frozen=True)
class QueryPlan:
    """One request's immutable retrieval meaning and search expressions."""

    original_query: str
    resolved_query: str
    semantic_queries: tuple[str, ...]
    lexical_queries: tuple[str, ...]
    analysis: QueryAnalysis
    intent: QueryIntent
    meeting_ids: tuple[int, ...] = field(default_factory=tuple)
    file_ids: tuple[int, ...] = field(default_factory=tuple)
    file_types: tuple[str, ...] = field(default_factory=tuple)
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    valid_at: datetime.datetime | None = None
    known_at: datetime.datetime | None = None
    historical_cutoffs: tuple[datetime.date, ...] = field(default_factory=tuple)
    action_constraints: ActionConstraints = field(default_factory=ActionConstraints)
    project_ids: tuple[str, ...] = field(default_factory=tuple)


def build_query_plan(
    *,
    original_query: str,
    resolved_query: str,
    variants: list[str] | None = None,
    known_speakers: list[str] | None = None,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    valid_at: datetime.datetime | None = None,
    known_at: datetime.datetime | None = None,
    project_ids: tuple[str, ...] = (),
) -> QueryPlan:
    analysis = analyze_query(original_query, known_speakers)
    semantic = tuple(dict.fromkeys(q for q in (resolved_query, *(variants or [])) if q))
    lexical = tuple(dict.fromkeys(q for q in (original_query, analysis.topic_query) if q))
    inferred_cutoffs = infer_historical_cutoffs(original_query)
    effective_cutoffs = (valid_at.date(),) if valid_at else inferred_cutoffs
    return QueryPlan(
        original_query=original_query,
        resolved_query=resolved_query,
        semantic_queries=semantic or (original_query,),
        lexical_queries=lexical or (original_query,),
        analysis=analysis,
        intent=infer_query_intent(original_query),
        meeting_ids=tuple(meeting_ids or ()),
        file_ids=tuple(file_ids or ()),
        file_types=tuple(file_types or ()),
        date_from=date_from,
        date_to=date_to or (max(inferred_cutoffs) if inferred_cutoffs else None),
        valid_at=valid_at,
        known_at=known_at,
        historical_cutoffs=effective_cutoffs,
        action_constraints=parse_action_constraints(original_query),
        project_ids=project_ids,
    )
