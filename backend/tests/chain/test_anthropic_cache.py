"""Tests for Anthropic prompt cache control (P2-6)."""

from langchain_core.messages import HumanMessage, SystemMessage

from src.services.chain._anthropic_cache import apply_anthropic_cache_control

_LONG_SYSTEM = "You are a professional meeting assistant. " * 30  # ~1200 chars


def test_noop_when_not_anthropic(monkeypatch):
    monkeypatch.setattr("src.services.chain._anthropic_cache.settings.LLM_BINDING", "openai")
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_ENABLED", True
    )
    messages = [SystemMessage(content=_LONG_SYSTEM), HumanMessage(content="q")]
    out = apply_anthropic_cache_control(messages)
    assert out is messages  # unchanged


def test_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("src.services.chain._anthropic_cache.settings.LLM_BINDING", "anthropic")
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_ENABLED", False
    )
    messages = [SystemMessage(content=_LONG_SYSTEM), HumanMessage(content="q")]
    out = apply_anthropic_cache_control(messages)
    assert out is messages


def test_wraps_system_for_anthropic(monkeypatch):
    monkeypatch.setattr("src.services.chain._anthropic_cache.settings.LLM_BINDING", "anthropic")
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_ENABLED", True
    )
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_MIN_CHARS", 500
    )
    messages = [SystemMessage(content=_LONG_SYSTEM), HumanMessage(content="q")]
    out = apply_anthropic_cache_control(messages)

    assert isinstance(out, list)
    system = out[0]
    assert isinstance(system.content, list)
    assert system.content[0]["type"] == "text"
    assert system.content[0]["cache_control"] == {"type": "ephemeral"}
    # Human message untouched
    assert isinstance(out[1].content, str)


def test_skips_short_system_message(monkeypatch):
    monkeypatch.setattr("src.services.chain._anthropic_cache.settings.LLM_BINDING", "anthropic")
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_ENABLED", True
    )
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_MIN_CHARS", 10_000
    )
    messages = [SystemMessage(content="short"), HumanMessage(content="q")]
    out = apply_anthropic_cache_control(messages)
    # Short system stayed as plain string (below threshold, no transformation)
    assert out[0].content == "short"


def test_preserves_chat_prompt_value_shape(monkeypatch):
    """Accept a ChatPromptValue-like object with .messages attribute."""
    monkeypatch.setattr("src.services.chain._anthropic_cache.settings.LLM_BINDING", "anthropic")
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_ENABLED", True
    )
    monkeypatch.setattr(
        "src.services.chain._anthropic_cache.settings.ANTHROPIC_PROMPT_CACHE_MIN_CHARS", 500
    )

    class _PromptValue:
        def __init__(self, msgs):
            self.messages = list(msgs)

    pv = _PromptValue([SystemMessage(content=_LONG_SYSTEM), HumanMessage(content="q")])
    out = apply_anthropic_cache_control(pv)
    assert out is pv
    assert isinstance(pv.messages[0].content, list)
    assert pv.messages[0].content[0]["cache_control"] == {"type": "ephemeral"}
