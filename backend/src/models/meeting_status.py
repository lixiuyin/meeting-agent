"""Meeting status lifecycle and transition guards."""

from __future__ import annotations

from enum import StrEnum


class MeetingLifecycleStatus(StrEnum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUMMARIZING = "summarizing"
    READY = "ready"
    FAILED = "failed"
    ERROR = "error"


ALLOWED_STATUS_TRANSITIONS: dict[MeetingLifecycleStatus, set[MeetingLifecycleStatus]] = {
    MeetingLifecycleStatus.UPLOADING: {
        MeetingLifecycleStatus.UPLOADING,
        MeetingLifecycleStatus.PROCESSING,
        MeetingLifecycleStatus.FAILED,
        MeetingLifecycleStatus.ERROR,
    },
    MeetingLifecycleStatus.PROCESSING: {
        MeetingLifecycleStatus.PROCESSING,
        MeetingLifecycleStatus.SUMMARIZING,
        MeetingLifecycleStatus.READY,
        MeetingLifecycleStatus.FAILED,
        MeetingLifecycleStatus.ERROR,
    },
    MeetingLifecycleStatus.SUMMARIZING: {
        MeetingLifecycleStatus.SUMMARIZING,
        MeetingLifecycleStatus.READY,
        MeetingLifecycleStatus.FAILED,
        MeetingLifecycleStatus.ERROR,
        MeetingLifecycleStatus.PROCESSING,
    },
    MeetingLifecycleStatus.READY: {
        MeetingLifecycleStatus.READY,
        MeetingLifecycleStatus.PROCESSING,
        MeetingLifecycleStatus.SUMMARIZING,
        MeetingLifecycleStatus.FAILED,
        MeetingLifecycleStatus.ERROR,
    },
    MeetingLifecycleStatus.FAILED: {
        MeetingLifecycleStatus.FAILED,
        MeetingLifecycleStatus.PROCESSING,
        MeetingLifecycleStatus.ERROR,
    },
    MeetingLifecycleStatus.ERROR: {
        MeetingLifecycleStatus.ERROR,
        MeetingLifecycleStatus.PROCESSING,
    },
}


def normalize_status(status: str) -> MeetingLifecycleStatus:
    """Validate and normalize a status string."""
    return MeetingLifecycleStatus(status)


def assert_transition(current: str, nxt: str) -> MeetingLifecycleStatus:
    """Ensure the status transition is valid and return normalized next status."""
    current_status = normalize_status(current)
    next_status = normalize_status(nxt)
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
    if next_status not in allowed:
        raise ValueError(f"Invalid status transition: {current_status} -> {next_status}")
    return next_status
