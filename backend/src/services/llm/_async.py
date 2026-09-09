"""One bounded path for asynchronous LLM text invocation."""

from __future__ import annotations

import asyncio
from typing import Any

from ...core.config import settings
from ...core.exceptions import LLMEmptyResponseError, LLMTimeoutError
from ..traffic_control import get_traffic_controller
from ._parsing import extract_visible_text


async def invoke_llm_text(llm: Any, prompt: Any) -> str:
    """Invoke an LLM under shared traffic control and require visible text."""

    async def _invoke() -> str:
        try:
            response = await asyncio.wait_for(
                llm.ainvoke(prompt), timeout=settings.LLM_GENERATION_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise LLMTimeoutError(
                "Generation deadline exhausted",
                timeout=settings.LLM_GENERATION_TIMEOUT_S,
                provider=settings.LLM_BINDING,
            ) from exc
        text = extract_visible_text(response)
        if not text:
            raise LLMEmptyResponseError(
                "Provider completed without user-visible content",
                provider=settings.LLM_BINDING,
            )
        return text

    controller = get_traffic_controller()
    if controller is None:
        return await _invoke()
    async with controller:
        text = await _invoke()
        controller.record_success()
        return text
