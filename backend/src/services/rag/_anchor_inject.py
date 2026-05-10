"""Anchor file injection helper.

Both :mod:`_funnel_narrow` and the ``router_only`` scoping strategy need
to merge "must-include" anchor files into a candidate scope while staying
within a cap.  The helper here is the single source of truth so the two
call sites cannot drift.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def apply_anchor_evict(
    scope: list[int],
    anchor_file_ids: list[int] | None,
    *,
    cap: int,
    quota_ratio: float,
) -> tuple[list[int], int]:
    """Append missing anchor files, evicting tail non-anchors as needed.

    Args:
        scope: current ordered scope file IDs (highest priority first).
        anchor_file_ids: files that must appear in the result if quota allows.
        cap: maximum number of evictions; also bounds the anchor quota.
            ``funnel_narrow`` passes ``target_files``; ``router_only``
            passes ``len(scope)``.
        quota_ratio: max fraction of *cap* that anchor files may consume.
            ``0.0`` disables anchor injection; ``1.0`` allows full replacement.

    Returns:
        ``(new_scope, evicted)`` where ``new_scope`` is a freshly allocated
        list and ``evicted`` counts how many non-anchor files were dropped
        from the tail.  When ``anchor_file_ids`` is empty / falsy, the
        scope is returned unchanged with ``evicted=0``.
    """
    if not anchor_file_ids or not scope:
        return list(scope), 0

    anchor_set = set(anchor_file_ids)
    existing = set(scope)
    missing = [f for f in anchor_file_ids if f not in existing]
    if not missing:
        return list(scope), 0

    anchor_quota = math.ceil(cap * quota_ratio)

    non_anchor_pos = [i for i, f in enumerate(scope) if f not in anchor_set]
    evict_count = min(len(missing), len(non_anchor_pos), anchor_quota)
    if evict_count <= 0:
        return list(scope), 0

    evict_set = set(non_anchor_pos[-evict_count:])
    kept = [f for i, f in enumerate(scope) if i not in evict_set]
    new_scope = kept + missing[:evict_count]
    # H-RAG-7: Log anchor injection metrics for observability.
    injected = min(len(missing), evict_count)
    skipped = len(missing) - injected
    logger.info(
        "Anchor injection: %d/%d anchors injected, %d skipped (quota=%d, cap=%d, ratio=%.2f)",
        injected,
        len(anchor_file_ids),
        skipped,
        anchor_quota,
        cap,
        quota_ratio,
    )
    return new_scope, evict_count
