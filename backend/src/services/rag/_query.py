"""Query rewriting and adaptive top-k logic."""

import asyncio
import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Literal

from cachetools import TTLCache
from langchain_core.prompts import ChatPromptTemplate

from ...core.config import settings
from ..llm import get_llm

logger = logging.getLogger(__name__)

_QUERY_REWRITE_PROMPT = """Rewrite the query to improve document retrieval quality.
- If the query is in Chinese, include relevant English technical terms.
- Expand abbreviations and acronyms.
- Add synonymous phrasings that might match the document language.
- Keep the core intent unchanged.
- Return ONLY the rewritten query, nothing else.

Original query: {query}{speaker_hint}"""

_COMPLEXITY_KEYWORDS = {
    "how is",
    "how are",
    "how does",
    "how do",
    "how was",
    "how were",
    "how to",
    "how many",
    "why",
    "compare",
    "analyze",
    "list all",
    "summary of",
    "relationship between",
    "difference between",
    "why did",
    "what caused",
    "step by step",
    "explain",
    "all of the",
    "what is the",
    "what are the",
    "could you",
    "can you",
    "generate",
    "proposal",
    "write",
    "create",
    "draft",
    "summarize",
    "怎么",
    "如何",
    "流程",
    "步骤",
    "原理",
    "解释",
    "讲解",
    "详细说明",
    "详细介绍",
    "为什么",
    "为何",
}
_SIMPLE_QUESTION_MIN_CHARS = 30
_SIMPLE_QUERY_TOP_K = 3  # QR-4: Extracted constant, was hardcoded 3
_MAX_TOP_K_HARD_LIMIT = 50  # M-14: Prevent unbounded retrieval

QueryRoute = Literal["atomic_fact", "bounded_synthesis", "analytical_synthesis"]
AnswerType = Literal["person", "date", "number", "boolean", "short_text", "explanation"]


@dataclass(frozen=True)
class QueryRouteDecision:
    """Conservative, explainable routing decision made before retrieval.

    ``atomic_fact`` means only that a request is eligible for a low-cost probe.
    The fast generation path still requires a separate post-retrieval evidence
    decision; query shape alone is never sufficient.
    """

    route: QueryRoute
    confidence: float
    answer_type: AnswerType
    requires_synthesis: bool
    reasons: tuple[str, ...]

    @property
    def fast_candidate(self) -> bool:
        return self.route == "atomic_fact" and not self.requires_synthesis


@dataclass(frozen=True)
class FastEvidenceDecision:
    """Whether a fast-path retrieval result is concentrated enough to trust."""

    safe: bool
    confidence: float
    reason: str


_ATOMIC_QUERY_PATTERNS: tuple[tuple[AnswerType, re.Pattern[str]], ...] = (
    (
        "person",
        re.compile(
            r"(?:\bwho\b|谁|哪位|负责人(?:是|为)?谁|谁(?:负责|拥有|提出|决定))",
            re.IGNORECASE,
        ),
    ),
    (
        "date",
        re.compile(
            r"(?:\bwhen\b|\bwhat\s+(?:is\s+the\s+)?(?:date|time|deadline)\b|"
            r"什么时候|何时|截止(?:日期|时间)|日期是什么)",
            re.IGNORECASE,
        ),
    ),
    (
        "number",
        re.compile(r"(?:\bhow\s+(?:many|much)\b|多少|几个|数量(?:是|为)?多少)", re.IGNORECASE),
    ),
    (
        "boolean",
        re.compile(
            r"(?:^(?:is|are|was|were|did|does|has|have)\b|是否|有没有|完成了吗)",
            re.IGNORECASE,
        ),
    ),
    (
        "short_text",
        re.compile(
            r"(?:\bwhat\s+(?:is\s+the\s+)?(?:version|status|id|identifier)\b|"
            r"版本(?:是|为)?什么|状态(?:是|为)?什么|编号(?:是|为)?什么)",
            re.IGNORECASE,
        ),
    ),
)

