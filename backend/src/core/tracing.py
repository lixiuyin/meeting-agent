"""OpenTelemetry tracing configuration.

Enabled via the ``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable.
When unset, all tracing is no-op with zero overhead.

Instrumented components:
    - FastAPI (request spans)
    - httpx (outbound HTTP calls)

Explicitly **not** instrumented:
    - sqlite3 — would pollute span context around the write lock serialization
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_initialized = False


@contextmanager
def otel_span(
    name: str,
    attributes: dict[str, str | int | float | bool] | None = None,
) -> Iterator[None]:
    """Start an OpenTelemetry span when tracing is enabled; otherwise no-op."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("meeting-agent")
    except Exception:
        logger.debug("OTel span creation failed for '%s'", name, exc_info=True)
        yield
        return

    # Do not catch exceptions raised by the instrumented application block.
    # The former broad try/except yielded a second time after an application
    # error, masking the real failure as "generator didn't stop after throw()".
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield


def setup_tracing() -> bool:
    """Initialize OpenTelemetry if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured.

    Returns True if tracing was enabled, False otherwise.
    Safe to call multiple times (idempotent).
    """
    global _initialized
    if _initialized:
        return False

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.debug("OTel tracing disabled (OTEL_EXPORTER_OTLP_ENDPOINT not set)")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "meeting-agent"),
                "service.version": os.getenv("OTEL_SERVICE_VERSION", "0.1.0"),
            }
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)

        # Instrument FastAPI (deferred — will be applied when app is ready)
        _instrument_fastapi()
        _instrument_httpx()

        _initialized = True
        logger.info("OpenTelemetry tracing enabled (endpoint=%s)", endpoint)
        return True

    except ImportError as exc:
        logger.warning("OpenTelemetry packages not installed: %s. Tracing disabled.", exc)
        return False
    except Exception as exc:
        logger.warning("Failed to initialize OpenTelemetry: %s. Tracing disabled.", exc)
        return False


def _instrument_fastapi() -> None:
    """Instrument FastAPI application for automatic request spans."""
    try:
        from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingImports] - optional extra
            FastAPIInstrumentor,
        )

        FastAPIInstrumentor.instrument()  # type: ignore[attr-defined]
        logger.debug("FastAPI instrumentation enabled")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-fastapi not installed, skipping")
    except Exception as exc:
        logger.warning("FastAPI instrumentation failed: %s", exc, exc_info=True)


def _instrument_httpx() -> None:
    """Instrument httpx for outbound HTTP request spans."""
    try:
        from opentelemetry.instrumentation.httpx import (  # pyright: ignore[reportMissingImports] - optional extra
            HTTPXClientInstrumentor,
        )

        HTTPXClientInstrumentor().instrument()  # type: ignore[attr-defined]
        logger.debug("httpx instrumentation enabled")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-httpx not installed, skipping")
    except Exception as exc:
        logger.warning("httpx instrumentation failed: %s", exc, exc_info=True)
