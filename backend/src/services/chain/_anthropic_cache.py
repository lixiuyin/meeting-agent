"""Anthropic prompt caching helper.

Transforms a prompt value produced by a LangChain ChatPromptTemplate so that
the system message is marked with ``cache_control: {"type": "ephemeral"}`` —
letting the Anthropic API cache its tokens for 5 minutes and avoid re-charging
on repeat requests within that window.

Only activates when:
- ``LLM_BINDING`` is "anthropic"
- ``ANTHROPIC_PROMPT_CACHE_ENABLED`` is true
- The system message text is at least ``ANTHROPIC_PROMPT_CACHE_MIN_CHARS`` chars
  (caching short prompts is pointless and counts against the 4-block budget).

This module is intentionally dependency-free apart from a lazy LangChain import
so it can be unit-tested without spinning up the whole RAG pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from ...core.config import settings

logger = logging.getLogger(__name__)


def _build_cached_system_block(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def apply_anthropic_cache_control(prompt_value: Any) -> Any:
    """Mark the SystemMessage in the given prompt value for ephemeral caching.

    Accepts either a LangChain ``ChatPromptValue``-like object (with
    ``to_messages()`` and ``messages`` attributes) or a raw list of messages.
    Returns the same shape it received; if no system message is present or the
    configuration gate is closed, the input is returned unchanged.
    """
    if not settings.ANTHROPIC_PROMPT_CACHE_ENABLED:
        return prompt_value
    if settings.LLM_BINDING != "anthropic":
        return prompt_value

    min_chars = max(0, settings.ANTHROPIC_PROMPT_CACHE_MIN_CHARS)

    # Extract a mutable list of messages regardless of input shape.
    if hasattr(prompt_value, "messages") and isinstance(prompt_value.messages, list):
        messages = list(prompt_value.messages)
        wrap_back = True
    elif isinstance(prompt_value, list):
        messages = list(prompt_value)
        wrap_back = False
    else:
        return prompt_value

    transformed = False
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)
        if msg_type not in ("system", "SystemMessage"):
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            if len(content) < min_chars:
                continue
            block = _build_cached_system_block(content)
            try:
                messages[idx] = msg.model_copy(update={"content": block})
            except AttributeError:
                try:
                    messages[idx] = type(msg)(content=block)
                except Exception:
                    logger.debug("Could not clone system message for cache_control", exc_info=True)
                    continue
            transformed = True
            break  # Anthropic charges by distinct cache_control markers; one is enough.
        elif isinstance(content, list) and content:
            # Already block-form — add cache_control to the first text block if missing.
            first_block = content[0]
            if (
                isinstance(first_block, dict)
                and first_block.get("type") == "text"
                and "cache_control" not in first_block
                and len(first_block.get("text", "")) >= min_chars
            ):
                new_blocks = [{**first_block, "cache_control": {"type": "ephemeral"}}]
                new_blocks.extend(content[1:])
                try:
                    messages[idx] = msg.model_copy(update={"content": new_blocks})
                except AttributeError:
                    continue
                transformed = True
                break

    if not transformed:
        return prompt_value

    if wrap_back:
        try:
            return prompt_value.model_copy(update={"messages": messages})  # type: ignore[union-attr]
        except AttributeError:
            try:
                prompt_value.messages = messages  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Could not reassign .messages on prompt value", exc_info=True)
                return prompt_value
            return prompt_value
    return messages