_ANALYTICAL_MARKERS = {
    "compare",
    "comparison",
    "contrast",
    "difference",
    "why",
    "summary",
    "summarize",
    "all of the",
    "比较",
    "对比",
    "差异",
    "为什么",
    "原因",
    "总结",
    "概览",
    "所有",
    "全部",
}
_ATOMIC_COMPLEXITY_EXEMPTIONS = {"how many", "what is the", "what are the"}
_DATE_EVIDENCE_PATTERN = re.compile(
    r"(?:\b\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\b|\d{1,2}\s*月\s*\d{0,2}\s*日?)",
    re.IGNORECASE,
)
_NUMBER_EVIDENCE_PATTERN = re.compile(r"(?:\d|[零〇一二两三四五六七八九十百千万亿])")


def classify_query_route(
    question: str,
    *,
    meeting_count: int = 0,
    file_count: int = 0,
) -> QueryRouteDecision:
    """Classify request shape with a conservative atomic-fact allow-list.

    False-fast errors are more harmful than routing a simple request through
    the normal path, so unknown shapes default to bounded synthesis.  This
    decision is intentionally independent of retrieval confidence.
    """
    text = question.strip()
    folded = text.casefold()
    if not text:
        return QueryRouteDecision("bounded_synthesis", 1.0, "explanation", True, ("empty_query",))
    if _ANAPHORA_PATTERN.search(text):
        return QueryRouteDecision(
            "bounded_synthesis", 0.98, "explanation", True, ("context_reference",)
        )

    complexity_hits = tuple(
        sorted(
            marker
            for marker in _COMPLEXITY_KEYWORDS
            if marker in folded and marker not in _ATOMIC_COMPLEXITY_EXEMPTIONS
        )
    )
    if complexity_hits:
        analytical = (
            meeting_count > 1
            or file_count > 1
            or not (meeting_count or file_count)
            or any(marker in folded for marker in _ANALYTICAL_MARKERS)
        )
        return QueryRouteDecision(
            "analytical_synthesis" if analytical else "bounded_synthesis",
            0.98,
            "explanation",
            True,
            ("complexity_marker", *complexity_hits[:3]),
        )

    max_words = max(1, int(getattr(settings, "RAG_FAST_PATH_MAX_WORDS", 12)))
    if len(text.split()) <= max_words:
        answer_types = tuple(
            answer_type for answer_type, pattern in _ATOMIC_QUERY_PATTERNS if pattern.search(text)
        )
        if len(answer_types) == 1:
            return QueryRouteDecision(
                "atomic_fact",
                0.97,
                answer_types[0],
                False,
                ("atomic_answer_shape", answer_types[0]),
            )
        if len(answer_types) > 1:
            return QueryRouteDecision(
                "bounded_synthesis",
                0.95,
                "explanation",
                True,
                ("multiple_answer_shapes", *answer_types),
            )

    return QueryRouteDecision(
        "bounded_synthesis",
        0.70,
        "explanation",
        True,
        ("not_provably_atomic",),
    )


