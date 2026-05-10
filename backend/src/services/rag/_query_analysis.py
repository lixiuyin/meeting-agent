"""Lightweight query analysis: extract speaker names and temporal hints.

All extraction is regex-based (no LLM calls) for speed.  The analysis
result feeds into filter building and post-retrieval boosting so that
speaker-scoped queries like "What did Alex say about AI?" route to the
correct chunks without cross-meeting contamination.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=512)
def _speaker_pattern_cached(name: str) -> re.Pattern[str]:
    """Compile and cache speaker regex to avoid re-compilation per query."""
    return re.compile(
        r"(?:^|(?<=[^a-zA-Z一-鿿]))" + re.escape(name) + r"(?=$|[^a-zA-Z一-鿿])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Temporal hint detection
# ---------------------------------------------------------------------------

# Relative region patterns — each maps to a (ratio_min, ratio_max) range.
# Order matters: more specific / compound patterns must come first.
_RELATIVE_TEMPORAL_PATTERNS: list[tuple[re.Pattern[str], float, float]] = [
    # Compound ranges (中后期, 前中期, etc.) — must precede individual matches
    (re.compile(r"前\s*中\s*期|前\s*中\s*段", re.I), 0.0, 0.70),
    (re.compile(r"中\s*后\s*期|中\s*后\s*段|中\s*后\s*半", re.I), 0.30, 1.0),
    (re.compile(r"前\s*半|first\s+half", re.I), 0.0, 0.55),
    (re.compile(r"后\s*半|second\s+half", re.I), 0.45, 1.0),
    # Individual regions
    (re.compile(r"(?:开头|开始|前期|前段|开场|beginning|start|opening|early)", re.I), 0.0, 0.38),
    (re.compile(r"(?:中间|中期|中段|middle|mid[\s-]?part)", re.I), 0.25, 0.75),
    (
        re.compile(
            r"(?:后期|后段|结尾|末尾|结束|尾声|end(?:ing)?|closing|later|latter|final)", re.I
        ),
        0.62,
        1.0,
    ),
]

# Chinese numeral → digit conversion (covers 一 through 十 and common compounds)
_ZH_NUM_MAP = {
    "零": 0,
    "〇": 0,  # noqa: RUF001 — intentional Chinese ideographic zero, not Latin O
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "半": 0.5,
}


def _parse_zh_number(s: str) -> float | None:
    """Parse a simple Chinese numeral string like '两', '三十', '十五', '半'."""
    s = s.strip()
    if not s:
        return None
    # Pure digit
    try:
        return float(s)
    except ValueError:
        pass
    if s == "半":
        return 0.5
    # Single character
    if len(s) == 1 and s in _ZH_NUM_MAP:
        return _ZH_NUM_MAP[s]
    # "十N" = 10+N, "N十" = N*10, "N十M" = N*10+M
    if "十" in s:
        parts = s.split("十", 1)
        tens = _ZH_NUM_MAP.get(parts[0], 1) if parts[0] else 1
        ones = _ZH_NUM_MAP.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones
    return None


# Numeric pattern: Arabic digits or Chinese numerals
_NUM = r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十半]+)"

# Absolute time patterns: "前N分钟", "最后N分钟", "first N minutes", etc.
_ABSOLUTE_TIME_RE = re.compile(
    r"(?:"
    r"(?:前|开[始头]?)\s*" + _NUM + r"\s*分钟"  # 前N分钟
    r"|(?:最后|后|结尾?)\s*" + _NUM + r"\s*分钟"  # 最后N分钟
    r"|(?:first|opening)\s+(\d+(?:\.\d+)?)\s*min(?:ute)?s?"  # first N minutes
    r"|(?:last|final|closing)\s+(\d+(?:\.\d+)?)\s*min(?:ute)?s?"  # last N minutes
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TemporalHint:
    """Desired time range expressed as fractions of meeting duration.

    When ``absolute_seconds`` is set, the filter can also use raw
    timestamp_start / timestamp_end metadata for precise matching.
    """

    ratio_min: float  # 0.0 - 1.0
    ratio_max: float
    absolute_seconds: tuple[float, float] | None = None  # (start_sec, end_sec) if known


@dataclass
class QueryAnalysis:
    """Result of lightweight query analysis."""

    speaker_names: list[str] = field(default_factory=list)
    temporal_hint: TemporalHint | None = None
    topic_query: str = ""


# ---------------------------------------------------------------------------
# Speaker name extraction
# ---------------------------------------------------------------------------

# English name pattern: capitalized word NOT abutting another ASCII letter,
# but allowing CJK characters on either side.
# e.g. "Alex发表了什么" → captures "Alex"
#      "What did Alex say" → captures "Alex" (not "What")
_NAME_PATTERN = re.compile(
    r"(?:^|(?<=[^a-zA-Z]))"  # not preceded by ASCII letter
    r"([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)"
    r"(?=$|[^a-zA-Z])"  # not followed by ASCII letter
)

_ZH_NAME_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,4})")

# Chinese patterns that signal a speaker-focused query
_SPEAKER_QUERY_PATTERNS_ZH = re.compile(
    r"(?:的观点|的看法|的意见|的建议|的想法|的发言|的论点|的立场|的讨论|的评论|"
    r"说了什么|讲了什么|提出了什么|表达了什么|提到了什么|讨论了什么|认为什么|觉得什么|"
    r"发表了什么|说过什么|认为|表示|指出|提出|提议|强调|"
    r"都说了|都提出|都讲了|都认为|都建议|"
    r"什么观点|什么看法|什么意见|什么想法|什么建议|什么立场)"
)

# Words that look like capitalized names but are not
_NON_NAME_WORDS = frozenset(
    {
        "What",
        "When",
        "Where",
        "Which",
        "Who",
        "How",
        "Why",
        "The",
        "This",
        "That",
        "These",
        "Those",
        "Did",
        "Does",
        "Has",
        "Was",
        "Were",
        "Are",
        "Is",
        "Please",
        "Could",
        "Would",
        "Should",
        "Can",
        "May",
        "About",
        "From",
        "With",
        "During",
        "After",
        "Before",
        "Meeting",
        "Session",
        "Discussion",
        "Summary",
        "All",
        "First",
        "Second",
        "Third",
        "Last",
        "Next",
        "Early",
        "Middle",
        "Late",
        "Start",
        "End",
        "Not",
        "And",
        "But",
        "For",
    }
)

_ZH_NON_NAMES = frozenset(
    {
        "会议",
        "讨论",
        "总结",
        "观点",
        "看法",
        "意见",
        "建议",
        "什么",
        "怎么",
        "如何",
        "哪些",
        "哪个",
        "谁的",
        "开始",
        "结束",
        "中间",
        "前期",
        "后期",
        "时候",
        "发表",
        "认为",
        "表示",
        "指出",
        "提出",
        "提议",
        "强调",
        "分享",
        "比较",
        "分析",
        "内容",
        "方面",
        "关于",
        "对于",
        "之间",
        "所有",
        "一些",
        "以及",
    }
)


def _extract_speaker_names_from_query(
    query: str, known_speakers: list[str] | None = None
) -> list[str]:
    """Extract likely person names from the query.

    Strategy (ordered by precision):
    1. If known_speakers are provided (from meeting metadata / speaker_mappings),
       do case-insensitive substring match — highest precision.
    2. Fall back to regex heuristics for capitalized English words and
       Chinese names adjacent to speaker-query patterns.
    """
    found: list[str] = []

    # Layer 1: known speakers — authoritative, word-boundary-aware matching.
    # Use regex with non-letter boundaries to avoid partial matches
    # (e.g. "A" matching inside "Alex", "Am" matching inside "America").
    if known_speakers:
        for sp in known_speakers:
            if len(sp) < 2:
                continue
            pattern = _speaker_pattern_cached(sp)
            if pattern.search(query):
                found.append(sp)
        if found:
            return found

    # Layer 2: English names via regex (CJK-boundary-aware)
    for m in _NAME_PATTERN.finditer(query):
        name = m.group(1)
        first_word = name.split()[0]
        if first_word not in _NON_NAME_WORDS:
            found.append(name)

    # CJK heuristic is a last resort when NO speaker list is available.
    # When known_speakers is provided, Layer 1 is authoritative: if nothing
    # matched there the query is not speaker-scoped, so stop here to avoid
    # regex artifacts (e.g. "明一下" from "说明一下").
    if not found and not known_speakers and _SPEAKER_QUERY_PATTERNS_ZH.search(query):
        for m in _ZH_NAME_PATTERN.finditer(query):
            name = m.group(1)
            if name not in _ZH_NON_NAMES and len(name) <= 3:
                found.append(name)

    return list(dict.fromkeys(found))


def _extract_temporal_hint(query: str) -> TemporalHint | None:
    """Detect if the query references a time position within the meeting.

    Supports:
    - Absolute time: "前2分钟", "最后5分钟", "first 3 minutes"
    - Relative regions: "前期", "中期", "后期", "中后期", "前半", etc.
    - Compound regions: "中后期" → ratio 0.30-1.0

    For absolute time references, we store the seconds in
    ``absolute_seconds`` so the filter can use raw timestamps when
    ``meeting_duration`` metadata is available.
    """
    # Priority 1: absolute time patterns (e.g. "前2分钟", "前两分钟", "last 5 minutes")
    m = _ABSOLUTE_TIME_RE.search(query)
    if m:
        front_zh, back_zh, front_en, back_en = m.groups()
        if front_zh or front_en:
            raw = front_zh or front_en
            minutes = _parse_zh_number(raw) if front_zh else float(raw)
            if minutes and minutes > 0:
                secs = minutes * 60
                return TemporalHint(
                    ratio_min=0.0,
                    ratio_max=0.0,
                    absolute_seconds=(0.0, secs),
                )
        if back_zh or back_en:
            raw = back_zh or back_en
            minutes = _parse_zh_number(raw) if back_zh else float(raw)
            if minutes and minutes > 0:
                secs = minutes * 60
                return TemporalHint(
                    ratio_min=1.0,
                    ratio_max=1.0,
                    absolute_seconds=(-secs, 0.0),
                )

    # Priority 2: relative region patterns
    for pattern, lo, hi in _RELATIVE_TEMPORAL_PATTERNS:
        if pattern.search(query):
            return TemporalHint(ratio_min=lo, ratio_max=hi)
    return None


def _strip_speaker_prefix(query: str, speakers: list[str]) -> str:
    """Remove speaker name from query to get the pure topic for embedding."""
    topic = query
    for name in speakers:
        topic = re.sub(re.escape(name), "", topic, flags=re.IGNORECASE)
    topic = re.sub(r"\s{2,}", " ", topic).strip()
    return topic


def analyze_query(query: str, known_speakers: list[str] | None = None) -> QueryAnalysis:
    """Analyze a user query and extract speaker names and temporal hints.

    Returns a QueryAnalysis with:
      - speaker_names: person names found in the query
      - temporal_hint: time region if the query references "beginning/middle/end"
      - topic_query: the query with speaker names stripped (for topical matching)
    """
    speakers = _extract_speaker_names_from_query(query, known_speakers)
    temporal = _extract_temporal_hint(query)
    topic = _strip_speaker_prefix(query, speakers) if speakers else query
    return QueryAnalysis(
        speaker_names=speakers,
        temporal_hint=temporal,
        topic_query=topic,
    )
