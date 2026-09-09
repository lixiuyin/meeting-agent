"""FastAPI middleware and request tracing."""

import hashlib
import ipaddress
import logging
import os
import time
import uuid

from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prometheus HTTP latency tracking
# ---------------------------------------------------------------------------

_METRICS_ENABLED = False


def _init_metrics() -> None:
    """Lazy-init flag to avoid import errors when prometheus_client is missing."""
    global _METRICS_ENABLED
    try:
        from ..core.metrics import HTTP_REQUEST_DURATION  # noqa: F401

        _METRICS_ENABLED = True
    except ImportError:
        _METRICS_ENABLED = False


def _get_rate_limit_key(request: Request) -> str:
    """Determine rate-limit key: use API key when auth is configured, else client IP.

    This ensures each API consumer gets an independent quota rather than
    sharing a limit with everyone behind the same proxy/NAT.
    """
    # When auth is enabled, rate-limit by the API key presented
    if settings.API_KEY.get_secret_value():
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"key:{hashlib.sha256(api_key.encode()).hexdigest()}"

    # Fall back to client IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        raw_proxies = [p.strip() for p in settings.TRUSTED_PROXIES.split(",") if p.strip()]
        # "0.0.0.0" here is a sentinel for missing client info, not a bind address.
        client = request.client.host if request.client else "0.0.0.0"  # nosec B104
        if _is_trusted_proxy(client, raw_proxies):
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"  # nosec B104


def _is_trusted_proxy(client_ip: str, trusted: list[str]) -> bool:
    """Check if client IP matches any trusted proxy (exact IP or CIDR)."""
    if not trusted:
        return False
    try:
        client_addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if "/" in entry:
                if client_addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == entry:
                return True
        except ValueError:
            continue
    return False


# Module-level limiter instance — routers import this to apply per-endpoint limits.
# Initialized with the rate-limit key function; default 60/min applies to all endpoints
# that don't have an explicit @limiter.limit() or @limiter.exempt decorator.
_disable_rl_raw = os.getenv("DISABLE_RATE_LIMIT", "").strip().lower()
_rate_limit_enabled = _disable_rl_raw not in {"1", "true", "yes"}
if _disable_rl_raw in {"1", "true", "yes"} and os.getenv("ENVIRONMENT", "dev").lower() not in {
    "dev",
    "development",
    "test",
    "testing",
}:
    raise RuntimeError(
        "DISABLE_RATE_LIMIT cannot be set in non-dev environments. "
        f"Got ENVIRONMENT={os.getenv('ENVIRONMENT', 'unknown')!r}."
    )
