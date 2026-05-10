"""LLM response JSON parsing utilities."""

import json
import re
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Streaming thinking filter (stateful, per-stream instance)
# ---------------------------------------------------------------------------

# Prefixes that reasoning models commonly emit before the real answer.
# Only matched at the very start of the stream (before any substantive content).
_REASONING_PATTERNS = [
    re.compile(
        r"^(?:okay|wait|hmm|let\s+me\s+think|so|alright|well|first|let's)"
        r"[\s,.!?-]+",
        re.IGNORECASE,
    ),
]

# Phrases the model emits while self-checking the prompt instructions.
# Distinct from short interjections (handled by ``_REASONING_PATTERNS``):
# these are full self-correction sentences that we want to drop entirely
# up to the next sentence terminator.
_META_SENTENCE_PATTERNS = [
    re.compile(
        r"^(?:i(?:'| )ll\s+check|let\s+me\s+(?:check|verify|review)|"
        r"the\s+instruction\b|"
        r"based\s+on\s+the\s+instruction|"
        r"i\s+(?:should|need\s+to)\s+(?:check|follow|use|cite))",
        re.IGNORECASE,
    ),
]
_MAX_META_BUFFER_CHARS = 600


class StreamingThinkingFilter:
    """Incremental state machine that suppresses thinking/meta tokens during streaming.

    The filter has three states:
      INIT      — no content emitted yet; scanning for thinking blocks.
      THINKING  — inside a thinking block (`.THINKING_OPENERS` detected).
      PASSTHROUGH — real content detected; all subsequent tokens pass through.

    Usage::

        f = StreamingThinkingFilter()
        for token in stream:
            clean = f.feed(token)
            if clean is not None:
                emit(clean)
    """

    _THINKING_OPENERS: ClassVar[list[str]] = [
        "<think>",
        "<thinking>",
        "### Thinking:",
        "### Reasoning:",
    ]
    _THINKING_CLOSERS: ClassVar[list[str]] = [
        "</think>",
        "</thinking>",
        "### Response:",
        "### Answer:",
    ]

    def __init__(self) -> None:
        self._buffer = ""
        self._in_thinking = False
        self._content_started = False
        self._total_passed = 0

    def feed(self, token: str) -> str | None:
        """Process a streaming token.

        Returns the cleaned text to emit, or None if the token should be
        suppressed.
        """
        if self._content_started:
            # Already in pass-through mode — just emit everything.
            self._total_passed += len(token)
            return token

        self._buffer += token

        # Check for thinking block transitions
        if self._in_thinking:
            for closer in self._THINKING_CLOSERS:
                if closer in self._buffer:
                    # Strip the thinking block from buffer
                    idx = self._buffer.index(closer) + len(closer)
                    self._buffer = self._buffer[idx:]
                    self._in_thinking = False
                    break
            if self._in_thinking:
                return None  # Still inside thinking block

        # Check for thinking block openers
        for opener in self._THINKING_OPENERS:
            if opener in self._buffer:
                # If there's content before the opener, emit it
                before = self._buffer[: self._buffer.index(opener)]
                self._buffer = self._buffer[self._buffer.index(opener) + len(opener) :]
                self._in_thinking = True
                if before.strip():
                    self._content_started = True
                    self._total_passed += len(before)
                    return before
                return None

        # Check for reasoning model pre-answer chatter (only in INIT state)
        if not self._in_thinking:
            stripped = self._buffer.strip()
            if not stripped:
                return None

            # Suppress contiguous meta-commentary sentences: drop each completed
            # sentence that starts with a meta marker, then re-evaluate whatever
            # remains in the buffer.  Real content is detected when a sentence
            # *does not* start with meta and contains substantive characters.
            self._buffer = self._drop_meta_sentences(self._buffer)
            stripped = self._buffer.strip()
            if not stripped:
                return None

            # Safety valve: if we have buffered too much without finding real
            # content, flush what we have so the user is not left waiting.
            if len(self._buffer) >= _MAX_META_BUFFER_CHARS:
                self._content_started = True
                self._total_passed += len(self._buffer)
                text = self._buffer
                self._buffer = ""
                return text

            # If the head of the buffer still matches a meta pattern, the
            # current sentence is incomplete — wait for more tokens.
            if any(p.match(stripped) for p in _META_SENTENCE_PATTERNS):
                return None
            if any(p.match(stripped) for p in _REASONING_PATTERNS):
                return None

            # Real content — flush buffer and enter pass-through.
            self._content_started = True
            self._total_passed += len(self._buffer)
            text = self._buffer
            self._buffer = ""
            return text

        return None

    @staticmethod
    def _drop_meta_sentences(buf: str) -> str:
        """Strip leading meta-commentary from the buffer.

        - Short reasoning interjections (Okay/Wait/Hmm/...) drop only the matched
          prefix so substantive content right after is preserved.
        - Self-correction phrases (``I'll check the instruction``, ``let me
          verify``, ...) drop the whole sentence up to the next ``.!?\\n``.

        The loop repeats until the head of the buffer is no longer meta.
        """
        while True:
            stripped_left = buf.lstrip()
            if not stripped_left:
                return ""

            # Self-correction sentences: drop until end-of-sentence.
            phrase_match = None
            for p in _META_SENTENCE_PATTERNS:
                m = p.match(stripped_left)
                if m:
                    phrase_match = m
                    break
            if phrase_match is not None:
                sentence_end = -1
                for idx, ch in enumerate(stripped_left):
                    if ch in ".!?\n":
                        sentence_end = idx
                        break
                if sentence_end < 0:
                    return buf  # wait for more tokens to complete the sentence
                buf = stripped_left[sentence_end + 1 :]
                continue

            # Short reasoning interjections: drop only the matched prefix.
            prefix_match = None
            for p in _REASONING_PATTERNS:
                m = p.match(stripped_left)
                if m:
                    prefix_match = m
                    break
            if prefix_match is not None:
                buf = stripped_left[prefix_match.end() :]
                continue

            # No more leading meta — return the (lstripped) remainder.
            return stripped_left

    def flush(self) -> str | None:
        """Return any remaining buffered content."""
        if self._buffer:
            text = self._buffer
            self._buffer = ""
            return text
        return None

    @property
    def content_started(self) -> bool:
        return self._content_started


def strip_thinking_blocks(text: str) -> str:
    """Remove reasoning/thinking blocks from LLM output.

    Handles formats produced by reasoning models (e.g. DeepSeek-R1, QwQ):
    - <think>...</think>
    - <thinking>...</thinking>
    - ### Thinking: ... ### Response:
    """
    # 1. Strip XML-style thinking tags
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", cleaned, flags=re.DOTALL)

    # 2. Strip markdown-style thinking sections
    cleaned = re.sub(r"###\s*Thinking:.*?###\s*Response:", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"###\s*Reasoning:.*?###\s*Answer:", "", cleaned, flags=re.DOTALL)

    # 3. Collapse excessive blank lines left behind
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned.strip())
    return cleaned.strip()


def parse_llm_json(raw: str) -> Any:
    """Parse JSON from an LLM response, handling common formatting quirks.

    Handles:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Wrapper text around JSON
    - Both object ({}) and array ([]) top-level forms

    Returns:
        Parsed JSON (dict or list).

    Raises:
        json.JSONDecodeError: If no valid JSON found.
    """
    text = raw.strip()

    # 1. Extract from markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 2. Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Find first JSON object or array in the text
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    raise json.JSONDecodeError("No JSON found in LLM response", raw, 0)
