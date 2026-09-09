"""History-aware query resolver — replaces rewrite_query for multi-turn conversations.

Rewrites anaphoric questions (``"这个决定是谁提出的?"``) into self-contained
queries by inlining entity references from conversation history.  Uses a single
LLM call with a lightweight model, gated by a syntactic check so that
self-contained first turns pay zero cost.

Two-tier cache:
- L1: ``(session_id, question_normalized)`` — handles retry/regenerate within same turn.
- L2: ``question_normalized`` only — cross-session LRU (reuses existing rewrite cache).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import TYPE_CHECKING

from cachetools import TTLCache
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from src.services.chain._steps_session import sanitize_history_messages
from src.services.rag._query import _ANAPHORA_PATTERN, _is_simple_query

from ...core.config import settings
from ..llm import get_llm

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_RESOLVER_PROMPT = """You rewrite multi-turn questions into self-contained search queries.

Rules:
- If the new question already mentions all entities/topics it refers to,
  return it UNCHANGED.
- Otherwise, inline the meeting/file/topic names from history that the
  question is referring to.
- Do NOT add information that wasn't in history or the question.
- Do NOT include citation markers, brackets, or quotes.
- Output ONE LINE: the rewritten query.

History (last {n} turns):
{history}

New question: {question}

Rewritten:"""

_MAX_OUTPUT_RATIO = 4  # guard: output > 4x input tokens → fallback to original
_L1_MAX = 64


class _ThreadSafeTTLCache(TTLCache):
    """TTLCache wrapper that serializes all access through an RLock.

    cachetools' caches are not thread-safe — concurrent ``__setitem__`` calls
    can race during eviction (``popitem`` iterates the underlying OrderedDict),
    raising ``RuntimeError: OrderedDict mutated during iteration``.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)

    def __setitem__(self, key, value) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key) -> None:
        with self._lock:
            super().__delitem__(key)

    def __contains__(self, key) -> bool:
        with self._lock:
            return super().__contains__(key)

    def __len__(self) -> int:
        with self._lock:
            return super().__len__()

    def __iter__(self):
        with self._lock:
            # Snapshot keys under the lock so callers can iterate safely.
            return iter(list(super().__iter__()))

    def get(self, key, default=None):
        with self._lock:
            return super().get(key, default)

    def clear(self) -> None:
        with self._lock:
            super().clear()


# L1 cache: (session_id, normalized_question) → resolved query.
_l1_cache: TTLCache[str, str] = _ThreadSafeTTLCache(maxsize=_L1_MAX, ttl=600)


def _normalize(q: str) -> str:
    return q.strip().lower()


def _history_signature(history: list[BaseMessage]) -> str:
    rendered = "\n".join(f"{type(msg).__name__}:{msg.content}" for msg in history)
    # MD5 used for cache-key fingerprinting only, not security.
    return hashlib.md5(rendered.encode(), usedforsecurity=False).hexdigest()


def _l1_key(
    session_id: str,
    question: str,
    history: list[BaseMessage] | None = None,
    *,
    invoke_identity: int | None = None,
) -> str:
    from ...core.settings_epoch import get_settings_epoch

    epoch = get_settings_epoch()
    history_sig = _history_signature(history or [])
    invoke_sig = invoke_identity if invoke_identity is not None else id(_invoke_resolver)
    # MD5 used for cache-key fingerprinting only, not security.
    question_hash = hashlib.md5(_normalize(question).encode(), usedforsecurity=False).hexdigest()
    return f"{session_id}:{epoch}:{question_hash}:{history_sig}:{invoke_sig}"


def _should_resolve(question: str, history: list[BaseMessage]) -> bool:
    """Syntactic gate: skip resolver when there's nothing to resolve."""
    if not getattr(settings, "RESOLVER_ENABLED", True):
        return False
    if not history:
        return False
    if _is_simple_query(question):
        return False
    if _ANAPHORA_PATTERN.search(question):
        return True
    short = len(question.split()) <= 6
    return not (short and not any(c.isalpha() for c in question if ord(c) > 0x2000))


