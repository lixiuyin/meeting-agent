from scripts.slo_report import WINDOW_SECONDS, assess


def test_short_history_does_not_report_perfect_slo():
    result = assess(
        {"scrapes": 120, "availability": 1, "chat_p95_seconds": 0.1, "target_uptime": 1}
    )
    assert result["chat_slo_passed"] is None and result["chat_availability"] is None


def test_new_metrics_under_old_monitoring_history_are_not_30_day_evidence():
    assert (
        assess(
            {
                "scrapes": WINDOW_SECONDS / 30,
                "availability": 1,
                "chat_p95_seconds": 0.1,
                "target_uptime": 1,
            }
        )["chat_slo_passed"]
        is None
    )


def test_full_history_still_requires_latency_and_uptime_targets():
    values = {
        "scrapes": WINDOW_SECONDS / 30,
        "metric_present_30d_ago": 1,
        "availability": 1,
        "chat_p95_seconds": 2.9,
        "target_uptime": 1,
    }
    assert assess(values)["chat_slo_passed"] is True
    assert assess({**values, "chat_p95_seconds": 3.1})["chat_slo_passed"] is False
    assert assess({**values, "target_uptime": 0.99})["chat_slo_passed"] is False
    assert assess({**values, "chat_p95_seconds": None})["chat_slo_passed"] is None
