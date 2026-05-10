"""Tests for LLM-based caption/OCR deduplication."""

import pytest


@pytest.mark.asyncio
async def test_dedupe_returns_original_when_disabled(monkeypatch):
    from src.services.vision import _dedupe as dedupe_module

    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED", False)
    monkeypatch.setattr(
        dedupe_module.llm_service,
        "get_llm",
        lambda: (_ for _ in ()).throw(AssertionError("no llm")),
    )

    caption, ocr = await dedupe_module.deduplicate_caption_ocr("A chart", "Revenue Q1: 100")
    assert caption == "A chart"
    assert ocr == "Revenue Q1: 100"


@pytest.mark.asyncio
async def test_dedupe_keeps_caption_when_llm_marks_duplicate(monkeypatch):
    from src.services.vision import _dedupe as dedupe_module

    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED", True)
    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS", 2.0)

    class _FakeLLM:
        async def ainvoke(self, prompt):
            class _Resp:
                content = '{"duplicate": true, "keep": "caption"}'

            return _Resp()

    monkeypatch.setattr(dedupe_module.llm_service, "get_llm", lambda: _FakeLLM())

    caption, ocr = await dedupe_module.deduplicate_caption_ocr(
        "A flooded rice paddy at sunset.", "A flooded rice paddy at sunset."
    )
    assert caption == "A flooded rice paddy at sunset."
    assert ocr is None


@pytest.mark.asyncio
async def test_dedupe_keeps_both_when_llm_says_not_duplicate(monkeypatch):
    from src.services.vision import _dedupe as dedupe_module

    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED", True)
    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS", 2.0)

    class _FakeLLM:
        async def ainvoke(self, prompt):
            class _Resp:
                content = '{"duplicate": false, "keep": "both"}'

            return _Resp()

    monkeypatch.setattr(dedupe_module.llm_service, "get_llm", lambda: _FakeLLM())

    caption, ocr = await dedupe_module.deduplicate_caption_ocr(
        "A whiteboard in a meeting room.", "Q4 OKR\n- Reduce infra cost by 15%"
    )
    assert caption == "A whiteboard in a meeting room."
    assert ocr == "Q4 OKR\n- Reduce infra cost by 15%"


@pytest.mark.asyncio
async def test_dedupe_falls_back_to_original_on_llm_error(monkeypatch):
    from src.services.vision import _dedupe as dedupe_module

    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED", True)
    monkeypatch.setattr(dedupe_module.settings, "MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS", 2.0)

    class _FakeLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("upstream failure")

    monkeypatch.setattr(dedupe_module.llm_service, "get_llm", lambda: _FakeLLM())

    caption, ocr = await dedupe_module.deduplicate_caption_ocr(
        "A dashboard screenshot.", "Revenue: 1.2M"
    )
    assert caption == "A dashboard screenshot."
    assert ocr == "Revenue: 1.2M"
