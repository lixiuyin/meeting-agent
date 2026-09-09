"""Tests for vision captioner quality gating and retry behavior."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr


def test_vision_endpoint_falls_back_to_llm_credentials(monkeypatch):
    from src.services.vision import _captioner as captioner

    monkeypatch.setattr(captioner.settings, "VISION_BASE_URL", "")
    monkeypatch.setattr(captioner.settings, "LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_API_KEY", SecretStr(""))
    monkeypatch.setattr(captioner.settings, "LLM_API_KEY", SecretStr("shared-key"))

    assert captioner._vision_endpoint() == (
        "https://openrouter.ai/api/v1",
        "shared-key",
        "vision-model",
    )


def test_openrouter_vision_payload_disables_reasoning(monkeypatch):
    from src.services.vision import _captioner as captioner

    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_REASONING_EFFORT", "none")
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")

    payload = captioner._vision_payload(
        "image.png",
        base_url="https://openrouter.ai/api/v1",
        prompt="Describe the image.",
        max_tokens=2048,
    )

    assert payload["reasoning"] == {"effort": "none", "exclude": True}
    assert payload["max_tokens"] == 2048


def test_non_openrouter_vision_payload_omits_reasoning(monkeypatch):
    from src.services.vision import _captioner as captioner

    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")

    payload = captioner._vision_payload(
        "image.png",
        base_url="https://vision.example/v1",
        prompt="Describe the image.",
        max_tokens=120,
    )

    assert "reasoning" not in payload


@pytest.mark.asyncio
async def test_caption_retries_and_succeeds(monkeypatch):
    from src.services.vision import _captioner as captioner

    class _FakeClient:
        def __init__(self):
            self.calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise httpx.ConnectError("transient")
            request = httpx.Request("POST", "https://vision.example/chat/completions")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "A kanban board with sprint tasks."}}]},
                request=request,
            )

    client = _FakeClient()
    monkeypatch.setattr(captioner, "get_vision_client", lambda: client)
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")
    monkeypatch.setattr(captioner.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(captioner.settings, "VISION_BASE_URL", "https://vision.example")
    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_DELAY_SECONDS", 0.0)

    result = await captioner.caption_image("image.png")
    assert result == "A kanban board with sprint tasks."
    assert client.calls == 3


@pytest.mark.asyncio
async def test_caption_quality_gate_drops_noise(monkeypatch):
    from src.services.vision import _captioner as captioner

    class _FakeClient:
        async def post(self, *_args, **_kwargs):
            request = httpx.Request("POST", "https://vision.example/chat/completions")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "N/A"}}]},
                request=request,
            )

    monkeypatch.setattr(captioner, "get_vision_client", lambda: _FakeClient())
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")
    monkeypatch.setattr(captioner.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(captioner.settings, "VISION_BASE_URL", "https://vision.example")
    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_API_KEY", SecretStr("test-key"))

    assert await captioner.caption_image("image.png") is None


@pytest.mark.asyncio
async def test_caption_retries_on_http_5xx_then_succeeds(monkeypatch):
    from src.services.vision import _captioner as captioner

    class _FakeClient:
        def __init__(self):
            self.calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            request = httpx.Request("POST", "https://vision.example/chat/completions")
            if self.calls == 1:
                return httpx.Response(502, request=request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "System architecture diagram."}}]},
                request=request,
            )

    client = _FakeClient()
    monkeypatch.setattr(captioner, "get_vision_client", lambda: client)
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")
    monkeypatch.setattr(captioner.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(captioner.settings, "VISION_BASE_URL", "https://vision.example")
    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_DELAY_SECONDS", 0.0)

    result = await captioner.caption_image("image.png")
    assert result == "System architecture diagram."
    assert client.calls == 2


@pytest.mark.asyncio
async def test_caption_does_not_retry_on_http_4xx(monkeypatch):
    from src.services.vision import _captioner as captioner

    class _FakeClient:
        def __init__(self):
            self.calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            request = httpx.Request("POST", "https://vision.example/chat/completions")
            return httpx.Response(400, request=request)

    client = _FakeClient()
    monkeypatch.setattr(captioner, "get_vision_client", lambda: client)
    monkeypatch.setattr(captioner, "_image_to_data_url", lambda _path: "data:image/png;base64,AA==")
    monkeypatch.setattr(captioner.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(captioner.settings, "VISION_BASE_URL", "https://vision.example")
    monkeypatch.setattr(captioner.settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(captioner.settings, "VISION_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(captioner.settings, "VISION_RETRY_MAX_DELAY_SECONDS", 0.0)

    result = await captioner.caption_image("image.png")
    assert result is None
    assert client.calls == 1
