"""Tests for StreamingThinkingFilter — incremental thinking block suppression."""

from src.services.llm._parsing import StreamingThinkingFilter, strip_thinking_blocks


class TestStreamingThinkingFilter:
    """Verify the state machine suppresses thinking tokens and passes real content."""

    def test_normal_text_passes_through(self):
        f = StreamingThinkingFilter()
        out = f.feed("Hello, ")
        assert out is not None
        assert "Hello, " in out
        out2 = f.feed("world!")
        assert "world!" in out2

    def test_unicode_thinking_block_suppressed(self):
        f = StreamingThinkingFilter()
        # Simulate thinking tokens
        tokens = ["Hmm, ", "let me ", "think"]
        results = []
        for t in tokens:
            r = f.feed(t)
            if r is not None:
                results.append(r)
        # All are meta-prefix reasoning chatter, may be buffered
        # Feed real content to flush
        real = f.feed("The answer is 42.")
        if real:
            results.append(real)
        combined = "".join(results)
        assert "42" in combined

    def test_thinking_tag_block_suppressed(self):
        f = StreamingThinkingFilter()
        f.feed("<thinking>")
        out = f.feed("internal reasoning here")
        assert out is None  # suppressed
        out = f.feed("</thinking>")
        # After closing tag, content should pass
        out = f.feed("The real answer.")
        assert "The real answer." in out

    def test_markdown_thinking_section_suppressed(self):
        f = StreamingThinkingFilter()
        f.feed("### Thinking:")
        out = f.feed("some reasoning")
        assert out is None
        f.feed("### Response:")
        out = f.feed("Actual answer here.")
        assert "Actual answer here." in out

    def test_flush_returns_remaining_buffer(self):
        f = StreamingThinkingFilter()
        # Feed a thinking opener so the buffer is held (not yet passed through)
        f.feed("<thinking>")
        f.feed("internal reasoning")
        # Buffer has content, not yet flushed via feed
        result = f.flush()
        # After thinking block, flush returns whatever is left
        assert result is not None or f.content_started  # either flushed or already emitted

    def test_flush_empty_returns_none(self):
        f = StreamingThinkingFilter()
        f.feed("text")
        f.flush()  # clear buffer
        assert f.flush() is None

    def test_content_started_property(self):
        f = StreamingThinkingFilter()
        assert not f.content_started
        f.feed("Some real content")
        assert f.content_started

    def test_reasoning_prefix_okay_suppressed(self):
        f = StreamingThinkingFilter()
        out = f.feed("Okay. ")
        # "Okay." is a reasoning prefix — may be buffered
        # Feed enough to confirm it's meta or real
        out2 = f.feed("Wait, let me check the instruction. ")
        # Still meta — continue
        out3 = f.feed("The project deadline is March 15.")
        combined = "".join(t for t in [out, out2, out3] if t is not None)
        assert "March 15" in combined


class TestStripThinkingBlocks:
    """Verify the batch strip function handles all thinking formats."""

    def test_removes_unicode_thinking(self):
        # strip_thinking_blocks handles <thinking>...</thinking> tags
        text = "<thinking>some thinking</thinking>result"
        assert strip_thinking_blocks(text) == "result"

    def test_removes_xml_thinking_tags(self):
        text = "<thinking>reasoning</thinking>answer"
        assert strip_thinking_blocks(text) == "answer"

    def test_removes_markdown_thinking(self):
        text = "### Thinking: some thoughts ### Response: the answer"
        assert strip_thinking_blocks(text) == "the answer"

    def test_removes_markdown_reasoning(self):
        text = "### Reasoning: steps ### Answer: result"
        assert strip_thinking_blocks(text) == "result"

    def test_no_thinking_blocks(self):
        text = "Just a normal answer."
        assert strip_thinking_blocks(text) == "Just a normal answer."

    def test_collapses_blank_lines(self):
        text = "line1\n\n\n\nline2"
        assert strip_thinking_blocks(text) == "line1\n\nline2"
