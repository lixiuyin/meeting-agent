"""Token counting utilities using tiktoken (lazy-loaded, cached per model)."""

import asyncio
import logging
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

logger = logging.getLogger(__name__)

# Cache of tiktoken encoders per model name
_encoders: dict[str, Any] = {}


def get_encoder(model: str) -> Any:
    """Get or create a cached tiktoken encoder for the given model."""
    if model not in _encoders:
        try:
            import tiktoken

            _encoders[model] = tiktoken.encoding_for_model(model)
        except (ImportError, KeyError):
            # Fallback to cl100k_base for unknown models
            import tiktoken

            _encoders[model] = tiktoken.get_encoding("cl100k_base")
            logger.debug("Using cl100k_base encoding for model %s", model)
    return _encoders[model]


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count the number of tokens in a text string."""
    return len(get_encoder(model).encode(text))


def count_messages_tokens(messages: list[Any], model: str = "gpt-4o-mini") -> int:
    """Count the total tokens in a list of LangChain BaseMessage objects.

    Includes per-message overhead (approximately 4 tokens per message
    for role/content framing in most GPT models).
    """
    encoder = get_encoder(model)
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else str(msg)
        total += 4  # per-message overhead (approximate)
        total += len(encoder.encode(str(content)))
    return total


def truncate_messages(
    messages: list[Any],
    max_tokens: int,
    model: str = "gpt-4o-mini",
) -> list[Any]:
    """Remove oldest messages until the total token count is under max_tokens.

    Keeps the newest messages (most recent conversation context).
    Returns a new list; does not modify the original.
    """
    if not messages or max_tokens <= 0:
        return list(messages)

    result = list(messages)
    while count_messages_tokens(result, model) > max_tokens and len(result) > 1:
        result = result[1:]  # Remove oldest
    return result


# ─── Sliding window history summarization ──────────────────────────────────────

# Minimum number of recent messages to keep verbatim
_MIN_RECENT_MESSAGES = 4

# Target token budget for the summary (compact)
_SUMMARY_TARGET_TOKENS = 300

_SUMMARY_PROMPT = """
Summarize the following conversation in 2-3 concise sentences,
preserving key facts, decisions, and any named entities.
Write in the same language as the conversation.

Conversation:
{conversation}
"""


async def summarize_messages(
    messages: list[BaseMessage],
    model: str = "gpt-4o-mini",
) -> str | None:
    """Summarize a list of messages using the LLM.

    Returns the summary text, or None if summarization fails.
    Uses the configured LLM via the service layer.
    """
    if not messages:
        return None

    try:
        from .llm import cached_retry_invoke, get_llm

        conversation_parts = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            conversation_parts.append(f"{role}: {content}")

        conversation = "\n".join(conversation_parts)
        prompt = _SUMMARY_PROMPT.format(conversation=conversation)

        llm = get_llm()
        response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
        content = response.content
        if isinstance(content, list):
            return None
        return content.strip()
    except Exception:
        logger.warning("Failed to summarize messages", exc_info=True)
        return None


def truncate_with_summary(
    messages: list[BaseMessage],
    max_tokens: int,
    model: str = "gpt-4o-mini",
    summary_text: str | None = None,
) -> list[BaseMessage]:
    """Truncate messages with an optional summary prefix for older context.

    Strategy:
    1. Keep the most recent N messages verbatim (N = _MIN_RECENT_MESSAGES)
    2. If a summary is provided, prepend it as a SystemMessage
    3. If no summary, fall back to simple truncation

    Returns a new list; does not modify the original.
    """
    if not messages or max_tokens <= 0:
        return list(messages)

    # Always keep recent messages
    if len(messages) > _MIN_RECENT_MESSAGES:
        recent = messages[-_MIN_RECENT_MESSAGES:]
    else:
        recent = list(messages)

    result: list[BaseMessage] = []
    if summary_text:
        summary_msg = SystemMessage(content=f"[Earlier conversation summary]\n{summary_text}")
        result.append(summary_msg)
    result.extend(recent)

    # Ensure we're still within budget (trim from front if needed)
    while count_messages_tokens(result, model) > max_tokens and len(result) > 1:
        result = result[1:]

    return result