def validate_fast_path_evidence(
    decision: QueryRouteDecision,
    docs: list[dict[str, Any]],
    *,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> FastEvidenceDecision:
    """Validate that an atomic probe found concentrated, high-signal evidence.

    Retrieval adapters expose higher-is-better relevance scores. BM25 scores
    are not bounded, so this gate uses relative concentration rather than
    pretending that every backend shares an absolute probability scale.
    """
    if not decision.fast_candidate:
        return FastEvidenceDecision(False, 0.0, "query_requires_synthesis")
    ranked = [doc for doc in docs if str(doc.get("content") or "").strip()]
    if not ranked:
        return FastEvidenceDecision(False, 0.0, "no_evidence")

    scores = [max(0.0, float(doc.get("score", 0.0) or 0.0)) for doc in ranked]
    top_score = scores[0]
    if top_score <= 0.0:
        return FastEvidenceDecision(False, 0.0, "weak_top_score")

    evidence_text = "\n".join(str(doc.get("content") or "") for doc in ranked[:3])
    if decision.answer_type == "date" and not _DATE_EVIDENCE_PATTERN.search(evidence_text):
        return FastEvidenceDecision(False, 0.0, "missing_date_answer_shape")
    if decision.answer_type == "number" and not _NUMBER_EVIDENCE_PATTERN.search(evidence_text):
        return FastEvidenceDecision(False, 0.0, "missing_number_answer_shape")

    signal_floor = top_score * 0.75
    strong_docs = [
        doc for doc, score in zip(ranked[:3], scores[:3], strict=False) if score >= signal_floor
    ]

    def _source_identity(doc: dict[str, Any]) -> tuple[str, Any] | None:
        metadata = doc.get("metadata") or {}
        if metadata.get("file_id") is not None:
            return ("file", metadata["file_id"])
        if metadata.get("meeting_id") is not None:
            return ("meeting", metadata["meeting_id"])
        if metadata.get("source"):
            return ("source", metadata["source"])
        return None

    source_keys = {_source_identity(doc) for doc in strong_docs}
    if None in source_keys:
        return FastEvidenceDecision(False, 0.0, "missing_source_identity")
    explicitly_single_file = bool(file_ids and len(set(file_ids)) == 1)
    if len(source_keys) > 1 and not explicitly_single_file:
        return FastEvidenceDecision(False, top_score, "evidence_spans_sources")

    scope_bonus = 0.05 if meeting_ids or file_ids else 0.0
    confidence = min(1.0, 0.85 + scope_bonus)
    return FastEvidenceDecision(True, confidence, "concentrated_atomic_evidence")


# Anaphora / pronouns that signal the query needs context rewriting
_ANAPHORA_PATTERN = re.compile(
    r"\b(it|that|this|they|them|these|those|he|she|his|her|the above|the previous|the last)\b"
    r"|(?<!其)[他她它](?:们)?|上述|前者|后者|刚才|之前提到|继续"
    r"|^(?:那|这)(?:个|些|项|件|么|截止|负责人)|呢[\uff1f?]?$",
    re.IGNORECASE,
)

_REWRITE_MAX_TOKENS = 6  # skip rewrite if query is this short or less


def _is_context_free_query(question: str, *, include_summary: bool = False) -> bool:
    """Return whether a standalone fact lookup can use the low-latency path.

    The normal rewrite/resolver and hierarchical summary routing are useful for
    follow-ups and broad analytical questions, but they add multiple remote
    calls for short, self-contained lookups. Keep the gate conservative for
    follow-ups and comparisons; explicitly scoped summaries may skip redundant
    query rewriting but still require full answer generation. The
    word budget is configurable so deployments can tune recall without code.
    """
    if not question or _ANAPHORA_PATTERN.search(question):
        return False
    folded = question.casefold()
    # Chinese has no whitespace word boundaries. Do not treat a paragraph,
    # comparison or contextual follow-up as a one-word fact lookup.
    if len(re.findall(r"[\u3400-\u9fff]", question)) > 24 or any(
        marker in folded
        for marker in (
            "比较",
            "对比",
            "分析",
            "为什么",
            "原因",
            "关系",
            "所有",
            "全部",
            "这些",
            "那些",
            "上述",
            "它们",
            "继续",
            "总结",
            "概览",
            "主要内容",
        )
    ):
        return False
    # ``what is/are the ...`` is common factual lookup phrasing; keep it on the
    # fast path. Other complexity markers (compare, explain, list all, why)
    # still require the full resolver and broad-recall pipeline. Short scoped
    # summaries are explicitly relaxed below for the streaming SLO guard.
    complexity_markers = _COMPLEXITY_KEYWORDS - {"what is the", "what are the"}
    summary_relaxation = include_summary and any(
        marker in folded for marker in {"summarize", "summary of", "overview"}
    )
    if any(keyword in folded for keyword in complexity_markers) and not summary_relaxation:
        return False
    return include_summary or not is_summary_intent(question)


def is_fast_query(question: str, *, include_summary: bool = False) -> bool:
    """Return whether a query is eligible for a post-retrieval fast-path check.

    ``include_summary`` remains for API compatibility; summaries always require
    synthesis and are intentionally excluded from the atomic allow-list.
    """
    _ = include_summary
    from ...core.config import settings as _settings

    if not getattr(_settings, "RAG_FAST_PATH_ENABLED", True):
        return False
    return classify_query_route(question).fast_candidate


# Singleton for the lightweight rewrite model (thread-safe)
_rewrite_llm: Any = None
_rewrite_llm_lock = threading.Lock()
_cached_rewrite_key: tuple[str, str, str] | None = None

# C-2: TTL cache for query rewrites to avoid repeated LLM calls for identical queries.
_REWRITE_CACHE: TTLCache = TTLCache(maxsize=2048, ttl=600)
_REWRITE_CACHE_LOCK = threading.Lock()


def _clear_rewrite_cache() -> None:
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE.clear()


from ...core.settings_epoch import register_epoch_cache  # noqa: E402

register_epoch_cache(_clear_rewrite_cache)


def _get_rewrite_llm() -> Any:
    """Get or create the singleton lightweight rewrite model (thread-safe).

    All reads/writes of ``_rewrite_llm`` and ``_cached_rewrite_key`` happen
    inside ``_rewrite_llm_lock`` to prevent data races under concurrent access.
    """
    global _rewrite_llm, _cached_rewrite_key
    rewrite_model = settings.QUERY_REWRITE_MODEL
    if not rewrite_model:
        with _rewrite_llm_lock:
            if _rewrite_llm is not None:
                _rewrite_llm = None
                _cached_rewrite_key = None
                logger.info("Query rewrite LLM singleton cleared (model unset)")
        return None
    with _rewrite_llm_lock:
        api_key = settings.LLM_API_KEY.get_secret_value()
        base_url = settings.LLM_BASE_URL or "https://api.openai.com/v1"
        rewrite_key = (
            rewrite_model,
            base_url,
            hashlib.sha256(api_key.encode()).hexdigest() if api_key else "",
        )
        # An older request snapshot may recreate a client after a global reset.
        # Key every access so a newer request can never inherit that client.
        if _cached_rewrite_key is not None and _cached_rewrite_key != rewrite_key:
            _rewrite_llm = None
            _cached_rewrite_key = None
        if _rewrite_llm is None:
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {
                "model": rewrite_model,
                "temperature": 0.0,
                "max_tokens": 128,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            _rewrite_llm = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
            _cached_rewrite_key = rewrite_key
        return _rewrite_llm


def reset_rewrite_llm() -> None:
    """Reset the cached rewrite model singleton (call when settings change)."""
    global _rewrite_llm, _cached_rewrite_key
    with _rewrite_llm_lock:
        _rewrite_llm = None
        _cached_rewrite_key = None
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE.clear()


def _is_simple_query(question: str) -> bool:
    """Return True if the query is short and has no anaphora, making rewrite unnecessary."""
    words = question.split()
    return len(words) <= _REWRITE_MAX_TOKENS and _is_context_free_query(question)


async def rewrite_query(
    question: str,
    *,
    speaker_names: list[str] | None = None,
) -> str:
    """Use LLM to rewrite query for better retrieval. Returns original on failure.

    When ``speaker_names`` are provided, they are injected into the prompt
    so the rewritten query preserves speaker identity (HIGH-6).
    """
    if _is_simple_query(question) or is_fast_query(question):
        logger.debug("Skipping rewrite for simple query: '%s'", question[:50])
        return question

    # C-2: Check TTL cache before calling LLM
    cache_key = (
        question,
        tuple(sorted(n.casefold() for n in speaker_names)) if speaker_names else (),
    )
    with _REWRITE_CACHE_LOCK:
        cached = _REWRITE_CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Query rewrite cache hit for: '%s'", question[:50])
        return cached

    rewrite_llm = _get_rewrite_llm()
    llm = rewrite_llm if rewrite_llm else get_llm()
    prompt = ChatPromptTemplate.from_messages([("human", _QUERY_REWRITE_PROMPT)])
    # HIGH-6: Preserve speaker names so rewritten queries match speaker-filtered
    # chunks. Inject via structured prompt variable to prevent prompt injection.
    speaker_hint = ""
    if speaker_names:
        # Sanitize: only allow letters, digits, spaces, hyphens, apostrophes, dots,
        # and CJK characters. Reject any speaker name that could inject prompt content.
        import unicodedata

        safe_names = []
        for name in speaker_names:
            if not name or len(name) > 80:
                continue
            # Allow letters (any script), digits, spaces, hyphens, apostrophes, periods
            if all(
                unicodedata.category(c).startswith(("L", "N")) or c in (" ", "-", "'", ".", "·")
                for c in name
            ):
                safe_names.append(name)
        if safe_names:
            speaker_hint = f"\nKnown speakers in this context: {', '.join(safe_names)}."
    formatted = prompt.format_messages(query=question, speaker_hint=speaker_hint)
    try:
        # Use the provider's async transport so the optional rewrite deadline
        # cancels the request. A timed-out worker thread otherwise keeps retrying
        # in the background and delays process shutdown. Successful rewrites
        # already have their own scoped cache above; provider retries stay
        # inside this single deadline.
        response = await asyncio.wait_for(
            llm.ainvoke(formatted),
            timeout=settings.QUERY_REWRITE_TIMEOUT_SECONDS,
        )
        from ..llm import extract_visible_text

        result = extract_visible_text(response)
        # QR-2: Log token usage for cost observability
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            token_in = response.usage_metadata.get("input_tokens", 0)
            token_out = response.usage_metadata.get("output_tokens", 0)
            logger.info(
                "Query rewritten: '%s' -> '%s' (tokens: %d in, %d out)",
                question[:50],
                result[:50],
                token_in,
                token_out,
            )
        else:
            logger.info("Query rewritten: '%s' -> '%s'", question[:50], result[:50])
        rewritten = result.strip()
        with _REWRITE_CACHE_LOCK:
            _REWRITE_CACHE[cache_key] = rewritten
        return rewritten
    except Exception:
        logger.warning("Query rewrite failed, using original", exc_info=True)
        return question


_SUMMARY_INTENT_PATTERNS = (
    re.compile(
        r"\b(summari[sz]e|overview|list\s+.*topics|what\s+(was|were)\s+discussed|compare)\b",
        re.IGNORECASE,
    ),
    re.compile(r"总结|概述|梳理|都讨论了|讲了什么|主要内容|对比"),
)


def is_summary_intent(question: str) -> bool:
    """Detect if the question asks for a summary or broad overview."""
    return any(p.search(question) for p in _SUMMARY_INTENT_PATTERNS)


def determine_adaptive_top_k(
    question: str,
    user_requested_k: int | None,
    *,
    is_broad_recall: bool = False,
) -> int:
    """Decide top_k based on question complexity. User override always wins.

    When ``is_broad_recall`` is True (no file scope, with or without meeting
    scope), the floor is raised to ensure broad questions retrieve enough
    context. Summary intent questions get an even higher floor.
    """
    if user_requested_k is not None:
        return min(user_requested_k, _MAX_TOP_K_HARD_LIMIT)
    base = settings.TOP_K
    if is_broad_recall:
        if is_summary_intent(question):
            return min(max(base, settings.SUMMARY_INTENT_TOP_K), _MAX_TOP_K_HARD_LIMIT)
        return min(max(base, 8), _MAX_TOP_K_HARD_LIMIT)
    if is_summary_intent(question):
        return min(max(base, settings.SUMMARY_INTENT_TOP_K), _MAX_TOP_K_HARD_LIMIT)
    q = question.lower().strip()
    if len(q) < _SIMPLE_QUESTION_MIN_CHARS and not any(kw in q for kw in _COMPLEXITY_KEYWORDS):
        return _SIMPLE_QUERY_TOP_K
    return min(base, _MAX_TOP_K_HARD_LIMIT)
