"""Regression tests for supervised background-task restart behavior."""

import asyncio
from unittest.mock import patch

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_supervised_task_records_error_type_and_recovers():
    from src.utils.supervised_task import create_supervised_task

    calls = 0

    async def worker() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")

    with patch("src.utils.supervised_task.BACKGROUND_TASK_FAILURES_TOTAL") as failures:
        task = create_supervised_task(
            "reconciler",
            worker,
            max_restarts=1,
            base_backoff_seconds=0,
        )
        await task

    assert calls == 2
    failures.labels.assert_called_once_with(name="reconciler", error_type="RuntimeError")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_supervised_task_records_exhaustion_without_masking_original_error():
    from src.utils.supervised_task import create_supervised_task

    async def worker() -> None:
        raise LookupError("permanent")

    with (
        patch("src.utils.supervised_task.BACKGROUND_TASK_FAILURES_TOTAL") as failures,
        patch("src.utils.supervised_task.BACKGROUND_TASK_EXHAUSTED_TOTAL") as exhausted,
    ):
        task = create_supervised_task("worker", worker, max_restarts=0)
        with pytest.raises(LookupError, match="permanent"):
            await task

    failures.labels.assert_called_once_with(name="worker", error_type="LookupError")
    exhausted.labels.assert_called_once_with(name="worker")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_metric_failure_does_not_disable_restart():
    from src.utils.supervised_task import create_supervised_task

    calls = 0

    async def worker() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")

    with patch("src.utils.supervised_task.BACKGROUND_TASK_FAILURES_TOTAL") as failures:
        failures.labels.side_effect = ValueError("bad metric")
        await create_supervised_task(
            "metric-safe",
            worker,
            max_restarts=1,
            base_backoff_seconds=0,
        )

    assert calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_deduplicates_an_active_named_task():
    from src.utils.supervised_task import BackgroundTaskRegistry

    registry = BackgroundTaskRegistry()
    release = asyncio.Event()
    calls = 0

    async def worker() -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    first = registry.create("same-job", worker)
    second = registry.create("same-job", worker)

    assert second is first
    assert registry.active_count == 1
    release.set()
    await first
    await asyncio.sleep(0)
    assert calls == 1
    assert registry.active_count == 0
