"""Authoritative temporal eligibility for current-memory consumers."""

from datetime import UTC, datetime
from typing import Any


def is_active_memory(row: dict[str, Any], *, now: datetime | None = None) -> bool:
    if row.get("archived_at"):
        return False
    if str(row.get("assertion_status") or "confirmed").lower() != "confirmed":
        return False
    if row.get("superseded_by"):
        return False
    instant = now or datetime.now(UTC)
    for field in ("expires_at", "valid_to", "valid_from"):
        raw = row.get(field)
        if not raw:
            continue
        try:
            boundary = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return False  # Invalid temporal metadata cannot authorize recall.
        if field == "valid_from":
            if boundary > instant:
                return False
        elif boundary <= instant:
            return False
    return True
