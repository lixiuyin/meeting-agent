"""Provider-specific generation settings."""

from unittest.mock import patch

from src.core.config import settings
from src.services.llm._providers import _create_openai_compatible_llm, create_llm


def test_openrouter_bounds_and_excludes_reasoning(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BINDING", "openrouter")
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "low")

    with patch("langchain_openai.ChatOpenAI") as chat_openai:
        _create_openai_compatible_llm("https://openrouter.ai/api/v1")

    kwargs = chat_openai.call_args.kwargs
    assert kwargs["extra_body"] == {
        "reasoning": {"effort": "low", "exclude": True},
    }


def test_create_llm_model_override_does_not_mutate_settings(monkeypatch):
    from src.services.llm import _providers

    configured_model = settings.LLM_MODEL
    calls = []
    monkeypatch.setattr(settings, "LLM_BINDING", "test-provider")
    monkeypatch.setitem(
        _providers._LLM_CREATORS,
        "test-provider",
        lambda model_name=None: calls.append(model_name) or object(),
    )

    created = create_llm("independent-judge")

    assert created is not None
    assert calls == ["independent-judge"]
    assert configured_model == settings.LLM_MODEL
