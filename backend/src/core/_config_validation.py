"""RAG configuration validation extracted from Settings class.

Separates the ~180 lines of cross-field validation logic from the field
declarations so ``config.py`` stays focused on schema definition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.config import Settings

_VALID_FILE_SCOPING_MODES: frozenset[str] = frozenset(
    {"router_and_funnel", "funnel_only", "router_pre_filter", "router_only"}
)
_VALID_FUNNEL_MERGE_STRATEGIES: frozenset[str] = frozenset({"rrf", "zigzag"})
_VALID_FUNNEL_AGGREGATIONS: frozenset[str] = frozenset({"top_k_mean", "max", "count"})
_VALID_FUNNEL_EVIDENCE_MODES: frozenset[str] = frozenset({"absolute", "ratio", "percentile"})
_VALID_ANCHOR_TTL_MODES: frozenset[str] = frozenset({"fixed", "sliding"})
_VALID_FILE_PRIOR_MODES: frozenset[str] = frozenset({"additive", "multiplicative"})
# Cap on wide-fetch size: ``TOP_K * RAG_FUNNEL_FETCH_MULTIPLIER``.  Above
# this the wide fetch dominates request latency without recall gain on a
# corpus of typical size.  Tune the cap upward only when the benchmark
# explicitly justifies it.
_MAX_WIDE_FETCH_SIZE: int = 200


def validate_rag_settings(settings_instance: Settings) -> None:
    """Enforce cross-field invariants on RAG broad-recall configuration.

    Defence-in-depth: most fields are already constrained by ``Field``
    bounds, but a few correctness traps span multiple settings (e.g.
    ``TOP_K * fetch_multiplier`` blowing up Chroma latency).  These
    checks fail fast at startup so misconfiguration never reaches the
    request path.
    """
    import logging as _logging

    s = settings_instance
    _log = _logging.getLogger(__name__)

    if s.RAG_FILE_SCOPING_MODE not in _VALID_FILE_SCOPING_MODES:
        raise ValueError(
            f"RAG_FILE_SCOPING_MODE={s.RAG_FILE_SCOPING_MODE!r} is invalid. "
            f"Must be one of {sorted(_VALID_FILE_SCOPING_MODES)}"
        )
    if s.RAG_FUNNEL_MERGE_STRATEGY not in _VALID_FUNNEL_MERGE_STRATEGIES:
        raise ValueError(
            f"RAG_FUNNEL_MERGE_STRATEGY={s.RAG_FUNNEL_MERGE_STRATEGY!r} is invalid. "
            f"Must be one of {sorted(_VALID_FUNNEL_MERGE_STRATEGIES)}"
        )
    if s.RAG_FUNNEL_AGGREGATION not in _VALID_FUNNEL_AGGREGATIONS:
        raise ValueError(
            f"RAG_FUNNEL_AGGREGATION={s.RAG_FUNNEL_AGGREGATION!r} is invalid. "
            f"Must be one of {sorted(_VALID_FUNNEL_AGGREGATIONS)}"
        )
    if not 0.0 <= s.RAG_ANCHOR_QUOTA_RATIO <= 1.0:
        raise ValueError(f"RAG_ANCHOR_QUOTA_RATIO={s.RAG_ANCHOR_QUOTA_RATIO} must be in [0, 1]")
    if not 0.0 <= s.RAG_FUNNEL_NARROW_MIN_EVIDENCE <= 1.0:
        raise ValueError(
            f"RAG_FUNNEL_NARROW_MIN_EVIDENCE={s.RAG_FUNNEL_NARROW_MIN_EVIDENCE} must be in [0, 1]"
        )
    if s.RAG_BROAD_RECALL_SCOPE_CAP < 1:
        raise ValueError(f"RAG_BROAD_RECALL_SCOPE_CAP={s.RAG_BROAD_RECALL_SCOPE_CAP} must be >= 1")
    if s.RAG_FUNNEL_FETCH_MULTIPLIER < 1:
        raise ValueError(
            f"RAG_FUNNEL_FETCH_MULTIPLIER={s.RAG_FUNNEL_FETCH_MULTIPLIER} must be >= 1"
        )
    if s.TOP_K < 1:
        raise ValueError(f"TOP_K={s.TOP_K} must be >= 1 (zero disables all RAG retrieval)")
    if s.RAG_FUNNEL_RRF_K < 1:
        raise ValueError(f"RAG_FUNNEL_RRF_K={s.RAG_FUNNEL_RRF_K} must be >= 1")
    if s.RAG_FUNNEL_EVIDENCE_MODE not in _VALID_FUNNEL_EVIDENCE_MODES:
        raise ValueError(
            f"RAG_FUNNEL_EVIDENCE_MODE={s.RAG_FUNNEL_EVIDENCE_MODE!r} is invalid. "
            f"Must be one of {sorted(_VALID_FUNNEL_EVIDENCE_MODES)}"
        )
    if s.RAG_ANCHOR_TTL_MODE not in _VALID_ANCHOR_TTL_MODES:
        raise ValueError(
            f"RAG_ANCHOR_TTL_MODE={s.RAG_ANCHOR_TTL_MODE!r} is invalid. "
            f"Must be one of {sorted(_VALID_ANCHOR_TTL_MODES)}"
        )
    if s.RAG_BROAD_RECALL_MQ_MERGE not in _VALID_FUNNEL_MERGE_STRATEGIES:
        raise ValueError(
            f"RAG_BROAD_RECALL_MQ_MERGE={s.RAG_BROAD_RECALL_MQ_MERGE!r} is invalid. "
            f"Must be one of {sorted(_VALID_FUNNEL_MERGE_STRATEGIES)}"
        )
    if s.RAG_FUNNEL_FILE_PRIOR_MODE not in _VALID_FILE_PRIOR_MODES:
        raise ValueError(
            f"RAG_FUNNEL_FILE_PRIOR_MODE={s.RAG_FUNNEL_FILE_PRIOR_MODE!r} is invalid. "
            f"Must be one of {sorted(_VALID_FILE_PRIOR_MODES)}"
        )
    if s.RAG_FUNNEL_WIDE_K_MIN < 0 or s.RAG_FUNNEL_WIDE_K_MAX < 0:
        raise ValueError(
            "RAG_FUNNEL_WIDE_K_MIN/MAX must be >= 0 "
            f"(got MIN={s.RAG_FUNNEL_WIDE_K_MIN} MAX={s.RAG_FUNNEL_WIDE_K_MAX})"
        )
    if (
        s.RAG_FUNNEL_WIDE_K_MAX
        and s.RAG_FUNNEL_WIDE_K_MIN
        and s.RAG_FUNNEL_WIDE_K_MIN > s.RAG_FUNNEL_WIDE_K_MAX
    ):
        raise ValueError(
            f"RAG_FUNNEL_WIDE_K_MIN ({s.RAG_FUNNEL_WIDE_K_MIN}) cannot exceed "
            f"RAG_FUNNEL_WIDE_K_MAX ({s.RAG_FUNNEL_WIDE_K_MAX})"
        )
    wide_fetch_size = s.TOP_K * s.RAG_FUNNEL_FETCH_MULTIPLIER
    if wide_fetch_size > _MAX_WIDE_FETCH_SIZE:
        raise ValueError(
            f"TOP_K ({s.TOP_K}) * RAG_FUNNEL_FETCH_MULTIPLIER "
            f"({s.RAG_FUNNEL_FETCH_MULTIPLIER}) = {wide_fetch_size} "
            f"exceeds the wide-fetch safety cap of {_MAX_WIDE_FETCH_SIZE}. "
            "Lower one of the values or raise the cap explicitly after "
            "verifying Chroma latency stays acceptable."
        )

    # Soft warnings for unusual but legal combinations
    if s.RAG_BROAD_RECALL_SCOPE_CAP > s.RAG_FUNNEL_TOP_FILES:
        _log.warning(
            "RAG_BROAD_RECALL_SCOPE_CAP (%d) > RAG_FUNNEL_TOP_FILES (%d): "
            "the funnel will never produce enough candidates to fill the scope. "
            "Either raise RAG_FUNNEL_TOP_FILES or lower the scope cap.",
            s.RAG_BROAD_RECALL_SCOPE_CAP,
            s.RAG_FUNNEL_TOP_FILES,
        )
    anchor_quota_slots = int(s.RAG_BROAD_RECALL_SCOPE_CAP * s.RAG_ANCHOR_QUOTA_RATIO)
    if (
        s.RAG_ANCHOR_BOOST_IN_BROAD_RECALL
        and s.RAG_ANCHOR_MAX_IDS >= s.RAG_BROAD_RECALL_SCOPE_CAP
        and anchor_quota_slots >= s.RAG_BROAD_RECALL_SCOPE_CAP
    ):
        _log.warning(
            "Anchor configuration may dominate the broad-recall scope: "
            "RAG_ANCHOR_MAX_IDS=%d, RAG_BROAD_RECALL_SCOPE_CAP=%d, "
            "RAG_ANCHOR_QUOTA_RATIO=%.2f. Consider lowering one of them.",
            s.RAG_ANCHOR_MAX_IDS,
            s.RAG_BROAD_RECALL_SCOPE_CAP,
            s.RAG_ANCHOR_QUOTA_RATIO,
        )

    # Critical LLM and memory settings
    if s.LLM_MAX_TOKENS < 1:
        raise ValueError(f"LLM_MAX_TOKENS={s.LLM_MAX_TOKENS} must be >= 1")
    if s.LLM_CONTEXT_WINDOW < 1:
        raise ValueError(f"LLM_CONTEXT_WINDOW={s.LLM_CONTEXT_WINDOW} must be >= 1")
    if s.MEMORY_TTL_DAYS < 1:
        raise ValueError(f"MEMORY_TTL_DAYS={s.MEMORY_TTL_DAYS} must be >= 1")
    if s.CHILD_CHUNK_OVERLAP < 0:
        raise ValueError(f"CHILD_CHUNK_OVERLAP={s.CHILD_CHUNK_OVERLAP} must be >= 0")
