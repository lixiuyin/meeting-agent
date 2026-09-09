"""Execute the report's actual PromQL against synthetic latency buckets."""

import os
import shutil
import subprocess

import pytest
import yaml

from scripts.slo_report import build_queries


def test_report_p95_includes_slow_stream_without_fast_sync_dilution(tmp_path):
    executable = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not executable:
        pytest.skip("promtool is required for real PromQL evaluation")
    series = []
    for endpoint, rate in (("/api/v1/chat", 1000), ("/api/v1/chat/stream", 100)):
        for boundary in ("3", "5", "+Inf"):
            increment = 0 if endpoint.endswith("/stream") and boundary == "3" else rate
            series.append(
                {
                    "series": 'chat_completion_duration_seconds_bucket{job="meeting-agent-backend",endpoint="'
                    + endpoint
                    + '",le="'
                    + boundary
                    + '"}',
                    "values": f"0+{increment}x35",
                }
            )
    document = {
        "rule_files": [],
        "evaluation_interval": "1m",
        "tests": [
            {
                "interval": "1m",
                "input_series": series,
                "promql_expr_test": [
                    {
                        "expr": build_queries()["chat_p95_seconds"],
                        "eval_time": "30m",
                        "exp_samples": [{"labels": "{}", "value": 4.9}],
                    }
                ],
            }
        ],
    }
    spec = tmp_path / "slo.yaml"
    spec.write_text(yaml.safe_dump(document))
    result = subprocess.run(
        [executable, "test", "rules", str(spec)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
