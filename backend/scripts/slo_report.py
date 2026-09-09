"""Read a 30-day Prometheus chat SLO report without treating missing history as success."""

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

WINDOW_SECONDS = 30 * 24 * 3600


def assess(values: dict, scrape_interval: int = 30) -> dict:
    coverage = min(1.0, (values.get("scrapes") or 0) * scrape_interval / WINDOW_SECONDS)
    complete = bool(values.get("metric_present_30d_ago")) and coverage >= 0.995
    available = values.get("availability")
    latency = values.get("chat_p95_seconds")
    uptime = values.get("target_uptime")
    measurable = complete and all(value is not None for value in (available, latency, uptime))
    return {
        "window_days": 30,
        "scrape_coverage": coverage,
        "history_complete": complete,
        "chat_availability": available if measurable else None,
        "chat_p95_seconds": latency if measurable else None,
        "target_uptime": uptime if measurable else None,
        "chat_slo_passed": (available >= 0.995 and latency < 3 and uptime >= 0.995)
        if measurable
        else None,
        "release_ready": False,
        "limitations": [
            "single target; missing or short history is not a passing score",
            "does not certify ingestion quality, user experience, or full release acceptance",
        ],
    }


def build_queries() -> dict[str, str]:
    return {
        "scrapes": 'min(count_over_time(up{job="meeting-agent-backend"}[30d]))',
        "metric_present_30d_ago": (
            'count(chat_completion_total{job="meeting-agent-backend"} offset 30d)'
        ),
        "target_uptime": 'min(avg_over_time(up{job="meeting-agent-backend"}[30d]))',
        "availability": (
            '1 - ((sum(increase(chat_completion_total{job="meeting-agent-backend",'
            'outcome!="success"}[30d])) or (0 * sum(increase(chat_completion_total'
            '{job="meeting-agent-backend"}[30d])))) / sum(increase(chat_completion_total'
            '{job="meeting-agent-backend"}[30d])))'
        ),
        "chat_p95_seconds": (
            "max(histogram_quantile(0.95, sum by (le, endpoint) (increase("
            'chat_completion_duration_seconds_bucket{job="meeting-agent-backend",'
            'endpoint=~"/api/v1/chat(/stream)?"}[30d]))))'
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if urllib.parse.urlsplit(args.prometheus_url).scheme not in {"http", "https"}:
        parser.error("Prometheus URL must use HTTP or HTTPS")
    instant = time.time()
    queries = build_queries()
    values = {}
    for key, query in queries.items():
        url = (
            args.prometheus_url.rstrip("/")
            + "/api/v1/query?"
            + urllib.parse.urlencode({"query": query, "time": instant})
        )
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.load(response)
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {key}")
        rows = payload["data"]["result"]
        value = float(rows[0]["value"][1]) if len(rows) == 1 else None
        values[key] = value if value is not None and math.isfinite(value) else None
    result = {**assess(values), "evaluated_at_unix": instant, "observations": values}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["chat_slo_passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
