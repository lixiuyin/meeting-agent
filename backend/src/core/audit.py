"""Structured audit logging for data mutations.

All mutation operations (create, update, delete) should call audit_log()
to produce a traceable record of who changed what and when.

The audit logger uses a dedicated 'audit' logger name so it can be
routed to a separate sink (file, SIEM) via standard logging config.
"""

import logging
import re

audit_logger = logging.getLogger("audit")

# M-19: Patterns for common PII that should never appear in audit logs.
_PII_PATTERNS = (
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b(?:\d{3}[- ]?){2}\d{4}\b"), "[SSN]"),
)


def _redact_pii(text: str) -> str:
    """Redact common PII patterns from audit detail strings."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def audit_log(
    action: str,
    resource_type: str,
    resource_id: str | int,
    *,
    user_id: str = "anonymous",
    detail: str = "",
) -> None:
    """Emit a structured audit log entry.

    Args:
        action: What happened (e.g. "create", "delete", "update").
        resource_type: Kind of resource (e.g. "meeting", "session", "memory").
        resource_id: Identifier of the affected resource.
        user_id: Who performed the action.
        detail: Optional extra context (keep short, no secrets).
    """
    audit_logger.info(
        "action=%s resource=%s/%s user=%s %s",
        action,
        resource_type,
        resource_id,
        user_id,
        _redact_pii(detail),
    )
