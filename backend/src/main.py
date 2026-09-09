"""FastAPI application entry point"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .api import register_routers
from .api.lifespan import lifespan
from .api.metrics import router as metrics_router
from .api.middleware import setup_middleware, setup_rate_limiter
from .core.config import settings
from .core.logging import configure_logging

# Initialize structured logging (JSON when LOG_FORMAT=json, text otherwise)
configure_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Meeting Agent API",
    description="Upload meeting recordings/PDFs/PPTs and ask questions via RAG",
    version="0.1.0",
    lifespan=lifespan,
)

setup_rate_limiter(app)
setup_middleware(app)

# Register all routers under /api/v1
register_routers(app)

# Metrics endpoint (authenticated, Prometheus format)
app.include_router(metrics_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    from .models.schemas import ErrorResponse

    request_id = getattr(request.state, "request_id", "unknown")
    structured = exc.detail if isinstance(exc.detail, dict) else None
    detail_str = (
        str(structured.get("message", ""))
        if structured is not None
        else (str(exc.detail) if exc.detail is not None else "")
    )
    body = ErrorResponse(
        code=(
            str(structured.get("code", f"HTTP_{exc.status_code}"))
            if structured is not None
            else f"HTTP_{exc.status_code}"
        ),
        message=detail_str,
        request_id=request_id,
        details=(structured.get("details") if structured is not None else None),
    ).model_dump()
    # Backward-compatible field for clients/tests that still read `detail`.
    body["detail"] = detail_str
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError):
    """Demote ``RuntimeError("Stream consumed")`` (and similar request-stream
    glitches from malformed multipart bodies) to a 400.

    FastAPI / Starlette's multipart parser can leave an UploadFile in a
    "consumed" state when a client sends a mismatched-boundary or otherwise
    truncated body; reading or seeking it from a route handler raises
    ``RuntimeError`` that escapes the handler's own try/except. Treat that
    as a client error rather than a 500 so the contract suite (and any
    monitoring) doesn't flag it as a server bug.
    """
    from .models.schemas import ErrorResponse

    msg = str(exc)
    if "Stream consumed" not in msg and "Stream is consumed" not in msg:
        # Not the multipart case — fall through to the generic handler so the
        # server keeps logging unexpected RuntimeErrors as 500.
        return await generic_exception_handler(request, exc)

    request_id = getattr(request.state, "request_id", "unknown")
    detail_str = f"Malformed request body: {msg}"
    body = ErrorResponse(
        code="HTTP_400",
        message=detail_str,
        request_id=request_id,
        details=None,
    ).model_dump()
    body["detail"] = detail_str
    return JSONResponse(status_code=400, content=body)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    from .models.schemas import ErrorResponse

    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("Unhandled exception for request %s", request_id)
    exc_type = type(exc).__name__
    details: dict | None = None
    if settings.ENVIRONMENT == "dev":
        import re

        raw_msg = str(exc)[:500]
        # Strip file-system paths to avoid leaking server internals
        safe_msg = re.sub(r"(?:/[\w.-]+)+", "<path>", raw_msg)
        details = {"type": exc_type, "message": safe_msg}
    body = ErrorResponse(
        code="INTERNAL_ERROR",
        message=f"Internal error: {exc_type}",
        request_id=request_id,
        details=details,
    ).model_dump()
    body["detail"] = body["message"]
    return JSONResponse(status_code=500, content=body)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
