"""Fail-closed runtime policy for the embedded Chroma deployment.

The application deliberately uses ``PersistentClient`` on the same host as
the API.  It does not expose a Chroma HTTP server and must never load a model
with ``trust_remote_code`` enabled.  This module centralises those invariants
so startup and every vector-store probe enforce the same policy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import settings
from .constants import DATA_DIR

logger = logging.getLogger(__name__)


def validate_chroma_runtime(*, persist_dir: str | Path | None = None) -> Path:
    """Validate the local-only Chroma policy and return its canonical path.

    A production vector database must live below ``DATA_DIR`` so a compromised
    configuration cannot redirect the embedded database to an arbitrary host
    path.  Development/test paths remain configurable for isolated fixtures.
    """
    if getattr(settings, "CHROMA_REMOTE_ENABLED", False):
        raise RuntimeError(
            "Remote Chroma clients are disabled; use the embedded PersistentClient deployment"
        )
    if getattr(settings, "CHROMA_TRUST_REMOTE_CODE", False):
        raise RuntimeError("Chroma model loading with trust_remote_code is disabled")

    candidate = Path(persist_dir) if persist_dir is not None else settings.VECTOR_DB_DIR
    resolved = candidate.expanduser().resolve(strict=False)
    if settings.ENVIRONMENT != "dev":
        data_root = Path(DATA_DIR).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(data_root)
        except ValueError as exc:
            raise RuntimeError(
                f"VECTOR_DB_DIR must be inside DATA_DIR in {settings.ENVIRONMENT}: "
                f"{resolved} is outside {data_root}"
            ) from exc
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def configure_chroma_environment() -> None:
    """Disable Chroma telemetry and assert the local-only policy at startup."""
    import os

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "FALSE")
    validate_chroma_runtime()
    logger.info(
        "Chroma local-only policy enabled (persist_dir=%s, telemetry=disabled)",
        settings.VECTOR_DB_DIR,
    )
