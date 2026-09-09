"""Operational metrics endpoint — Prometheus exposition format."""

import hmac
import logging
import os
import shutil

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from prometheus_client import generate_latest

from ..core.config import settings

router = APIRouter(tags=["ops"])
logger = logging.getLogger(__name__)


def _refresh_point_in_time_metrics() -> None:
    """Refresh gauges whose values must be current at scrape time."""
    try:
        from ..core.constants import DATA_DIR
        from ..core.database import get_connection
        from ..core.metrics import (
            APP_DATA_DISK_USAGE_RATIO,
            DURABLE_JOB_EXPIRED_RUNNING,
            DURABLE_JOB_OLDEST_EXPIRED_SECONDS,
            DURABLE_JOBS,
            INDEX_REPAIR_PENDING,
            PENDING_VECTOR_DELETION_JOBS,
        )

        with get_connection() as conn:
            counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT COALESCE(status, 'pending') AS status, COUNT(*) AS count "
                    "FROM pending_vector_deletions GROUP BY COALESCE(status, 'pending')"
                ).fetchall()
            }
            from ..core.database import job_health_stats

            durable_counts = job_health_stats(conn)
        for status in ("pending", "dead_letter"):
            PENDING_VECTOR_DELETION_JOBS.labels(status=status).set(counts.get(status, 0))
        for status in ("pending", "running", "completed", "dead_letter", "cancelled"):
            DURABLE_JOBS.labels(status=status).set(durable_counts.get(status, 0))
        DURABLE_JOB_EXPIRED_RUNNING.set(durable_counts.get("expired_running", 0))
        DURABLE_JOB_OLDEST_EXPIRED_SECONDS.set(durable_counts.get("oldest_expired_seconds", 0))
        with get_connection() as conn:
            repair_count = conn.execute(
                "SELECT COUNT(*) FROM index_state ist "
                "JOIN meeting_files mf ON mf.id=ist.file_id "
                "WHERE mf.status='ready' AND ist.repair_pending=1"
            ).fetchone()[0]
        INDEX_REPAIR_PENDING.set(repair_count)
        usage = shutil.disk_usage(DATA_DIR)
        APP_DATA_DISK_USAGE_RATIO.set(usage.used / usage.total if usage.total else 0)
    except Exception:
        logger.debug("Failed to refresh point-in-time metrics", exc_info=True)


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
    _refresh_point_in_time_metrics()
    body = generate_latest()
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
