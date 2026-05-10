import asyncio
import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ...core import database as db
from ._context import PipelineContext

MAX_HISTORY_MESSAGE_CHARS = 8_000
MAX_HISTORY_TOKENS = 4_096
MAX_RESOLVER_HISTORY_MESSAGES = 8

# Inline citation markers like [1], [12], [1][3]. These belong to the *past*
# answer's source numbering; carrying them into the next turn only confuses the
# LLM's new numbering scheme and wastes tokens. Full URLs like https://host/[x]
# are not affected because the regex requires the bracket to sit at a
# non-alphanumeric boundary.
_CITATION_MARKER_RE = re.compile(
    r"(?<!\w)\[\d{1,3}\](?=[\s,.;:!?\)\]\[]|$)(?:\s*\[\d{1,3}\](?=[\s,.;:!?\)\]\[]|$))*"
)


def _strip_citation_markers(content: str) -> str:
    """Remove inline [N] citation markers and collapse leftover whitespace."""
    if "[" not in content:
        return content
    stripped = _CITATION_MARKER_RE.sub("", content)
    # Collapse the double-spaces / trailing punctuation artefacts the removal
    # leaves behind: "the deadline  is March" → "the deadline is March".
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r"\s+([,.;:!?])", r"\1", stripped)
    return stripped.strip()


