"""Tests for funnel narrow: chunk-level file selection in broad recall (v4).

Covers:
1. Router + funnel overlap — zigzag interleave
2. Router empty, funnel picks files
3. Router and funnel disagree — zigzag order
4. Min evidence threshold filters noisy files
5. Anchor set-level append with evict
6. Anchor already in scope — no double append
7. Both router and funnel empty — returns empty
8. Target files cap respected
9. File scores priority: funnel > router_normalized > 1.0
10. Single Chroma call (no dedicated anchor fetch)
11. Anchor meeting IDs unioned into wide fetch scope (S1)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.chain._retrieve_routing import _scale_chunks_per_file
from src.services.rag._funnel_narrow import (
    _apply_evidence_threshold,
    _compute_file_scores,
    _merge_router_funnel,
    _normalize_to_unit,
    _rank_aware_union_rrf,
    _rank_aware_union_zigzag,
    _resolve_wide_k,
    narrow_scope_via_funnel,
)
from src.services.rag._scope_types import ScopeSelection


def _make_doc(
    meeting_id: int = 1,
    file_id: int = 1,
    content: str = "",
    score: float = 0.5,
) -> dict:
    meta: dict = {"meeting_id": meeting_id, "file_id": file_id}
    return {
        "content": content or f"chunk m{meeting_id} f{file_id}",
        "metadata": meta,
        "score": score,
    }


# ---------------------------------------------------------------------------
# _rank_aware_union_zigzag unit tests
# ---------------------------------------------------------------------------
class TestZigzagUnion:
    def test_intersection_first_then_zigzag(self) -> None:
        router: list[tuple[int, float]] = [(1, 0.9), (2, 0.8), (3, 0.7)]
        funnel: list[tuple[int, float]] = [(2, 0.85), (4, 0.6), (1, 0.5)]
        result = _rank_aware_union_zigzag(router, funnel, target_files=5)
        # 1 and 2 are intersection — they come first (router order)
        assert result[:2] == [1, 2]
        # Then zigzag: router-only [3], funnel-only [4]
        assert 3 in result
        assert 4 in result
        assert len(result) == 4

    def test_router_empty_funnel_picks(self) -> None:
        result = _rank_aware_union_zigzag([], [(4, 0.8), (5, 0.6)], target_files=3)
        assert result == [4, 5]

    def test_funnel_empty_router_picks(self) -> None:
        result = _rank_aware_union_zigzag([(1, 0.9), (2, 0.7)], [], target_files=5)
        assert result == [1, 2]

    def test_both_empty(self) -> None:
        assert _rank_aware_union_zigzag([], [], target_files=5) == []

    def test_none_router(self) -> None:
        assert _rank_aware_union_zigzag(None, [(1, 0.9)], target_files=5) == [1]

    def test_target_files_cap(self) -> None:
        router = [(i, 0.9 - i * 0.1) for i in range(10)]
        funnel = [(i + 5, 0.8 - i * 0.05) for i in range(10)]
        result = _rank_aware_union_zigzag(router, funnel, target_files=3)
        assert len(result) == 3

    def test_disjoint_zigzag_alternates(self) -> None:
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(3, 0.7), (4, 0.6)]
        result = _rank_aware_union_zigzag(router, funnel, target_files=5)
        assert result == [1, 3, 2, 4]


# ---------------------------------------------------------------------------
# _rank_aware_union_rrf unit tests
# ---------------------------------------------------------------------------
class TestRrfUnion:
    def test_intersection_double_weighted(self) -> None:
        # File 2 in both lists at top rank -> highest RRF score.
        router = [(1, 0.9), (2, 0.8), (3, 0.7)]
        funnel = [(2, 0.85), (4, 0.6), (1, 0.5)]
        result = _rank_aware_union_rrf(router, funnel, target_files=5)
        # File 2 contributes 1/61 + 1/61, file 1 contributes 1/61 + 1/63.
        assert result[0] == 2

    def test_funnel_high_rank_not_pushed_back(self) -> None:
        # No overlap; in zigzag the router-only [1,2] would precede funnel
        # [4,5].  With RRF, file 4 (funnel rank 0) ties with file 1 (router
        # rank 0) — both at 1/61.  Tiebreak (router rank lower wins) places
        # file 1 first, then file 4 second — funnel-only file is no longer
        # demoted to position 3 the way zigzag did.
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(4, 0.95), (5, 0.88)]
        result = _rank_aware_union_rrf(router, funnel, target_files=4)
        assert result.index(4) <= result.index(2)

    def test_router_empty(self) -> None:
        result = _rank_aware_union_rrf([], [(4, 0.8), (5, 0.6)], target_files=3)
        assert result == [4, 5]

    def test_funnel_empty(self) -> None:
        result = _rank_aware_union_rrf([(1, 0.9), (2, 0.7)], [], target_files=5)
        assert result == [1, 2]

    def test_both_empty(self) -> None:
        assert _rank_aware_union_rrf([], [], target_files=5) == []

    def test_target_files_cap(self) -> None:
        router = [(i, 0.9 - i * 0.1) for i in range(10)]
        funnel = [(i + 5, 0.8 - i * 0.05) for i in range(10)]
        result = _rank_aware_union_rrf(router, funnel, target_files=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# _merge_router_funnel dispatcher
# ---------------------------------------------------------------------------
class TestMergeStrategy:
    def test_default_rrf(self) -> None:
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(4, 0.95), (5, 0.88)]
        result = _merge_router_funnel(
            router,
            funnel,
            target_files=4,
            strategy="rrf",
        )
        # The funnel top file 4 (rank=1/61) must beat the router-only file 2
        # (rank=1/62).  In legacy zigzag this would have been [1, 4, 2, 5].
        assert result.index(4) < result.index(2)

    def test_zigzag_legacy(self) -> None:
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(3, 0.7), (4, 0.6)]
        result = _merge_router_funnel(
            router,
            funnel,
            target_files=5,
            strategy="zigzag",
        )
        # Strict alternation: router[0], funnel[0], router[1], funnel[1].
        assert result == [1, 3, 2, 4]

    def test_unknown_strategy_falls_back_to_rrf(self) -> None:
        router = [(1, 0.9)]
        funnel = [(4, 0.95)]
        result = _merge_router_funnel(
            router,
            funnel,
            target_files=3,
            strategy="not-a-strategy",
        )
        # Unknown strategy uses RRF default branch; both files appear.
        assert set(result) == {1, 4}


# ---------------------------------------------------------------------------
# _scale_chunks_per_file
# ---------------------------------------------------------------------------
class TestScaleChunksPerFile:
    def test_int_scaling(self) -> None:
        assert _scale_chunks_per_file(6, 3) == 2

    def test_int_floor_2(self) -> None:
        assert _scale_chunks_per_file(3, 5) == 2

    def test_dict_scaling(self) -> None:
        result = _scale_chunks_per_file({1: 6, 2: 9}, 3)
        assert result == {1: 2, 2: 3}

    def test_single_query_passthrough(self) -> None:
        assert _scale_chunks_per_file(6, 1) == 6
        assert _scale_chunks_per_file({1: 6}, 1) == {1: 6}


# ---------------------------------------------------------------------------
# _normalize_to_unit
# ---------------------------------------------------------------------------
class TestNormalizeToUnit:
    def test_empty(self) -> None:
        assert _normalize_to_unit([]) == {}

    def test_min_max_spread(self) -> None:
        result = _normalize_to_unit([(1, 0.0), (2, 10.0)])
        assert result == {1: 0.0, 2: 1.0}

    def test_in_unit_range(self) -> None:
        result = _normalize_to_unit([(1, 0.2), (2, 0.8)])
        assert result[1] == 0.0
        assert result[2] == 1.0

    def test_single_entry_maps_to_one(self) -> None:
        assert _normalize_to_unit([(1, 0.42)]) == {1: 1.0}

    def test_identical_values(self) -> None:
        assert _normalize_to_unit([(1, 5.0), (2, 5.0)]) == {1: 1.0, 2: 1.0}

    def test_all_zero(self) -> None:
        assert _normalize_to_unit([(1, 0.0), (2, 0.0)]) == {1: 0.0, 2: 0.0}

    def test_intermediate_values(self) -> None:
        result = _normalize_to_unit([(1, 0.0), (2, 5.0), (3, 10.0)])
        assert result[1] == 0.0
        assert abs(result[2] - 0.5) < 1e-9
        assert result[3] == 1.0


# ---------------------------------------------------------------------------
# narrow_scope_via_funnel integration tests (mocked retrieve)
# ---------------------------------------------------------------------------
class TestNarrowScopeViaFunnel:
    @pytest.fixture(autouse=True)
    def _patch_deps(self) -> None:
        with (
            patch("src.services.rag._funnel_narrow.retrieve") as mock_retrieve,
            patch(
                "src.services.rag._funnel_narrow._vector_score_lower_is_better", return_value=False
            ),
            patch("src.services.rag._funnel_narrow.settings") as mock_settings,
        ):
            self.mock_retrieve = mock_retrieve
            self.mock_settings = mock_settings
            self.mock_settings.TOP_K = 10
            self.mock_settings.RAG_FUNNEL_FETCH_MULTIPLIER = 3
            self.mock_settings.RAG_ANCHOR_ENABLED = True
            self.mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = True
            self.mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
            self.mock_settings.RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO = 0.8
            self.mock_settings.RAG_FUNNEL_AGGREGATION = "top_k_mean"
            self.mock_settings.RAG_FUNNEL_AGG_TOP_K = 3
            self.mock_settings.RAG_FUNNEL_AGGREGATION_ALPHA = 1.0
            self.mock_settings.RAG_FUNNEL_MERGE_STRATEGY = "rrf"
            self.mock_settings.RAG_FUNNEL_RRF_K = 60
            self.mock_settings.RAG_FUNNEL_FILE_PRIOR_ENABLED = False
            self.mock_settings.RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS = 0.0
            self.mock_settings.RAG_FUNNEL_EVIDENCE_MODE = "absolute"
            self.mock_settings.RAG_FUNNEL_WIDE_K_MIN = 0
            self.mock_settings.RAG_FUNNEL_WIDE_K_MAX = 0
            self.mock_settings.RAG_FUNNEL_MULTIMODAL_ENABLED = False
            self.mock_settings.RAGANYTHING_ENABLED = False
            yield

    def _set_retrieve_docs(self, docs: list[dict]) -> None:
        self.mock_retrieve.return_value = (docs, MagicMock())

    def test_returns_scope_selection_with_scores(self) -> None:
        docs = [_make_doc(file_id=1, score=0.9), _make_doc(file_id=2, score=0.7)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        assert isinstance(result, ScopeSelection)
        assert isinstance(result.scope_file_ids, list)
        assert isinstance(result.file_scores, dict)
        assert 1 in result.scope_file_ids
        assert 1 in result.file_scores

    def test_zigzag_with_router_overlap(self) -> None:
        docs = [
            _make_doc(file_id=1, score=0.9),
            _make_doc(file_id=2, score=0.85),
            _make_doc(file_id=4, score=0.7),
        ]
        self._set_retrieve_docs(docs)
        router_scope = [(1, 0.9), (2, 0.85), (3, 0.7)]
        result = narrow_scope_via_funnel(
            "test",
            router_scope,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.15,
        )
        # Intersection [1,2] first, then zigzag: router=[3], funnel=[4]
        assert result.scope_file_ids[:2] == [1, 2]
        assert 3 in result.scope_file_ids
        assert 4 in result.scope_file_ids

    def test_router_empty_funnel_picks(self) -> None:
        docs = [_make_doc(file_id=4, score=0.8), _make_doc(file_id=5, score=0.7)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            [],
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.15,
        )
        assert result.scope_file_ids == [4, 5]

    def test_min_evidence_filter(self) -> None:
        docs = [_make_doc(file_id=1, score=0.9), _make_doc(file_id=2, score=0.05)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.15,
        )
        assert 1 in result.scope_file_ids
        assert 2 not in result.scope_file_ids

    def test_anchor_appended_with_evict(self) -> None:
        docs = [_make_doc(file_id=i, score=0.8 - i * 0.05) for i in range(1, 9)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [99],
            None,
            None,
            None,
            target_files=8,
            min_chunk_evidence=0.0,
        )
        # 99 should be appended, evicting lowest-scoring non-anchor file
        assert 99 in result.scope_file_ids
        assert len(result.scope_file_ids) <= 8

    def test_anchor_already_in_scope(self) -> None:
        docs = [_make_doc(file_id=1, score=0.9), _make_doc(file_id=2, score=0.8)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [1],
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        assert result.scope_file_ids.count(1) == 1

    def test_anchor_disabled(self) -> None:
        self.mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
        docs = [_make_doc(file_id=1, score=0.9)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [99],
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        assert 99 not in result.scope_file_ids

    def test_both_empty(self) -> None:
        self._set_retrieve_docs([])
        result = narrow_scope_via_funnel(
            "test",
            [],
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.15,
        )
        assert result.scope_file_ids == []
        assert result.file_scores == {}

    def test_target_files_cap(self) -> None:
        docs = [_make_doc(file_id=i, score=0.9 - i * 0.05) for i in range(10)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=3,
            min_chunk_evidence=0.0,
        )
        assert len(result.scope_file_ids) <= 3

    def test_file_scores_priority_funnel_over_router(self) -> None:
        # Two funnel files so min-max normalisation produces a real spread.
        docs = [
            _make_doc(file_id=1, score=0.9),
            _make_doc(file_id=2, score=0.3),
        ]
        self._set_retrieve_docs(docs)
        router_scope = [(1, 5.0)]  # router score not in [0,1]
        result = narrow_scope_via_funnel(
            "test",
            router_scope,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        # File 1 is the top funnel match → normalised to 1.0 (uses funnel,
        # not router score).
        assert result.file_scores[1] == 1.0
        # File 2 (low funnel score) should be < 1.0
        assert result.file_scores[2] < 1.0

    def test_file_scores_fallback_to_router_normalized(self) -> None:
        docs: list[dict] = []  # funnel empty, fallback to router
        self._set_retrieve_docs(docs)
        router_scope = [(1, 8.0), (2, 4.0)]
        result = narrow_scope_via_funnel(
            "test",
            router_scope,
            None,
            None,
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        # Fallback path also min-max normalises to [0, 1]
        assert result.file_scores[1] == 1.0
        assert result.file_scores[2] == 0.0

    def test_single_retrieve_call_no_dedicated_anchor_fetch(self) -> None:
        docs = [_make_doc(file_id=1, score=0.9)]
        self._set_retrieve_docs(docs)
        narrow_scope_via_funnel(
            "test",
            None,
            None,
            [10],
            [1],
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        assert self.mock_retrieve.call_count == 1

    def test_anchor_meeting_ids_unioned_into_wide_fetch(self) -> None:
        self._set_retrieve_docs([])
        narrow_scope_via_funnel(
            "test",
            None,
            [10],
            [99],
            None,
            None,
            None,
            None,
            target_files=5,
            min_chunk_evidence=0.0,
        )
        call_args = self.mock_retrieve.call_args
        meeting_ids_arg = (
            call_args[1].get("meeting_ids") or call_args[0][1]
            if len(call_args[0]) > 1
            else call_args[1].get("meeting_ids")
        )
        # Should be union of [10] + [99]
        assert set(meeting_ids_arg) == {10, 99}


# ---------------------------------------------------------------------------
# anchor_quota_ratio parameterization (P3)
# ---------------------------------------------------------------------------
class TestAnchorQuotaRatio:
    @pytest.fixture(autouse=True)
    def _patch_deps(self) -> None:
        with (
            patch("src.services.rag._funnel_narrow.retrieve") as mock_retrieve,
            patch(
                "src.services.rag._funnel_narrow._vector_score_lower_is_better", return_value=False
            ),
            patch("src.services.rag._funnel_narrow.settings") as mock_settings,
            patch("src.services.rag._funnel_narrow.FUNNEL_NARROW_ANCHOR_EVICT"),
        ):
            self.mock_retrieve = mock_retrieve
            self.mock_settings = mock_settings
            self.mock_settings.TOP_K = 10
            self.mock_settings.RAG_FUNNEL_FETCH_MULTIPLIER = 3
            self.mock_settings.RAG_ANCHOR_ENABLED = True
            self.mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = True
            self.mock_settings.RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO = 0.8
            self.mock_settings.RAG_FUNNEL_AGGREGATION = "top_k_mean"
            self.mock_settings.RAG_FUNNEL_AGG_TOP_K = 3
            self.mock_settings.RAG_FUNNEL_AGGREGATION_ALPHA = 1.0
            self.mock_settings.RAG_FUNNEL_MERGE_STRATEGY = "rrf"
            self.mock_settings.RAG_FUNNEL_RRF_K = 60
            self.mock_settings.RAG_FUNNEL_FILE_PRIOR_ENABLED = False
            self.mock_settings.RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS = 0.0
            self.mock_settings.RAG_FUNNEL_EVIDENCE_MODE = "absolute"
            self.mock_settings.RAG_FUNNEL_WIDE_K_MIN = 0
            self.mock_settings.RAG_FUNNEL_WIDE_K_MAX = 0
            self.mock_settings.RAG_FUNNEL_MULTIMODAL_ENABLED = False
            self.mock_settings.RAGANYTHING_ENABLED = False
            yield

    def _set_retrieve_docs(self, docs: list[dict]) -> None:
        self.mock_retrieve.return_value = (docs, MagicMock())

    def test_quota_zero_still_injects_one_anchor(self) -> None:
        """LOW-4 fix: quota_ratio=0 still injects min 1 anchor via max(1, ...)."""
        self.mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.0
        docs = [_make_doc(file_id=i, score=0.8 - i * 0.05) for i in range(1, 9)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [99],
            None,
            None,
            None,
            target_files=8,
            min_chunk_evidence=0.0,
        )
        assert 99 in result.scope_file_ids

    def test_quota_half_default(self) -> None:
        self.mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
        docs = [_make_doc(file_id=i, score=0.8 - i * 0.05) for i in range(1, 9)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [99],
            None,
            None,
            None,
            target_files=8,
            min_chunk_evidence=0.0,
        )
        assert 99 in result.scope_file_ids
        assert len(result.scope_file_ids) <= 8

    def test_quota_one_all_anchor(self) -> None:
        self.mock_settings.RAG_ANCHOR_QUOTA_RATIO = 1.0
        docs = [_make_doc(file_id=i, score=0.8 - i * 0.05) for i in range(1, 5)]
        self._set_retrieve_docs(docs)
        result = narrow_scope_via_funnel(
            "test",
            None,
            None,
            None,
            [99, 100],
            None,
            None,
            None,
            target_files=4,
            min_chunk_evidence=0.0,
        )
        # With quota=1.0, up to 4 anchor files can be appended
        for f in [99, 100]:
            assert f in result.scope_file_ids


# ---------------------------------------------------------------------------
# H1: Anchor-only file score uses median-derived fallback, not 1.0
# ---------------------------------------------------------------------------
class TestComputeFileScoresAnchorFallback:
    def test_anchor_only_gets_median_derived_score(self) -> None:
        # File 99 is anchor-only (not in funnel or router).
        # Funnel has files 1..4 with normalised scores 0.0..1.0.
        scores = _compute_file_scores(
            scope=[1, 2, 3, 4, 99],
            funnel_candidates=[(1, 0.9), (2, 0.7), (3, 0.5), (4, 0.3)],
            router_scope=None,
        )
        # Median of [1.0, 0.667, 0.333, 0.0] ≈ 0.5 → fallback ≈ 0.8*0.5 = 0.4
        assert scores[99] < 0.6
        assert scores[99] > 0.0
        # Anchor-only score must be strictly less than top funnel score
        assert scores[99] < scores[1]

    def test_all_scores_empty_fallback_to_half(self) -> None:
        scores = _compute_file_scores(
            scope=[99],
            funnel_candidates=[],
            router_scope=None,
        )
        assert scores[99] == 0.5

    def test_anchor_score_lower_than_high_funnel(self) -> None:
        scores = _compute_file_scores(
            scope=[1, 99],
            funnel_candidates=[(1, 0.95), (2, 0.1)],
            router_scope=None,
        )
        # 99 gets 0.8 * median, which is < 1.0 (the top funnel norm)
        assert scores[99] < scores[1]

    def test_existing_funnel_router_scores_unchanged(self) -> None:
        scores = _compute_file_scores(
            scope=[1, 2],
            funnel_candidates=[(1, 0.9)],
            router_scope=[(2, 0.5)],
        )
        # File 1 from funnel → normalized to 1.0 (only funnel entry)
        assert scores[1] == 1.0
        # File 2 from router → normalized to 1.0 (only router entry)
        assert scores[2] == 1.0


# ---------------------------------------------------------------------------
# B4: _compute_file_scores — proper statistical median (even-n case)
# ---------------------------------------------------------------------------
class TestProperMedianAnchorFallback:
    def test_two_file_median_is_not_max(self) -> None:
        """With 2 in-scope files, proper median is the midpoint, not the max."""
        scores = _compute_file_scores(
            scope=[1, 2, 99],
            funnel_candidates=[(1, 1.0), (2, 0.0)],
            router_scope=None,
        )
        # norm: {1: 1.0, 2: 0.0}. Proper median of [0.0, 1.0] = 0.5.
        # Old bug: sorted_scores[n//2] = sorted_scores[1] = 1.0 → fallback = 0.8.
        # Fix:  (0.0 + 1.0) / 2 = 0.5 → fallback = 0.4.
        assert scores[99] < 0.6
        assert scores[99] < scores[1]

    def test_four_file_even_median(self) -> None:
        """Even-length list: median = average of two centre elements."""
        scores = _compute_file_scores(
            scope=[1, 2, 3, 4, 99],
            funnel_candidates=[(1, 0.9), (2, 0.7), (3, 0.5), (4, 0.3)],
            router_scope=None,
        )
        # norm: {1:1.0, 2:0.667, 3:0.333, 4:0.0}.
        # Sorted = [0.0, 0.333, 0.667, 1.0]. Median = (0.333 + 0.667) / 2 = 0.5.
        assert abs(scores[99] - 0.8 * 0.5) < 1e-6

    def test_odd_count_median_unchanged(self) -> None:
        """Odd-length list: middle element (no change from old formula)."""
        scores = _compute_file_scores(
            scope=[1, 2, 3, 99],
            funnel_candidates=[(1, 0.9), (2, 0.5), (3, 0.1)],
            router_scope=None,
        )
        # norm: {1:1.0, 2:0.5, 3:0.0}. Sorted = [0.0, 0.5, 1.0]. Median = 0.5.
        assert abs(scores[99] - 0.8 * 0.5) < 1e-6


# ---------------------------------------------------------------------------
# D: _apply_evidence_threshold — three-mode boundary tests
# ---------------------------------------------------------------------------
class TestApplyEvidenceThreshold:
    def test_absolute_keeps_above_and_equal(self) -> None:
        # threshold=0.3: files with score 0.5 and 0.3 pass; 0.1 is dropped.
        candidates = [(1, 0.5), (2, 0.1), (3, 0.3)]
        result = _apply_evidence_threshold(candidates, mode="absolute", threshold=0.3)
        assert [fid for fid, _ in result] == [1, 3]

    def test_absolute_all_below_returns_empty(self) -> None:
        assert _apply_evidence_threshold([(1, 0.1)], mode="absolute", threshold=0.5) == []

    def test_absolute_threshold_clamped_above_one(self) -> None:
        # threshold > 1.0 is clamped to 1.0; nothing can pass
        candidates = [(1, 0.9), (2, 0.5)]
        assert _apply_evidence_threshold(candidates, mode="absolute", threshold=2.0) == []

    def test_ratio_cutoff_relative_to_top(self) -> None:
        candidates = [(1, 1.0), (2, 0.5), (3, 0.1)]
        # threshold=0.4 → cutoff = 0.4 * 1.0 = 0.4; file 3 dropped
        result = _apply_evidence_threshold(candidates, mode="ratio", threshold=0.4)
        assert [fid for fid, _ in result] == [1, 2]

    def test_ratio_zero_top_returns_empty(self) -> None:
        candidates = [(1, 0.0), (2, 0.0)]
        assert _apply_evidence_threshold(candidates, mode="ratio", threshold=0.3) == []

    def test_ratio_preserves_order(self) -> None:
        candidates = [(3, 0.9), (1, 0.8), (2, 0.3)]
        result = _apply_evidence_threshold(candidates, mode="ratio", threshold=0.5)
        assert [fid for fid, _ in result] == [3, 1]

    def test_percentile_keep_top_fraction(self) -> None:
        candidates = [(1, 0.9), (2, 0.7), (3, 0.5), (4, 0.3)]
        # threshold=0.25 → keep_n = round(4 * 0.75) = 3
        result = _apply_evidence_threshold(candidates, mode="percentile", threshold=0.25)
        assert [fid for fid, _ in result] == [1, 2, 3]

    def test_percentile_at_least_one_always_kept(self) -> None:
        candidates = [(1, 0.1)]
        result = _apply_evidence_threshold(candidates, mode="percentile", threshold=0.99)
        assert len(result) == 1

    def test_empty_input_all_modes(self) -> None:
        for mode in ("absolute", "ratio", "percentile"):
            assert _apply_evidence_threshold([], mode=mode, threshold=0.3) == []


# ---------------------------------------------------------------------------
# D: _resolve_wide_k — clamp regression (B1 fix)
# ---------------------------------------------------------------------------
class TestResolveWideK:
    def test_no_bounds_returns_base(self) -> None:
        """MIN=MAX=0 → returns base = TOP_K * FETCH_MULTIPLIER without DB lookup."""
        with patch("src.services.rag._funnel_narrow.settings") as s:
            s.TOP_K = 8
            s.RAG_FUNNEL_FETCH_MULTIPLIER = 10
            s.RAG_FUNNEL_WIDE_K_MIN = 0
            s.RAG_FUNNEL_WIDE_K_MAX = 0
            result = _resolve_wide_k([])
        assert result == 80

    def test_hard_cap_blocks_log_overflow(self) -> None:
        """log_factor=4 on large corpus must not exceed _MAX_WIDE_FETCH_SIZE."""
        with (
            patch("src.services.rag._funnel_narrow.settings") as s,
            patch(
                "src.services.rag._funnel_narrow._estimate_scope_chunk_count",
                return_value=10_000,
            ),
        ):
            s.TOP_K = 8
            s.RAG_FUNNEL_FETCH_MULTIPLIER = 10
            s.RAG_FUNNEL_WIDE_K_MIN = 10
            s.RAG_FUNNEL_WIDE_K_MAX = 0
            s._MAX_WIDE_FETCH_SIZE = 200
            result = _resolve_wide_k([1, 2])
        # base=80, log10(10000)=4 → candidate=320; hard cap at 200
        assert result <= 200

    def test_min_bound_raises_small_corpus(self) -> None:
        with (
            patch("src.services.rag._funnel_narrow.settings") as s,
            patch(
                "src.services.rag._funnel_narrow._estimate_scope_chunk_count",
                return_value=5,
            ),
        ):
            s.TOP_K = 4
            s.RAG_FUNNEL_FETCH_MULTIPLIER = 3
            s.RAG_FUNNEL_WIDE_K_MIN = 50
            s.RAG_FUNNEL_WIDE_K_MAX = 200
            s._MAX_WIDE_FETCH_SIZE = 200
            result = _resolve_wide_k([1])
        assert result >= 50

    def test_max_bound_clips_large_value(self) -> None:
        with (
            patch("src.services.rag._funnel_narrow.settings") as s,
            patch(
                "src.services.rag._funnel_narrow._estimate_scope_chunk_count",
                return_value=1_000,
            ),
        ):
            s.TOP_K = 8
            s.RAG_FUNNEL_FETCH_MULTIPLIER = 10
            s.RAG_FUNNEL_WIDE_K_MIN = 10
            s.RAG_FUNNEL_WIDE_K_MAX = 60
            s._MAX_WIDE_FETCH_SIZE = 200
            result = _resolve_wide_k([1])
        assert result <= 60

    def test_result_always_at_least_one(self) -> None:
        with (
            patch("src.services.rag._funnel_narrow.settings") as s,
            patch(
                "src.services.rag._funnel_narrow._estimate_scope_chunk_count",
                return_value=0,
            ),
        ):
            s.TOP_K = 1
            s.RAG_FUNNEL_FETCH_MULTIPLIER = 1
            s.RAG_FUNNEL_WIDE_K_MIN = 1
            s.RAG_FUNNEL_WIDE_K_MAX = 5
            s._MAX_WIDE_FETCH_SIZE = 200
            result = _resolve_wide_k([])
        assert result >= 1


# ---------------------------------------------------------------------------
# D: router/funnel extreme divergence (chaos-equivalent unit test)
# ---------------------------------------------------------------------------
class TestRouterFunnelDivergence:
    def test_rrf_completely_disjoint_both_contribute(self) -> None:
        """When router and funnel share zero files, RRF merges both halves fairly."""
        router = [(1, 0.9), (2, 0.8), (3, 0.7)]
        funnel = [(4, 0.9), (5, 0.8), (6, 0.7)]
        result = _rank_aware_union_rrf(router, funnel, target_files=6)
        assert set(result) == {1, 2, 3, 4, 5, 6}
        # Top funnel file (rank 0) must beat bottom router file (rank 2) via RRF.
        assert result.index(4) < result.index(3)

    def test_rrf_disjoint_cap_respected(self) -> None:
        router = [(i, 1.0 / (i + 1)) for i in range(1, 10)]
        funnel = [(i + 10, 1.0 / (i + 1)) for i in range(1, 10)]
        result = _rank_aware_union_rrf(router, funnel, target_files=4)
        assert len(result) == 4

    def test_zigzag_disjoint_strict_alternation(self) -> None:
        """In zigzag mode, completely disjoint sets alternate router/funnel."""
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(3, 0.9), (4, 0.8)]
        result = _rank_aware_union_zigzag(router, funnel, target_files=4)
        assert result == [1, 3, 2, 4]

    def test_merge_router_funnel_dispatch_rrf_disjoint(self) -> None:
        router = [(1, 0.9), (2, 0.8)]
        funnel = [(3, 0.9), (4, 0.8)]
        result = _merge_router_funnel(router, funnel, target_files=4, strategy="rrf")
        assert set(result) == {1, 2, 3, 4}
