"""Tests for OpenTelemetry tracing integration.

Verifies that the tracing module correctly records spans when a
TracerProvider with an in-memory exporter is installed, and that the
``otel_span`` helper context-manager produces observable spans.
"""

import pytest

# Skip the entire module when OpenTelemetry SDK is not installed.
pytest.importorskip("opentelemetry.sdk.trace")

from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from src.core.tracing import otel_span, setup_tracing
from src.main import app

# ---------------------------------------------------------------------------
# In-memory span exporter (lightweight, no extra dependency)
# ---------------------------------------------------------------------------


class _InMemorySpanExporter(SpanExporter):
    """Collects finished spans in a list for test assertions."""

    def __init__(self) -> None:
        self._finished_spans: list[ReadableSpan] = []

    def export(self, spans: list[ReadableSpan]) -> None:
        self._finished_spans.extend(spans)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def get_finished_spans(self) -> list[ReadableSpan]:
        """Return all spans that have been exported so far."""
        return list(self._finished_spans)

    def clear(self) -> None:
        """Drop all recorded spans (useful between tests)."""
        self._finished_spans.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def span_exporter():
    """Create an in-memory exporter and install it as the global provider.

    The original ``TracerProvider`` (if any) is saved and restored on
    teardown so that tests remain isolated from one another and from the
    application's own provider.
    """
    exporter = _InMemorySpanExporter()
    resource = Resource.create({"service.name": "meeting-agent-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Save and replace the global tracer provider.
    previous_provider = trace.get_tracer_provider()
    # Reset the internal Once lock so we can override the provider across tests.
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    # Instrument the already-imported FastAPI app so request spans are captured.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument_app(app)  # type: ignore[attr-defined]
    except ImportError:
        pass

    yield exporter

    # Restore the previous provider so other tests are unaffected.
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()  # type: ignore[attr-defined]
    trace.set_tracer_provider(previous_provider)


@pytest.fixture()
def client():
    """Async test client backed by the real FastAPI app (no live server)."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTracing:
    """Verify spans are recorded through the OpenTelemetry pipeline."""

    @pytest.mark.asyncio
    async def test_health_endpoint_records_span(
        self,
        span_exporter: _InMemorySpanExporter,
        client: AsyncClient,
        monkeypatch,
    ) -> None:
        """A request to ``/api/v1/health`` should produce at least one span."""
        from src.api.routers import health as health_router

        async def ready():
            return health_router.HealthResponse(status="ok", checks={"startup": "ok"})

        # This is a tracing test, so keep it hermetic and independent of live
        # embedding/provider connectivity.
        monkeypatch.setattr(health_router, "_check_readiness", ready)
        async with client as c:
            response = await c.get("/api/v1/health")

        assert response.status_code == 200
        spans = span_exporter.get_finished_spans()
        assert len(spans) >= 1, (
            f"Expected at least one span after /health request, got {len(spans)}"
        )

    @pytest.mark.asyncio
    async def test_otel_span_records_span(self, span_exporter: _InMemorySpanExporter) -> None:
        """``otel_span`` should record a named span with attributes."""
        attributes = {"test.key": "value", "test.count": 42}

        with otel_span("test.manual-span", attributes):
            pass  # no-op body

        spans = span_exporter.get_finished_spans()
        assert len(spans) >= 1, f"Expected at least one span from otel_span, got {len(spans)}"

        manual_spans = [s for s in spans if s.name == "test.manual-span"]
        assert len(manual_spans) == 1, (
            f"Expected exactly one span named 'test.manual-span', found: {[s.name for s in spans]}"
        )
        span = manual_spans[0]
        assert span.attributes is not None
        assert span.attributes.get("test.key") == "value"
        assert span.attributes.get("test.count") == 42


class TestSetupTracing:
    """Tests for ``setup_tracing()`` idempotency and guard conditions."""

    def test_setup_tracing_returns_false_without_endpoint(self) -> None:
        """When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is not set, tracing is disabled."""
        import os

        previous = os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        try:
            # Reset the module-level flag so the guard re-evaluates.
            import src.core.tracing as tracing_mod

            tracing_mod._initialized = False
            result = setup_tracing()
            assert result is False
        finally:
            if previous is not None:
                os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = previous
            tracing_mod._initialized = False
