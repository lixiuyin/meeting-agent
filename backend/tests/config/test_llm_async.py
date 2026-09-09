"""Regression tests for the bounded visible-text LLM invocation path."""

from types import SimpleNamespace

import pytest

from src.core.exceptions import LLMEmptyResponseError
from src.services.llm._async import invoke_llm_text


class _FakeLlm:
    def __init__(self, content):
        self.content = content

    async def ainvoke(self, _prompt):
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_invoke_llm_text_rejects_reasoning_only_response(monkeypatch):
    monkeypatch.setattr("src.services.llm._async.get_traffic_controller", lambda: None)
    llm = _FakeLlm([{"type": "reasoning", "text": "hidden"}])

    with pytest.raises(LLMEmptyResponseError):
        await invoke_llm_text(llm, "prompt")


@pytest.mark.asyncio
async def test_invoke_llm_text_returns_visible_blocks(monkeypatch):
    monkeypatch.setattr("src.services.llm._async.get_traffic_controller", lambda: None)
    llm = _FakeLlm(
        [
            {"type": "reasoning", "text": "hidden"},
            {"type": "text", "text": "answer"},
        ]
    )

    assert await invoke_llm_text(llm, "prompt") == "answer"
