"""Index reconciliation between meeting_files metadata and index_state."""

from __future__ import annotations

from ...core.database import reconcile_index_state


def reconcile_multimodal_index_state(*, limit: int = 500) -> dict[str, int]:
    """Backfill index_state rows from ready meeting files."""
    return reconcile_index_state(limit=limit)
