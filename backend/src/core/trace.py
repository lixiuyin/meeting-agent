"""Structured trace metadata for pipeline execution visibility.

Attaches trace identifiers to requests so the frontend can render
execution timelines (which steps ran, in what order, how long each took).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_LOG_SIZE = 50 * 1024 * 1024  # 50 MB


_ERROR_MESSAGE_MAX_LEN = 500

_METADATA_MAX_BYTES = 16 * 1024
_METADATA_VALUE_MAX_LEN = 256


def _truncate_metadata(meta: dict) -> dict:
    """Truncate metadata values to prevent oversized pipeline log entries."""
    out: dict = {}
    for k, v in meta.items():
        s = str(v)
        if len(s) > _METADATA_VALUE_MAX_LEN:
            out[k] = f"{s[:_METADATA_VALUE_MAX_LEN]}...(+{len(s) - _METADATA_VALUE_MAX_LEN} chars)"
        else:
            out[k] = s
        if sum(len(str(x)) for x in out.values()) > _METADATA_MAX_BYTES:
            out["_truncated"] = True
            break
    return out


@dataclass
class TraceSpan:
    """A single timed span within a pipeline execution."""

    label: str
    phase: str  # e.g. "retrieve", "rerank", "generate", "memory"
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "running"  # running | success | error
    metadata: dict = field(default_factory=dict)
    parent_label: str | None = None
    skipped: bool = False

    # Optional enriched fields (populated by pipeline steps)
    tokens_in: int | None = None
    tokens_out: int | None = None
    docs_retrieved: int | None = None
    # When status=="error", capture the exception class + message so that
    # pipeline.jsonl is self-describing instead of just "status: error".
    error_type: str | None = None
    error_message: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def finish(self, status: str = "success", error: BaseException | None = None) -> None:
        self.end_time = time.monotonic()
        self.status = status
        if error is not None:
            self.error_type = type(error).__name__
            msg = str(error) or repr(error)
            if len(msg) > _ERROR_MESSAGE_MAX_LEN:
                msg = msg[:_ERROR_MESSAGE_MAX_LEN] + "…"
            self.error_message = msg

    def to_dict(self) -> dict:
        d: dict = {
            "label": self.label,
            "phase": self.phase,
            "duration_ms": round(self.duration_ms, 1) if self.duration_ms is not None else None,
            "status": self.status,
        }
        if self.parent_label is not None:
            d["parent_label"] = self.parent_label
        if self.skipped:
            d["skipped"] = True
        if self.tokens_in is not None:
            d["tokens_in"] = self.tokens_in
        if self.tokens_out is not None:
            d["tokens_out"] = self.tokens_out
        if self.docs_retrieved is not None:
            d["docs_retrieved"] = self.docs_retrieved
        if self.error_type:
            d["error_type"] = self.error_type
        if self.error_message:
            d["error_message"] = self.error_message
        if self.metadata:
            d["metadata"] = _truncate_metadata(self.metadata)
        return d


@dataclass
class TraceContext:
    """Carries trace metadata through the RAG pipeline.

    Created once per request, threaded through all pipeline steps.
    """

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    spans: list[TraceSpan] = field(default_factory=list)

    def start_span(
        self,
        label: str,
        phase: str,
        *,
        parent_label: str | None = None,
        skipped: bool = False,
        **meta: object,
    ) -> TraceSpan:
        """Start a new trace span."""
        span = TraceSpan(
            label=label,
            phase=phase,
            metadata=meta,
            parent_label=parent_label,
            skipped=skipped,
        )
        self.spans.append(span)
        if skipped:
            span.finish("success")
        return span

    def finish_span(
        self,
        label: str,
        status: str = "success",
        error: BaseException | None = None,
    ) -> None:
        """Finish a span by label. Pass ``error`` to capture exception details."""
        for span in reversed(self.spans):
            if span.label == label and span.end_time is None:
                span.finish(status, error=error)
                return

    @property
    def total_ms(self) -> float:
        if not self.spans:
            return 0.0
        first = self.spans[0].start_time
        last = max(s.end_time or time.monotonic() for s in self.spans)
        return (last - first) * 1000

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_ms": round(self.total_ms, 1),
            "spans": [s.to_dict() for s in self.spans],
        }

    def to_pipeline_log(
        self,
        question: str,
        *,
        skill_used: str | None = None,
        skill_confidence: float | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Return a flat dict suitable for JSONL pipeline performance logging."""
        span_durations: dict[str, float | None] = {}
        tokens_in: int | None = None
        tokens_out: int | None = None
        docs_retrieved: int | None = None
        inner_error: TraceSpan | None = None
        wrapper_error: TraceSpan | None = None

        for s in self.spans:
            span_durations[s.label] = round(s.duration_ms, 1) if s.duration_ms is not None else None
            if s.status == "error" and s.error_type:
                # Prefer the innermost failing span: skip the outermost
                # "pipeline" wrapper, which just re-raises and adds no info
                # beyond what the real failing step already captured.
                if s.label == "pipeline":
                    if wrapper_error is None:
                        wrapper_error = s
                elif inner_error is None:
                    inner_error = s
            if s.tokens_in is not None:
                tokens_in = s.tokens_in
            if s.tokens_out is not None:
                tokens_out = s.tokens_out
            if s.docs_retrieved is not None:
                docs_retrieved = s.docs_retrieved

        first_error_span = inner_error or wrapper_error

        has_error = any(s.status == "error" for s in self.spans)
        log: dict = {
            "timestamp": datetime.now(UTC).astimezone().isoformat(),
            "trace_id": self.trace_id,
            "question": question[:200],
            "total_ms": round(self.total_ms, 1),
            "spans": span_durations,
            "status": "error" if has_error else "success",
        }
        if session_id:
            log["session_id"] = session_id
        if skill_used:
            log["skill_used"] = skill_used
        if skill_confidence is not None:
            log["skill_confidence"] = skill_confidence
        if tokens_in is not None:
            log["tokens_in"] = tokens_in
        if tokens_out is not None:
            log["tokens_out"] = tokens_out
        if docs_retrieved is not None:
            log["docs_retrieved"] = docs_retrieved
        if first_error_span is not None:
            log["error_span"] = first_error_span.label
            log["error_type"] = first_error_span.error_type
            if first_error_span.error_message:
                log["error_message"] = first_error_span.error_message
        return log


# ---------------------------------------------------------------------------
# Pipeline performance log writer
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()


def write_pipeline_log(
    trace: TraceContext,
    question: str,
    *,
    log_dir: str | Path = "data/logs",
    skill_used: str | None = None,
    skill_confidence: float | None = None,
    session_id: str | None = None,
) -> None:
    """Append one JSON line to ``data/logs/pipeline.jsonl``.

    Thread-safe, silently catches all IO errors.
    """
    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / "pipeline.jsonl"

        # Rotate if oversized
        with _write_lock:
            try:
                if log_file.exists() and log_file.stat().st_size > _MAX_LOG_SIZE:
                    backup = log_file.with_suffix(".jsonl.1")
                    backup.unlink(missing_ok=True)
                    log_file.rename(backup)
            except OSError:
                pass

            entry = trace.to_pipeline_log(
                question,
                skill_used=skill_used,
                skill_confidence=skill_confidence,
                session_id=session_id,
            )
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):
        logger.debug("Failed to write pipeline log", exc_info=True)