async def _invoke_resolver(llm, formatted):
    """Use cancellable transport; cache successful resolutions at the caller."""
    return await llm.ainvoke(formatted)


async def resolve_query(
    question: str,
    history: list[BaseMessage],
    *,
    session_id: str | None = None,
    llm: BaseChatModel | None = None,
) -> str:
    """Resolve anaphora and inline references from history.

    Returns the resolved query string, or the original question when the
    resolver is skipped (gate), cached, or errors.
    """
    if not _should_resolve(question, history):
        return question

    token_budget = getattr(settings, "RESOLVER_HISTORY_TOKEN_BUDGET", 1500)
    n_turns = getattr(settings, "RESOLVER_HISTORY_TURNS", 3)
    last_n = history[-(n_turns * 2) :] if len(history) > n_turns * 2 else history
    sanitized = sanitize_history_messages(last_n, max_tokens=token_budget)

    # L1 cache hit within same session+question
    if session_id:
        key = _l1_key(session_id, question, sanitized)
        cached = _l1_cache.get(key)
        if cached is not None:
            logger.debug("Resolver L1 cache hit for session=%s", session_id[:8])
            return cached

    history_text = _format_history(sanitized)

    llm = llm or get_llm()
    prompt_text = _RESOLVER_PROMPT.replace("{n}", str(len(sanitized) // 2))
    prompt = ChatPromptTemplate.from_messages([("human", prompt_text)])
    formatted = prompt.format_messages(
        history=history_text,
        question=question,
    )

    timeout = getattr(settings, "RESOLVER_TIMEOUT_S", 4.0)
    try:
        import asyncio

        response = await asyncio.wait_for(
            _invoke_resolver(llm, formatted),
            timeout=timeout,
        )
        result = response.content if hasattr(response, "content") else str(response)
        result = _parse_output(result, question)

        # Length guard against garbage output (token-based for CJK accuracy)
        from ..tokenizer import count_tokens

        result_tokens = count_tokens(result)
        question_tokens = max(count_tokens(question), 1)
        if result_tokens > question_tokens * _MAX_OUTPUT_RATIO:
            logger.warning(
                "Resolver output too long (%d vs %d tokens), using original",
                result_tokens,
                question_tokens,
            )
            return question

        # Store in L1 cache (TTLCache is thread-safe, handles eviction)
        if session_id:
            key = _l1_key(session_id, question, sanitized)
            _l1_cache[key] = result

        logger.info("Resolved query: '%s' -> '%s'", question[:50], result[:50])
        return result
    except Exception:
        logger.warning("Resolver failed, using original question", exc_info=True)
        return question


def _format_history(messages: list[BaseMessage]) -> str:
    """Format messages as numbered turns for the resolver prompt."""
    lines: list[str] = []
    for i, msg in enumerate(messages, 1):
        role = "User" if isinstance(msg, HumanMessage) else "Assistant"
        content = str(msg.content)[:300]
        lines.append(f"{i}. [{role}] {content}")
    return "\n".join(lines)


def _parse_output(raw: str, fallback: str) -> str:
    """Extract the first line of resolver output, stripping preamble."""
    text = raw.strip()
    if text.lower().startswith("rewritten:"):
        text = text[len("rewritten:") :].strip()
    line = text.splitlines()[0].strip() if text else fallback
    return line or fallback


def clear_l1_cache(*, session_id: str | None = None) -> None:
    """Clear L1 resolver cache.  If session_id given, only clear entries for that session."""
    if session_id:
        prefix = f"{session_id}:"
        for k in [k for k in _l1_cache if k.startswith(prefix)]:
            del _l1_cache[k]
    else:
        _l1_cache.clear()


# Register with settings epoch so cache is cleared when config changes (H11).
from ...core.settings_epoch import register_epoch_cache  # noqa: E402

register_epoch_cache(clear_l1_cache)
