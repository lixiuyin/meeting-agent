"""Chunking strategies: structure-aware semantic splitting."""

import logging
import re as _re

logger = logging.getLogger(__name__)

# Patterns that indicate topic boundaries in meeting transcripts.
# M-16: CJK heading and title patterns included so Chinese/Japanese/Korean
# documents split at natural section boundaries.
_TOPIC_BREAK_PATTERNS = _re.compile(
    r"|".join(
        [
            r"^#{1,4}\s+",  # Markdown headings: # Heading, ## Subheading
            r"^\d+[\.\)]\s+",  # Numbered lists: 1. Topic, 2) Topic
            r"^[-*]\s+[A-Z]",  # Bullet points starting with capital letter
            r"^-{3,}$",  # Horizontal rules: ---
            r"^={3,}$",  # Horizontal rules: ===
            r"^Speaker\s+\d",  # Speaker labels: Speaker 1, Speaker 2
            # CJK title patterns (M-16):
            r"^第[一二三四五六七八九十百千\d]+[章节篇部分]",  # 第一章, 第3节
            r"^[（(][一二三四五六七八九十百千\d]+[）)]",  # noqa: RUF001
            r"^[一二三四五六七八九十]、",  # 一、二、三、
            r"^###\s",  # ### Title (CJK-friendly markdown)
            r"^【[^】]+】",  # 【Section Title】
        ]
    ),
    _re.MULTILINE,
)


def _split_by_structure(
    text: str,
    max_chunk_size: int,
    *,
    heading_positions: list[int] | None = None,
) -> list[str]:
    """Split text into chunks at topic boundaries (HIGH-4: structure-aware).

    Uses structural cues (headings, speaker changes, numbered items) plus
    optional ``heading_positions`` from ``DocumentProfile`` to find natural
    split points.
    """
    lines = text.split("\n")
    segments: list[str] = []
    current: list[str] = []

    for line in lines:
        is_break = bool(_TOPIC_BREAK_PATTERNS.match(line))

        if is_break and current:
            segment = "\n".join(current)
            segments.append(segment)
            current = [line]
        else:
            current.append(line)

    if current:
        segments.append("\n".join(current))

    # Merge small segments up to max_chunk_size
    merged: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    for seg in segments:
        seg_len = len(seg)
        if buffer and buffer_len + seg_len + 1 > max_chunk_size:
            merged.append("\n".join(buffer))
            buffer = [seg]
            buffer_len = seg_len
        else:
            buffer.append(seg)
            buffer_len += seg_len + 1

    if buffer:
        merged.append("\n".join(buffer))

    return merged if merged else [text]