def sanitize_history_messages(messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    """Clamp chat history by type/content and total token budget."""
    from ...core.config import settings
    from ..tokenizer import count_tokens

    sanitized: list[BaseMessage] = []
    for msg in messages:
        if not isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
            continue
        content = str(msg.content)
        if not content:
            continue
        # Assistant turns carry [N] citation markers that are only meaningful
        # in the turn they were emitted in. Drop them for history replay.
        if isinstance(msg, AIMessage):
            content = _strip_citation_markers(content)
            if not content:
                continue
        if len(content) > MAX_HISTORY_MESSAGE_CHARS:
            content = content[:MAX_HISTORY_MESSAGE_CHARS]
        sanitized.append(type(msg)(content=content))

    if max_tokens <= 0 or not sanitized:
        return sanitized

    model = settings.LLM_MODEL
    per_msg_tokens = []
    total = 0
    for msg in sanitized:
        t = count_tokens(str(msg.content), model) + 4
        per_msg_tokens.append(t)
        total += t

    # Remove complete Human+AI turns (not single messages) so that entity
    # references defined in an early HumanMessage are not orphaned when the
    # paired AIMessage is kept.
    start = 0
    while start < len(sanitized) and total > max_tokens:
        removed_tokens = per_msg_tokens[start]
        start += 1
        if start < len(sanitized) and isinstance(sanitized[start], AIMessage):
            removed_tokens += per_msg_tokens[start]
            start += 1
        total -= removed_tokens
    return sanitized[start:]


def ensure_session(ctx: PipelineContext) -> None:
    """Create or validate the chat session, updating ctx.session_id."""
    ctx.trace.start_span("ensure_session", "session")
    try:
        if not ctx.session_id:
            with db.get_write_connection() as conn:
                ctx.session_id = db.create_session(
                    conn,
                    user_id=ctx.user_id,
                    title=ctx.question[:50],
                )
        else:
            # M-23: Use INSERT OR IGNORE to atomically ensure the session exists
            # without a check-then-insert race between concurrent requests.
            with db.get_write_connection() as conn:
                db.ensure_session(
                    conn,
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    title=ctx.question[:50],
                )
                db.touch_session(conn, ctx.session_id)
        ctx.trace.finish_span("ensure_session")
    except Exception as _trace_exc:
        ctx.trace.finish_span("ensure_session", "error", error=_trace_exc)
        raise


async def rewrite_query_step(ctx: PipelineContext) -> None:
    """Rewrite / resolve the user query for better retrieval.

    Self-contained step that loads its own lightweight history window when the
    resolver would actually benefit from it.  This removes the dependency on a
    separate ``preload_history_for_rewrite`` call, allowing this step to run
    in parallel with ``ensure_session``.
    """
    from ...core.config import settings

    # Lightweight gate: skip entirely when resolver is disabled or query is trivial.
    resolver_would_run = getattr(settings, "RESOLVER_ENABLED", True)
    if resolver_would_run:
        from ..rag._query import _is_simple_query

        if _is_simple_query(ctx.question):
            resolver_would_run = False

    if resolver_would_run:
        # Load a lightweight history window for the resolver.
        # Reuse preloaded history if available to avoid a duplicate DB read.
        resolver_history: list[BaseMessage] = []
        if ctx.session_id:
            ctx.trace.start_span("preload_history_for_rewrite", "history")
            try:
                if ctx.raw_history_messages is not None:
                    recent = ctx.raw_history_messages[-MAX_RESOLVER_HISTORY_MESSAGES:]
                else:
                    from ..memory import get_session_history

                    history = await asyncio.to_thread(get_session_history, ctx.session_id)
                    recent = history.messages[-MAX_RESOLVER_HISTORY_MESSAGES:]
                capped_tokens = min(settings.SESSION_MAX_TOKENS, MAX_HISTORY_TOKENS)
                resolver_history = sanitize_history_messages(recent, capped_tokens)
                ctx.trace.finish_span("preload_history_for_rewrite")
            except Exception as _trace_exc:
                ctx.trace.finish_span("preload_history_for_rewrite", "error", error=_trace_exc)
                from ._common import logger

                logger.warning(
                    "Resolver history preload failed; continuing without history",
                    exc_info=True,
                )

        if resolver_history:
            from ._resolver import resolve_query

            ctx.trace.start_span("resolver", "resolve")
            try:
                result = await resolve_query(
                    ctx.question,
                    resolver_history,
                    session_id=ctx.session_id,
                    llm=ctx.llm,
                )
                # If resolver returned input unchanged and legacy rewrite is enabled,
                # still run legacy rewrite for its expansion/abbreviation benefits.
                if result == ctx.question and settings.QUERY_REWRITE_ENABLED:
                    from ..rag import rewrite_query as _do_rewrite

                    result = await _do_rewrite(result)
                ctx.rewritten_query = result
                is_skipped = result == ctx.question
                if is_skipped:
                    for _span in reversed(ctx.trace.spans):
                        if _span.label == "resolver" and _span.end_time is None:
                            _span.skipped = True
                            break
                ctx.trace.finish_span("resolver")
            except Exception as _trace_exc:
                ctx.trace.finish_span("resolver", "error", error=_trace_exc)
                from ._common import logger

                logger.warning("Resolver failed, using original", exc_info=True)
                return

            if ctx.rewritten_query:
                return

    # Legacy path: original rewrite_query (for MCP, scripts, non-chat callers)
    if not settings.QUERY_REWRITE_ENABLED:
        return

    from ..rag import rewrite_query as _do_rewrite

    ctx.trace.start_span("rewrite_query", "retrieve")
    try:
        result = await _do_rewrite(ctx.question)
        ctx.rewritten_query = result
        ctx.trace.finish_span("rewrite_query")
    except Exception as _trace_exc:
        ctx.trace.finish_span("rewrite_query", "error", error=_trace_exc)
        from ._common import logger

        logger.warning("Query rewrite failed, using original", exc_info=True)


async def preload_history_for_rewrite(ctx: PipelineContext) -> None:
    """Load a lightweight recent history window for resolver-only query rewrite.

    This prefetch avoids the heavier summarization path in ``load_history`` while
    still giving the resolver enough nearby turns for pronoun/coreference handling.
    """
    from ...core.config import settings
    from ..memory import get_session_history

    if not ctx.session_id:
        return

    ctx.trace.start_span("preload_history_for_rewrite", "history")
    try:
        history = await asyncio.to_thread(get_session_history, ctx.session_id)
        recent = history.messages[-MAX_RESOLVER_HISTORY_MESSAGES:]
        capped_tokens = min(settings.SESSION_MAX_TOKENS, MAX_HISTORY_TOKENS)
        ctx.history_messages = sanitize_history_messages(recent, capped_tokens)
        ctx.trace.finish_span("preload_history_for_rewrite")
    except Exception as _trace_exc:
        ctx.trace.finish_span("preload_history_for_rewrite", "error", error=_trace_exc)
        from ._common import logger

        logger.warning("Resolver history preload failed; continuing without history", exc_info=True)
