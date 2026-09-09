"""Deterministic block identities in canonical Unicode text, never guessed PDF boxes."""

import hashlib
import re


def source_blocks(pages: list[dict], source: str) -> list[dict]:
    revision = hashlib.sha256(source.encode()).hexdigest()
    result, cursor = [], 0
    for page in pages:
        text = page.get("text") or ""
        start = source.find(text, cursor) if text else -1
        blocks = []
        if start >= 0:
            cursor = start + len(text)
            for match in re.finditer(r"\S[\s\S]*?(?=\n[ \t]*\n|\Z)", text):
                left, right = start + match.start(), start + match.start() + len(match[0].rstrip())
                block_id = hashlib.sha256(f"{revision}:{left}:{right}".encode()).hexdigest()[:24]
                blocks.append(
                    {
                        "block_id": block_id,
                        "window_start": left,
                        "window_end": right,
                        "text": source[left:right],
                        "parser_revision": "canonical-block-v1",
                    }
                )
        result.append({**page, "blocks": blocks})
    return result
