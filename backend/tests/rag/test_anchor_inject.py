"""Tests for the anchor evict helper used by funnel narrow and router_only."""

from __future__ import annotations

import pytest

from src.services.rag._anchor_inject import apply_anchor_evict


class TestApplyAnchorEvict:
    def test_no_anchor_passthrough(self) -> None:
        scope, evicted = apply_anchor_evict([1, 2, 3], None, cap=8, quota_ratio=0.5)
        assert scope == [1, 2, 3]
        assert evicted == 0

    def test_empty_anchor_passthrough(self) -> None:
        scope, evicted = apply_anchor_evict([1, 2, 3], [], cap=8, quota_ratio=0.5)
        assert scope == [1, 2, 3]
        assert evicted == 0

    def test_empty_scope_passthrough(self) -> None:
        scope, evicted = apply_anchor_evict([], [99], cap=8, quota_ratio=0.5)
        assert scope == []
        assert evicted == 0

    def test_anchor_already_present_no_evict(self) -> None:
        scope, evicted = apply_anchor_evict([1, 2, 3], [2], cap=8, quota_ratio=0.5)
        assert scope == [1, 2, 3]
        assert evicted == 0

    def test_quota_zero_no_eviction(self) -> None:
        scope, evicted = apply_anchor_evict([1, 2, 3], [99], cap=8, quota_ratio=0.0)
        assert scope == [1, 2, 3]
        assert evicted == 0
        assert 99 not in scope

    def test_basic_evict_tail(self) -> None:
        # cap=8, ratio=0.5 -> quota=4; one missing anchor evicts last non-anchor
        scope, evicted = apply_anchor_evict(
            [1, 2, 3, 4, 5, 6, 7, 8],
            [99],
            cap=8,
            quota_ratio=0.5,
        )
        assert 99 in scope
        assert 8 not in scope  # tail evicted
        assert evicted == 1
        assert len(scope) == 8

    def test_quota_caps_evictions(self) -> None:
        # 3 anchors missing, but quota allows only 2 evictions
        scope, evicted = apply_anchor_evict(
            [1, 2, 3, 4],
            [99, 100, 101],
            cap=4,
            quota_ratio=0.5,
        )
        # quota = int(4 * 0.5) = 2
        assert evicted == 2
        anchors_in = sum(a in scope for a in [99, 100, 101])
        assert anchors_in == 2
        assert len(scope) == 4

    def test_quota_full_replacement(self) -> None:
        scope, evicted = apply_anchor_evict(
            [1, 2, 3, 4],
            [99, 100],
            cap=4,
            quota_ratio=1.0,
        )
        for a in [99, 100]:
            assert a in scope
        assert evicted == 2

    def test_returns_new_list(self) -> None:
        original = [1, 2, 3]
        scope, _ = apply_anchor_evict(original, None, cap=8, quota_ratio=0.5)
        assert scope is not original
        scope.append(99)
        assert original == [1, 2, 3]

    def test_anchor_order_preserved(self) -> None:
        # Anchors append in input order
        scope, _ = apply_anchor_evict(
            [1, 2, 3, 4],
            [99, 100],
            cap=4,
            quota_ratio=1.0,
        )
        assert scope.index(99) < scope.index(100)

    @pytest.mark.parametrize(
        "ratio,expected_anchor_count",
        [
            (0.0, 0),
            (0.25, 1),  # int(4*0.25) = 1
            (0.5, 2),  # int(4*0.5) = 2
            (1.0, 2),  # capped by missing count
        ],
    )
    def test_quota_ratio_parameterised(self, ratio: float, expected_anchor_count: int) -> None:
        scope, evicted = apply_anchor_evict(
            [1, 2, 3, 4],
            [99, 100],
            cap=4,
            quota_ratio=ratio,
        )
        anchor_count = sum(a in scope for a in [99, 100])
        assert anchor_count == expected_anchor_count
        assert evicted == expected_anchor_count
