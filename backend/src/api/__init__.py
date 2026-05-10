"""API layer - registers all routers from the routers subpackage"""

from fastapi import FastAPI

from ..models.schemas import ErrorResponse
from .routers import (
    chat,
    file_download,
    health,
    meetings,
    memory,
    sessions,
    settings,
    skills,
    websocket,
)

# Common error responses surfaced by FastAPI/middleware/handlers across most
# routes. Declared once here so the OpenAPI schema documents them on every
# endpoint — schemathesis enforces ``status_code_conformance`` and would
# otherwise flag them as undocumented when fuzzing returns 4xx/5xx codes.
_COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Bad Request"},
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not Found"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    429: {"model": ErrorResponse, "description": "Too Many Requests"},
    500: {"model": ErrorResponse, "description": "Internal Server Error"},
}


def register_routers(app: FastAPI) -> None:
    """Register all API routers on the FastAPI app under /api/v1"""
    prefix = "/api/v1"
    # file_download must be registered before meetings so its GET file-download
    # endpoint (with dual header+token auth) takes precedence over the meetings
    # router's auth-only endpoint for the same path.
    app.include_router(file_download.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(meetings.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(chat.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(sessions.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(memory.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(health.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(settings.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(skills.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
    app.include_router(websocket.router, prefix=prefix, responses=_COMMON_ERROR_RESPONSES)
