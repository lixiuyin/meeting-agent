"""Verify pipeline trace captures exception type + message.

Before this change ``pipeline.jsonl`` only contained ``"status": "error"``
with no hint of what went wrong, making operational debugging painful.
"""

from __future__ import annotations

import json
import stat

from src.core.trace import TraceContext, write_ingest_trace, write_pipeline_log


class TestTraceErrorCapture:
    def test_serialized_trace_has_causal_process_fields(self) -> None:
        trace = TraceContext(trace_id="trace-test")
        trace.start_span("pipeline", "pipeline")
        trace.start_span("retrieve", "retrieve", parent_label="pipeline")
        trace.finish_span("retrieve")
        trace.finish_span("pipeline")

        payload = trace.to_dict()
        pipeline, retrieve = payload["spans"]

        assert payload["schema_version"] == 2
        assert pipeline["span_id"] == "trace-test:0"
        assert pipeline["sequence"] == 0
        assert retrieve["span_id"] == "trace-test:1"
        assert retrieve["parent_span_id"] == "trace-test:0"
        assert retrieve["sequence"] == 1
        assert retrieve["start_offset_ms"] <= retrieve["end_offset_ms"]

    def test_writer_resolves_default_log_dir_at_call_time(self, monkeypatch, tmp_path) -> None:
        from src.core import constants

        monkeypatch.setattr(constants, "LOG_DIR", tmp_path)
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.finish_span("pipeline")

        write_pipeline_log(trace, "isolated")

        lines = (tmp_path / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[-1])
        assert "question" not in payload
        assert payload["question_chars"] == len("isolated")
        assert len(payload["question_sha256"]) == 64
        assert stat.S_IMODE((tmp_path / "pipeline.jsonl").stat().st_mode) == 0o600

    def test_ingest_writer_records_join_keys_and_first_error(self, tmp_path) -> None:
        trace = TraceContext(trace_id="ingest-test")
        trace.start_span("fetch_metadata", "metadata")
        trace.finish_span("fetch_metadata")
        trace.start_span("parse", "extract")
        trace.finish_span("parse", "error", error=ValueError("empty document"))

        write_ingest_trace(
            trace,
            file_id=7,
            meeting_id=3,
            terminal_status="error",
            ready_status="error",
            log_dir=tmp_path,
        )

        payload = json.loads(
            (tmp_path / "ingest.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        )
        assert payload["process"] == "ingest"
        assert payload["trace_id"] == "ingest-test"
        assert payload["file_id"] == 7
        assert payload["meeting_id"] == 3
        assert payload["terminal_status"] == "error"
        assert payload["ready_status"] == "error"
        assert payload["error_span"] == "parse"
        assert payload["error_type"] == "ValueError"
        assert [span["sequence"] for span in payload["spans"]] == [0, 1]
        assert stat.S_IMODE((tmp_path / "ingest.jsonl").stat().st_mode) == 0o600

    def test_finish_latest_open_span_preserves_causal_failure(self) -> None:
        trace = TraceContext()
        trace.start_span("fetch_metadata", "metadata")
        trace.finish_span("fetch_metadata")
        trace.start_span("index_meeting", "index")

        assert trace.finish_latest_open_span(error=RuntimeError("vector write failed")) == (
            "index_meeting"
        )
        span = trace.spans[-1]
        assert span.status == "error"
        assert span.error_type == "RuntimeError"

    def test_finish_span_with_error_records_type_and_message(self) -> None:
        trace = TraceContext()
        trace.start_span("generate_answer", "generate")
        trace.finish_span("generate_answer", "error", error=ValueError("API key missing"))

        span = next(s for s in trace.spans if s.label == "generate_answer")
        assert span.status == "error"
        assert span.error_type == "ValueError"
        assert span.error_message == "API key missing"

    def test_to_pipeline_log_surfaces_error_fields(self) -> None:
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.start_span("generate_answer", "generate")
        trace.finish_span(
            "generate_answer",
            "error",
            error=RuntimeError("LLM connection refused"),
        )
        trace.finish_span("pipeline", "error", error=RuntimeError("wrapper"))

        log = trace.to_pipeline_log("What went wrong?")

        assert log["status"] == "error"
        assert "error_span" in log
        # First error span in chronological order is generate_answer
        assert log["error_span"] == "generate_answer"
        assert log["error_type"] == "RuntimeError"
        assert log["error_message"] == "LLM connection refused"

    def test_success_pipeline_has_no_error_fields(self) -> None:
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.finish_span("pipeline")

        log = trace.to_pipeline_log("Hello")
        assert log["status"] == "success"
        assert "error_type" not in log
        assert "error_message" not in log
        assert "error_span" not in log

    def test_timeout_marks_pipeline_log_degraded_without_failing_request(self) -> None:
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.start_span("skill_match", "skill")
        trace.finish_span("skill_match", "timeout", error=TimeoutError("optional fallback"))
        trace.finish_span("pipeline")

        payload = trace.to_dict()
        skill_span = next(span for span in payload["spans"] if span["label"] == "skill_match")
        assert skill_span["status"] == "timeout"
        assert skill_span["error_type"] == "TimeoutError"

        log = trace.to_pipeline_log("q")
        assert log["status"] == "degraded"
        assert log["span_statuses"] == {"skill_match": "timeout"}
        assert log["degraded_spans"] == ["skill_match"]
        assert "error_span" not in log

    def test_long_error_message_is_truncated(self) -> None:
        trace = TraceContext()
        trace.start_span("retrieve", "retrieve")
        huge = "x" * 5000
        trace.finish_span("retrieve", "error", error=Exception(huge))

        span = next(s for s in trace.spans if s.label == "retrieve")
        assert span.error_message is not None
        assert len(span.error_message) <= 501  # 500 + ellipsis char
        assert span.error_message.endswith("…")

    def test_error_without_exception_object_still_sets_status(self) -> None:
        """Back-compat: finish_span(..., 'error') without error kwarg still works."""
        trace = TraceContext()
        trace.start_span("retrieve", "retrieve")
        trace.finish_span("retrieve", "error")

        span = next(s for s in trace.spans if s.label == "retrieve")
        assert span.status == "error"
        assert span.error_type is None
        assert span.error_message is None

        # Pipeline log still marks status=error but without error_type (no info)
        log = trace.to_pipeline_log("q")
        assert log["status"] == "error"
        assert "error_type" not in log


class TestInnerVsWrapperErrorPreference:
    """When both an inner step and the outer pipeline wrapper fail, surface
    the inner (more actionable) error type + message in the log."""

    def test_prefers_inner_step_error_over_pipeline_wrapper(self) -> None:
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.start_span("retrieve", "retrieve")
        trace.finish_span("retrieve", "error", error=ConnectionError("vector store down"))
        # Outer wrapper re-raise — captured too, but should be secondary
        trace.finish_span("pipeline", "error", error=ConnectionError("vector store down"))

        log = trace.to_pipeline_log("q")
        assert log["error_span"] == "retrieve"
        assert log["error_type"] == "ConnectionError"
        assert log["error_message"] == "vector store down"

    def test_falls_back_to_pipeline_wrapper_when_no_inner_error(self) -> None:
        trace = TraceContext()
        trace.start_span("pipeline", "pipeline")
        trace.finish_span("pipeline", "error", error=RuntimeError("wrapper only"))

        log = trace.to_pipeline_log("q")
        assert log["error_span"] == "pipeline"
        assert log["error_type"] == "RuntimeError"
