"""Tests for combined vision extraction (P1-2)."""

import json

import pytest

from src.services.vision import _captioner as cap


@pytest.fixture(autouse=True)
def _stabilise_vision_settings(monkeypatch):
    """Some sibling tests replace settings fields with MagicMocks — reset what
    the combined-vision code path reads so these tests are order-independent."""
    monkeypatch.setattr(cap.settings, "VISION_CAPTION_MIN_CHARS", 3, raising=False)
    monkeypatch.setattr(cap.settings, "VISION_OCR_MIN_CHARS", 3, raising=False)
    monkeypatch.setattr(cap.settings, "VISION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(cap.settings, "VISION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)


def test_parse_combined_vision_json_all_fields():
    raw = json.dumps(
        {
            "caption": "A diagram showing network topology",
            "ocr": "Server A -> Server B",
            "semantics": "Two servers connected by an arrow in a network diagram",
        }
    )
    result = cap._parse_combined_vision_json(raw)
    assert result is not None
    assert result.caption
    assert result.ocr_text
    assert result.semantics


def test_parse_combined_vision_json_strips_fences():
    raw = (
        "```json\n"
        + json.dumps({"caption": "A photo of a whiteboard", "ocr": "", "semantics": ""})
        + "\n```"
    )
    result = cap._parse_combined_vision_json(raw)
    assert result is not None
    # Caption long enough; OCR/semantics empty -> None after gating
    assert result.caption
    assert result.ocr_text is None
    assert result.semantics is None


def test_parse_combined_vision_json_returns_none_on_bad_json():
    assert cap._parse_combined_vision_json("nope") is None
    assert cap._parse_combined_vision_json("") is None
    assert cap._parse_combined_vision_json(None) is None  # type: ignore[arg-type]


def test_parse_combined_vision_json_filters_noise():
    """Low-information fields are gated to None."""
    raw = json.dumps({"caption": "n/a", "ocr": "x", "semantics": "unreadable"})
    result = cap._parse_combined_vision_json(raw)
    assert result is not None
    assert result.caption is None
    assert result.ocr_text is None
    assert result.semantics is None


@pytest.mark.anyio
async def test_extract_image_content_disabled_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.setattr(cap.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(cap.settings, "VISION_COMBINED_EXTRACTION_ENABLED", False)
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert await cap.extract_image_content(img) is None


@pytest.mark.anyio
async def test_extract_image_content_disabled_when_multimodal_off(monkeypatch, tmp_path):
    monkeypatch.setattr(cap.settings, "MULTIMODAL_CAPTIONING_ENABLED", False)
    monkeypatch.setattr(cap.settings, "VISION_COMBINED_EXTRACTION_ENABLED", True)
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert await cap.extract_image_content(img) is None


@pytest.mark.anyio
async def test_extract_image_content_makes_single_call(monkeypatch, tmp_path):
    """Verify the combined path is a single POST — the main optimization."""
    monkeypatch.setattr(cap.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(cap.settings, "VISION_COMBINED_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(cap.settings, "VISION_BASE_URL", "http://vision.test")
    monkeypatch.setattr(
        cap.settings, "VISION_API_KEY", type("K", (), {"get_secret_value": lambda self: "key"})()
    )
    monkeypatch.setattr(cap.settings, "VISION_MODEL", "vlm-test")
    monkeypatch.setattr(cap.settings, "VISION_RETRY_MAX_ATTEMPTS", 1)

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    post_calls: list = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "caption": "A whiteboard sketch of the user login flow",
                                    "ocr": "Login -> Session -> Dashboard",
                                    "semantics": "A sequence diagram showing three stages",
                                }
                            )
                        }
                    }
                ]
            }

    class _Client:
        async def post(self, url, json=None, headers=None):
            post_calls.append((url, json))
            return _Resp()

    monkeypatch.setattr(cap, "get_vision_client", lambda: _Client())

    result = await cap.extract_image_content(img)
    assert result is not None
    assert result.caption is not None
    assert result.ocr_text is not None
    assert result.semantics is not None
    assert len(post_calls) == 1  # single VLM call, not three
