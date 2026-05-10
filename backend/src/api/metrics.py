"""Operational metrics endpoint — Prometheus exposition format."""

import hmac
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from prometheus_client import generate_latest

from ..core.config import settings

router = APIRouter(tags=["ops"])
logger = logging.getLogger(__name__)


async def _verify_metrics_auth(
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> None:
    """Prefer PROMETHEUS_API_KEY for scraper access so operators can rotate
    the scraper token independently of the main API key.  Falls back to the
    regular API key validation when PROMETHEUS_API_KEY is not configured.
    In production, rejects requests when no key is configured at all.
    """
    scraper_key = os.getenv("PROMETHEUS_API_KEY", "").strip()
    if scraper_key:
        if not x_api_key or not hmac.compare_digest(x_api_key, scraper_key):
            raise HTTPException(status_code=401, detail="Invalid scraper credentials")
        return
    # Fallback: use standard API key validation
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        # Fail-closed in production: no key configured means no access
        if settings.ENVIRONMENT == "production":
            raise HTTPException(
                status_code=401,
                detail="Metrics endpoint requires authentication. "
                "Configure PROMETHEUS_API_KEY or API_KEY.",
            )
        return  # dev mode — no auth
    if not x_api_key or not hmac.compare_digest(x_api_key, configured_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )


@router.get("/metrics", dependencies=[Depends(_verify_metrics_auth)])
async def metrics() -> Response:
    """Prometheus-format metrics (requires scraper or API key auth)."""
    body = generate_latest()
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
