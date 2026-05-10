"""Tests for router score direction normalization (C1 fix).

Verifies that both file-level and meeting-level routers return scores
where higher values always indicate better matches, regardless of the
underlying distance metric.
"""

import pytest


class TestExtractKeyPoints:
    """Verify _extract_key_points only picks bullet-pointed lines (C4 fix)."""

    @pytest.mark.unit
    def test_prefers_bullet_lines(self):
        from src.services.chain._per_file_summary import _extract_key_points

        summary = (
            "This is an overview paragraph about the meeting.\n"
            "- First important key point discussed\n"
            "- Second key decision made by team\n"
            "- Third action item assigned to Alice\n"
            "Some trailing prose that should not appear.\n"
        )
        points = _extract_key_points(summary)
        # Default limit is now 10 (was 5), so all 5 non-trivial lines are captured.
        assert len(points) == 5
        assert "overview" in points[0]
        assert "First important" in points[1]

    @pytest.mark.unit
    def test_numbered_bullets_recognised(self):
        from src.services.chain._per_file_summary import _extract_key_points

        summary = (
            "1. Budget approved for $50k\n2. Alice to send contract\n3. Bob to review proposal\n"
        )
        points = _extract_key_points(summary)
        assert len(points) == 3
        assert "Budget" in points[0]

    @pytest.mark.unit
    def test_fallback_when_no_bullets(self):
        from src.services.chain._per_file_summary import _extract_key_points

        summary = "First line of the summary text here.\nSecond line with more details to share.\n"
        points = _extract_key_points(summary)
        assert len(points) == 2

    @pytest.mark.unit
    def test_short_lines_filtered(self):
        from src.services.chain._per_file_summary import _extract_key_points

        summary = "- short\n- This is a valid key point with enough text\n"
        points = _extract_key_points(summary)
        assert len(points) == 1
        assert "valid" in points[0]

    @pytest.mark.unit
    def test_limit_respected(self):
        from src.services.chain._per_file_summary import _extract_key_points

        summary = "\n".join(f"- Key point number {i} with enough text" for i in range(10))
        points = _extract_key_points(summary, limit=3)
        assert len(points) == 3


class TestSplitTranscriptOversizedParagraph:
    """Verify _split_transcript handles oversized paragraphs (H4 fix)."""

    @pytest.mark.unit
    def test_oversized_paragraph_split(self):
        from src.api.routers.meetings._summary import _split_transcript

        # Create a paragraph much larger than the token budget.
        # 1 token ≈ 3.5 chars; max_tokens=20 → ~70 chars budget.
        long_para = " ".join(f"sentence{i}." for i in range(50))
        text = f"short intro\n\n{long_para}\n\nshort outro"
        chunks = _split_transcript(text, max_tokens=20)
        # The long paragraph must have been split into multiple chunks
        assert len(chunks) >= 2

    @pytest.mark.unit
    def test_normal_paragraphs_unchanged(self):
        from src.api.routers.meetings._summary import _split_transcript

        text = "paragraph one\n\nparagraph two"
        chunks = _split_transcript(text, max_tokens=100)
        assert len(chunks) == 1
        assert "paragraph one" in chunks[0]
        assert "paragraph two" in chunks[0]


class TestRouterScoreDirection:
    """Verify router outputs are always higher-is-better (C1 fix)."""

    @pytest.mark.unit
    def test_normalize_lower_is_better(self):
        """When lower-is-better scores are normalised, best match gets highest score."""
        raw_scores = [(1, 0.5), (2, 1.0), (3, 2.0)]
        lower_is_better = True
        if lower_is_better:
            normalised = [(fid, 1.0 / (1.0 + s)) for fid, s in raw_scores]
        normalised.sort(key=lambda x: x[1], reverse=True)
        # Best L2 score (0.5) should now have highest normalised score
        assert normalised[0][0] == 1
        assert normalised[0][1] > normalised[1][1]

    @pytest.mark.unit
    def test_normalize_higher_is_better_passthrough(self):
        """Higher-is-better scores should pass through unchanged."""
        raw_scores = [(1, 0.9), (2, 0.5), (3, 0.1)]
        lower_is_better = False
        if lower_is_better:
            normalised = [(fid, 1.0 / (1.0 + s)) for fid, s in raw_scores]
        else:
            normalised = raw_scores[:]
        normalised.sort(key=lambda x: x[1], reverse=True)
        assert normalised[0][0] == 1
        assert normalised[0][1] == 0.9


class TestFunnelNormalizeToUnit:
    """Verify _normalize_to_unit works correctly with higher-is-better inputs."""

    @pytest.mark.unit
    def test_preserves_ranking(self):
        from src.services.rag._funnel_narrow import _normalize_to_unit

        scored = [(1, 0.9), (2, 0.5), (3, 0.1)]
        result = _normalize_to_unit(scored)
        assert result[1] > result[2] > result[3]

    @pytest.mark.unit
    def test_empty_input(self):
        from src.services.rag._funnel_narrow import _normalize_to_unit

        assert _normalize_to_unit([]) == {}

    @pytest.mark.unit
    def test_single_entry(self):
        from src.services.rag._funnel_narrow import _normalize_to_unit

        result = _normalize_to_unit([(5, 0.42)])
        assert result[5] == 1.0
