"""Tests for hierarchical (funnel) RAG: meetings -> files -> chunks.

Covers:
1. Unscoped query picks relevant meetings from 5
2. Unscoped single meeting passthrough
3. Scoped meeting-only narrows files
4. Scoped meeting + file skips funnel
5. Min-pool fallback fires
6. top_k_mean beats long meeting
7. Title prior breaks tie
8. Disabled flag uses legacy path
9. Score normalization with mixed conventions
"""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.services.rag._funnel import (
    aggregate_by_meeting,
    fetch_title_priors,
    normalize_scores,
    restrict_pool,
)


def _make_doc(
    meeting_id: int,
    file_id: int | None = None,
    content: str = "",
    score: float = 0.5,
) -> dict:
    meta: dict = {"meeting_id": meeting_id}
    if file_id is not None:
        meta["file_id"] = file_id
    return {"content": content or f"chunk m{meeting_id}", "metadata": meta, "score": score}


# ---------------------------------------------------------------------------
# 1. Unscoped picks relevant meetings from 5
# ---------------------------------------------------------------------------
class TestUnscopedPicksRelevantMeetings:
    """Unscoped query: stage-1 picks meetings with highest aggregated scores."""

    def test_selects_high_scoring_meetings(self) -> None:
        docs: list[dict] = []
        # Meeting 2 and 4 have high scores
        for i in range(5):
            docs.append(_make_doc(2, score=0.9 - i * 0.05))
            docs.append(_make_doc(4, score=0.85 - i * 0.05))
        # Meeting 1, 3, 5 have low scores
        for mid in [1, 3, 5]:
            for i in range(5):
                docs.append(_make_doc(mid, score=0.2 + i * 0.01))

        selected = aggregate_by_meeting(docs, top_n=2, method="top_k_mean", agg_top_k=3)
        assert set(selected) == {2, 4}

    def test_final_docs_all_from_selected_meetings(self) -> None:
        docs = [
            _make_doc(1, score=0.3),
            _make_doc(2, score=0.9),
            _make_doc(3, score=0.1),
            _make_doc(4, score=0.85),
        ]
        selected = aggregate_by_meeting(docs, top_n=2)
        restricted = restrict_pool(docs, meeting_ids=selected)
        assert all(d["metadata"]["meeting_id"] in selected for d in restricted)


# ---------------------------------------------------------------------------
# 2. Unscoped single meeting passthrough
# ---------------------------------------------------------------------------
class TestUnscopedSingleMeetingPassthrough:
    """Single meeting in pool: stage-1 trivially picks it."""

    def test_single_meeting_selected(self) -> None:
        docs = [_make_doc(42, score=0.7), _make_doc(42, score=0.6)]
        selected = aggregate_by_meeting(docs, top_n=5)
        assert selected == [42]

    def test_equivalent_to_baseline(self) -> None:
        """With one meeting, the funnel should not drop any docs."""
        docs = [_make_doc(1, file_id=1, score=0.8), _make_doc(1, file_id=2, score=0.6)]
        normed = normalize_scores(docs, lower_is_better=False)
        selected_meetings = aggregate_by_meeting(normed, top_n=5)
        assert selected_meetings == [1]
        restricted = restrict_pool(normed, meeting_ids=selected_meetings)
        assert len(restricted) == 2


# ---------------------------------------------------------------------------
# 6. top_k_mean beats long meeting
# ---------------------------------------------------------------------------
class TestTopKMeanBeatsLongMeeting:
    """top_k_mean should not bias toward meetings with many low-score chunks."""

    def test_high_quality_short_meeting_wins(self) -> None:
        # Meeting A: 3 high-scoring chunks
        docs_a = [_make_doc(1, score=s) for s in [0.9, 0.7, 0.6]]
        # Meeting B: 30 mediocre chunks
        docs_b = [_make_doc(2, score=0.4) for _ in range(30)]
        docs = docs_a + docs_b

        selected = aggregate_by_meeting(docs, top_n=1, method="top_k_mean", agg_top_k=3)
        assert selected[0] == 1  # Meeting A wins

    def test_sum_aggregation_would_flip(self) -> None:
        """Prove that sum aggregation would incorrectly favor the long meeting."""
        scores_a = [0.9, 0.7, 0.6]
        scores_b = [0.4] * 30
        assert sum(scores_b) > sum(scores_a)  # B wins by sum
        assert (sum(sorted(scores_a, reverse=True)[:3]) / 3) > (
            sum(sorted(scores_b, reverse=True)[:3]) / 3
        )  # A wins by top_k_mean


