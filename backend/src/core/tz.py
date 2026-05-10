"""Centralized timezone utilities.

DB storage and internal comparisons must stay UTC for consistency with SQLite
CURRENT_TIMESTAMP.  Use ``format_local`` for any user-facing output (banners,
traces, etc.).
"""

from datetime import UTC, datetime, tzinfo


def _resolve_local_tz() -> tzinfo:
    """Resolve the host's current local timezone on every call.

    Defends against DST flips and ``/etc/localtime`` swaps in long-running
    containers where a module-level constant would become stale.
    """
    tz = datetime.now(UTC).astimezone().tzinfo
    if tz is None:
        return UTC
    return tz


def format_local(
    fmt: str = "%Y-%m-%d %H:%M:%S",
    *,
    include_tz: bool = True,
) -> str:
    """Return the current time formatted in the system local timezone."""
    tz = _resolve_local_tz()
    ts = datetime.now(tz).strftime(fmt)
    if include_tz:
        ts += f" ({tz})"
    return ts
