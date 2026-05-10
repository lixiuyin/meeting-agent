"""Verify pipeline trace captures exception type + message.

Before this change ``pipeline.jsonl`` only contained ``"status": "error"``
with no hint of what went wrong, making operational debugging painful.
"""

from __future__ import annotations

from src.core.trace import TraceContext


class TestTraceErrorCapture:
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