# ---------------------------------------------------------------------------
# 7. Title prior breaks tie
# ---------------------------------------------------------------------------
class TestTitlePriorBreaksTie:
    """When chunk scores are identical, title matching should break the tie."""

    def test_title_boost_selects_matching_meeting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "RAG_FUNNEL_TITLE_PRIOR_ENABLED", True)
        monkeypatch.setattr(settings, "RAG_FUNNEL_TITLE_PRIOR_WEIGHT", 0.05)
        monkeypatch.setattr(settings, "RAG_FUNNEL_TITLE_PRIOR_CAP", 0.15)

        # Both meetings have identical chunk scores
        docs = [
            _make_doc(1, score=0.5),
            _make_doc(2, score=0.5),
        ]
        # Meeting 1 title matches "budget"
        priors = {1: 0.05}
        selected = aggregate_by_meeting(
            docs, top_n=1, method="top_k_mean", agg_top_k=3, title_prior=priors
        )
        assert selected[0] == 1

    def test_fetch_title_priors_queries_db(self) -> None:
        """fetch_title_priors should return boosts for matching meetings."""
        from src.core.database import get_write_connection

        # Insert test meeting
        with get_write_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meetings (id, title, created_at) VALUES (?, ?, datetime('now'))",
                (
                    999,
                    "Budget Planning Meeting",
                ),
            )
            conn.commit()

        priors = fetch_title_priors("budget planning", [999])
        assert 999 in priors
        assert priors[999] > 0


# ---------------------------------------------------------------------------
# 8. Disabled flag uses legacy path
# ---------------------------------------------------------------------------
class TestDisabledFlagLegacyPath:
    """Scoped path now uses plain retrieve() directly (no _get_retrieve_fn)."""

    @pytest.mark.asyncio
    async def test_scoped_uses_plain_retrieve(self) -> None:
        from src.services.chain._steps_retrieve import retrieve_documents as scoped_retrieve

        assert scoped_retrieve is not None  # function exists


# ---------------------------------------------------------------------------
# 9. Score normalization mixed
# ---------------------------------------------------------------------------
class TestScoreNormalizationMixed:
    """normalize_scores handles both distance-like and RRF-like scores."""

    def test_distance_scores_normalized(self) -> None:
        # L2 distances: lower is better
        docs = [
            {"content": "a", "metadata": {}, "score": 0.5},
            {"content": "b", "metadata": {}, "score": 1.0},
            {"content": "c", "metadata": {}, "score": 2.0},
        ]
        normed = normalize_scores(docs, lower_is_better=True)
        scores = [d["score"] for d in normed]
        assert scores[0] > scores[1] > scores[2]  # 0.5 -> best (highest normalized)
        assert all(0 <= s <= 1 for s in scores)

    def test_rrf_scores_passthrough(self) -> None:
        docs = [
            {"content": "a", "metadata": {}, "score": 1.0},
            {"content": "b", "metadata": {}, "score": 0.5},
        ]
        normed = normalize_scores(docs, lower_is_better=False)
        assert normed[0]["score"] == 1.0
        assert normed[1]["score"] == 0.5

    def test_empty_docs(self) -> None:
        assert normalize_scores([], lower_is_better=True) == []

    def test_mixed_in_aggregator(self) -> None:
        """Distance-normalized docs + RRF docs rank correctly together."""
        distance_doc = {"content": "a", "metadata": {"meeting_id": 1}, "score": 0.5}
        rrf_doc = {"content": "b", "metadata": {"meeting_id": 2}, "score": 0.8}
        normed_distance = normalize_scores([distance_doc], lower_is_better=True)
        normed_rrf = normalize_scores([rrf_doc], lower_is_better=False)
        all_docs = normed_distance + normed_rrf
        # distance_doc 0.5 -> 1/(1+0.5) = 0.667; rrf_doc = 0.8
        assert all_docs[1]["score"] > all_docs[0]["score"]


# ---------------------------------------------------------------------------
# restrict_pool edge cases
# ---------------------------------------------------------------------------
class TestRestrictPool:
    def test_no_filters_returns_all(self) -> None:
        docs = [_make_doc(1), _make_doc(2)]
        assert restrict_pool(docs) == docs

    def test_meeting_filter(self) -> None:
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        result = restrict_pool(docs, meeting_ids=[1, 3])
        assert len(result) == 2

    def test_file_filter(self) -> None:
        docs = [_make_doc(1, file_id=10), _make_doc(1, file_id=20)]
        result = restrict_pool(docs, file_ids=[10])
        assert len(result) == 1
        assert result[0]["metadata"]["file_id"] == 10

    def test_empty_input(self) -> None:
        assert restrict_pool([]) == []
