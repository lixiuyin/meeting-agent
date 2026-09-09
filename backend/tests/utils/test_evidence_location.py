from src.models.schemas.evidence import EvidenceLocationRequest
from src.services.evidence_location import evidence_identity, resolve_evidence_location


def test_unicode_offsets_and_page_mapping():
    source = "😀Intro\n\nChatGPT was released\nin 2022/11."
    timeline = {
        "kind": "pages",
        "pages": [
            {"page_num": 1, "text": "😀Intro"},
            {"page_num": 3, "text": "ChatGPT was released\nin 2022/11."},
        ],
    }
    result = resolve_evidence_location(
        source, timeline, EvidenceLocationRequest(excerpt="released in 2022/11")
    )
    assert result["status"] == "exact" and result["page"] == 3
    assert source[result["window_start"] : result["window_end"]] == "released\nin 2022/11"
    assert evidence_identity(1, 2, "v1", "p1", result) != evidence_identity(
        1, 2, "v2", "p1", result
    )


def test_ambiguity_and_bad_windows_fail_closed():
    assert (
        resolve_evidence_location(
            "same same", {"kind": "text"}, EvidenceLocationRequest(excerpt="same")
        )["status"]
        == "ambiguous"
    )
    assert (
        resolve_evidence_location(
            "text", {"kind": "text"}, EvidenceLocationRequest(window_start=2, window_end=99)
        )["status"]
        == "not_found"
    )


def test_audio_location_keeps_zero_timestamp():
    result = resolve_evidence_location(
        "Ship today",
        {"kind": "segments", "segments": [{"text": "Ship today", "start": 0, "end": 5}]},
        EvidenceLocationRequest(excerpt="Ship"),
    )
    assert result["timestamp_start"] == 0 and result["timestamp_end"] == 5
