from src.core.source_blocks import source_blocks
from src.models.schemas.evidence import EvidenceLocationRequest
from src.services.evidence_location import resolve_evidence_location


def test_block_ids_are_revision_bound_and_use_exact_unicode_offsets():
    source = "😀 Intro\n\nAlice owns Atlas.\n\nRepeated\n\nRepeated"
    pages = [{"page_num": 1, "text": source}]
    mapped = source_blocks(pages, source)
    blocks = mapped[0]["blocks"]
    assert len({b["block_id"] for b in blocks}) == 4
    for block in blocks:
        assert source[block["window_start"] : block["window_end"]] == block["text"]
    timeline = {"kind": "pages", "pages": mapped}
    result = resolve_evidence_location(
        source, timeline, EvidenceLocationRequest(block_id=blocks[1]["block_id"])
    )
    assert result["status"] == "page_only" and result["block_id"] == blocks[1]["block_id"]
    assert source_blocks(pages, source) == mapped
    changed = source_blocks([{"page_num": 1, "text": source + "."}], source + ".")
    assert changed[0]["blocks"][0]["block_id"] != blocks[0]["block_id"]