limiter = Limiter(
    key_func=_get_rate_limit_key,
    default_limits=["60/minute"],
    enabled=_rate_limit_enabled,
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Add a unique request_id to every request for log tracing."""

    async def dispatch(self, request: Request, call_next):
        raw_request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:16])
        # Sanitize to prevent log injection and cap length
        request_id = "".join(c for c in raw_request_id if c.isalnum() or c in "-_")[:32]
        if not request_id:
            request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )

        # Record Prometheus HTTP latency histogram
        if _METRICS_ENABLED:
            try:
                from ..core.metrics import HTTP_REQUEST_DURATION

                route = request.scope.get("route")
                metric_path = getattr(route, "path", None) or "unmatched"

                HTTP_REQUEST_DURATION.labels(
                    method=request.method,
                    path=metric_path,
                    status=str(response.status_code),
                ).observe(time.monotonic() - start)
            except Exception:
                logger.debug("Failed to record HTTP metrics", exc_info=True)

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not settings.SECURITY_HEADERS_ENABLED:
            return response
        response.headers["Strict-Transport-Security"] = (
            f"max-age={settings.SECURITY_HSTS_MAX_AGE}; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = settings.SECURITY_FRAME_OPTIONS
        response.headers["Referrer-Policy"] = settings.SECURITY_REFERRER_POLICY
        response.headers["Content-Security-Policy"] = settings.SECURITY_CSP
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        return response


class SupportedContentTypeMiddleware(BaseHTTPMiddleware):
    """Reject form encoding the API does not support before Starlette parses it."""

    async def dispatch(self, request: Request, call_next):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/x-www-form-urlencoded":
            return JSONResponse(
                status_code=415,
                content={
                    "detail": (
                        "application/x-www-form-urlencoded is not supported; "
                        "use JSON or multipart/form-data"
                    )
                },
            )
        return await call_next(request)


class IdempotencyReservationMiddleware(BaseHTTPMiddleware):
    """Always release a mutation reservation when its endpoint did not complete it."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        finally:
            guard = getattr(request.state, "idempotency_guard", None)
            if guard is not None:
                try:
                    await guard.abandon()
                except Exception:
                    logger.exception("Failed to release unfinished idempotency reservation")


class RequestIdFilter(logging.Filter):
    """Inject request_id into log records."""

    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


def setup_middleware(app) -> None:
    """Register middleware on the FastAPI app."""
    # Attach request_id filter to root logger (moved from module-level to avoid import side effects)
    logging.getLogger().addFilter(RequestIdFilter())

    # Initialize Prometheus metrics (lazy, no-op if prometheus_client missing)
    _init_metrics()

    # Request ID tracing
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(IdempotencyReservationMiddleware)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SupportedContentTypeMiddleware)

    # Trusted hosts (blocks Host header attacks / DNS rebinding)
    trusted_hosts = [h.strip() for h in settings.TRUSTED_HOSTS.split(",") if h.strip()]
    if trusted_hosts:
        from fastapi.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
        logger.info("TrustedHostMiddleware configured: %s", trusted_hosts)

    # CORS — disallow wildcard origins with credentials
    from fastapi.middleware.cors import CORSMiddleware

    _origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if "*" in _origins:
        if settings.ENVIRONMENT not in ("dev", "development"):
            raise ValueError(
                "CORS wildcard '*' is not allowed in production. "
                "Set explicit allowed origins in CORS_ORIGINS."
            )
        logger.warning(
            "CORS wildcard '*' detected in dev mode; removing it because credentials are enabled."
        )
        _origins = [o for o in _origins if o != "*"]

    # In dev mode with no configured origins, provide sensible defaults
    if not _origins and settings.ENVIRONMENT == "dev":
        _origins = ["http://localhost:8307"]
        logger.info("CORS: no origins configured in dev mode, using defaults %s", _origins)

    # CORS origins are validated in Settings.model_post_init() for non-dev environments.
    # This redundant guard is kept as a belt-and-suspenders check during middleware setup.

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=bool(_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "X-API-Key",
            "Content-Type",
            "X-Request-ID",
            "Idempotency-Key",
            "Last-Event-ID",
        ],
        expose_headers=["X-Run-ID", "X-Request-ID", "Retry-After"],
    )
    logger.info("CORS configured: origins=%s", _origins if _origins else "[] (deny all)")

    if _METRICS_ENABLED:
        from .slo_metrics import ChatCompletionMetricsMiddleware

        app.add_middleware(ChatCompletionMetricsMiddleware)


async def _rate_limit_envelope_handler(request: Request, exc: RateLimitExceeded):
    """429 handler that returns the unified ErrorResponse envelope.

    slowapi's default handler emits ``{"error": "Rate limit exceeded: ..."}``,
    which does not satisfy the OpenAPI ``ErrorResponse`` schema (``code`` /
    ``message`` / ``request_id`` required) declared on every router. Wrap
    the underlying response with envelope fields while keeping the
    rate-limit metadata headers slowapi attaches (``Retry-After``,
    ``X-RateLimit-*``).

    DO NOT copy ``Content-Length`` / ``Content-Type`` / ``Transfer-Encoding``
    from slowapi's response — its body is the ~48-byte ``{"error": ...}``
    string, while our envelope is larger. A stale ``Content-Length`` truncates
    the response and the client sees ``Connection broken: IncompleteRead``.
    """
    from fastapi.responses import JSONResponse

    from ..models.schemas import ErrorResponse

    base_response = _rate_limit_exceeded_handler(request, exc)
    request_id = getattr(request.state, "request_id", "unknown")
    detail_str = f"Rate limit exceeded: {exc.detail}" if exc.detail else "Rate limit exceeded"
    body = ErrorResponse(
        code="HTTP_429",
        message=detail_str,
        request_id=request_id,
        details=None,
    ).model_dump()
    body["detail"] = detail_str
    body["error"] = detail_str  # backward-compat for legacy clients

    # Forward only the rate-limit metadata headers; let JSONResponse compute
    # its own Content-Length / Content-Type for the new (larger) envelope.
    _RATE_HEADER_PREFIXES = ("retry-after", "x-ratelimit-", "x-rate-limit-")
    forwarded_headers = {
        name: value
        for name, value in base_response.headers.items()
        if name.lower().startswith(_RATE_HEADER_PREFIXES)
    }
    return JSONResponse(
        status_code=429,
        content=body,
        headers=forwarded_headers,
    )


def setup_rate_limiter(app) -> None:
    """Register rate limiter on the FastAPI app."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_envelope_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)
