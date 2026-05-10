"""Cross-field validation for RAG broad-recall settings."""

from __future__ import annotations

import pytest

from src.core.config import Settings


def _make_settings(**overrides: object) -> Settings:
    """Build a Settings instance with overrides applied via env-style kwargs."""
    return Settings(**overrides)


class TestRagConfigValidator:
    def test_valid_defaults(self) -> None:
        s = _make_settings()
        assert s.RAG_FILE_SCOPING_MODE in Settings._VALID_FILE_SCOPING_MODES
        assert s.RAG_FUNNEL_MERGE_STRATEGY in Settings._VALID_FUNNEL_MERGE_STRATEGIES
        assert s.RAG_FUNNEL_FILE_PRIOR_MODE in Settings._VALID_FILE_PRIOR_MODES

    def test_unknown_scoping_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FILE_SCOPING_MODE"):
            _make_settings(RAG_FILE_SCOPING_MODE="not-a-mode")

    def test_unknown_merge_strategy_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_MERGE_STRATEGY"):
            _make_settings(RAG_FUNNEL_MERGE_STRATEGY="bayesian")

    def test_unknown_aggregation_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_AGGREGATION"):
            _make_settings(RAG_FUNNEL_AGGREGATION="median")

    def test_anchor_quota_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_settings(RAG_ANCHOR_QUOTA_RATIO=1.5)

    def test_evidence_threshold_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_NARROW_MIN_EVIDENCE"):
            _make_settings(RAG_FUNNEL_NARROW_MIN_EVIDENCE=2.0)

    def test_scope_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="RAG_BROAD_RECALL_SCOPE_CAP"):
            _make_settings(RAG_BROAD_RECALL_SCOPE_CAP=0)

    def test_fetch_multiplier_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_FETCH_MULTIPLIER"):
            _make_settings(RAG_FUNNEL_FETCH_MULTIPLIER=0)

    def test_wide_fetch_safety_cap(self) -> None:
        with pytest.raises(ValueError, match="wide-fetch safety cap"):
            _make_settings(TOP_K=50, RAG_FUNNEL_FETCH_MULTIPLIER=10)

    def test_rrf_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_RRF_K"):
            _make_settings(RAG_FUNNEL_RRF_K=0)

    def test_unknown_evidence_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_EVIDENCE_MODE"):
            _make_settings(RAG_FUNNEL_EVIDENCE_MODE="fuzzy")

    def test_unknown_anchor_ttl_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_ANCHOR_TTL_MODE"):
            _make_settings(RAG_ANCHOR_TTL_MODE="expiring")

    def test_unknown_mq_merge_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_BROAD_RECALL_MQ_MERGE"):
            _make_settings(RAG_BROAD_RECALL_MQ_MERGE="weighted")

    def test_unknown_file_prior_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="RAG_FUNNEL_FILE_PRIOR_MODE"):
            _make_settings(RAG_FUNNEL_FILE_PRIOR_MODE="exponential")

    def test_wide_k_min_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            _make_settings(RAG_FUNNEL_WIDE_K_MIN=200, RAG_FUNNEL_WIDE_K_MAX=50)

    def test_wide_k_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            _make_settings(RAG_FUNNEL_WIDE_K_MIN=-1)

    def test_valid_evidence_modes(self) -> None:
        for mode in ("absolute", "ratio", "percentile"):
            s = _make_settings(RAG_FUNNEL_EVIDENCE_MODE=mode)
            assert mode == s.RAG_FUNNEL_EVIDENCE_MODE

    def test_valid_file_prior_modes(self) -> None:
        for mode in ("additive", "multiplicative"):
            s = _make_settings(RAG_FUNNEL_FILE_PRIOR_MODE=mode)
            assert mode == s.RAG_FUNNEL_FILE_PRIOR_MODE
