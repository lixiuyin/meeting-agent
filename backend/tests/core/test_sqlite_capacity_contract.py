"""Isolated component load tests; these do not certify end-to-end LLM capacity."""

import asyncio
import math
import time

import pytest

from src.api.routers.memory import query_recorded_facts
from src.core import database as db
from src.models.schemas.fact_query import FactQueryRequest


@pytest.mark.parametrize("concurrency", [1, 5, 10, 20])
@pytest.mark.asyncio
async def test_sqlite_mixed_fact_reads_and_writes(concurrency, record_property):
    user = f"capacity-{concurrency}"
    latencies = []

    async def worker(number):
        for turn in range(4):
            started = time.monotonic()

            def write(turn=turn):
                with db.get_write_connection() as conn:
                    db.set_memory(
                        conn,
                        user_id=user,
                        key=f"task.{number}.{turn}",
                        value="open",
                        fact_type="action_item",
                        project_id="capacity",
                    )

            await asyncio.to_thread(write)
            result = await query_recorded_facts(
                FactQueryRequest(project_id="capacity", limit=25), {"user_id": user}
            )
            assert result.returned == len(result.items) <= result.total
            assert all(item.project_id == "capacity" for item in result.items)
            latencies.append(time.monotonic() - started)

    await asyncio.wait_for(asyncio.gather(*(worker(i) for i in range(concurrency))), timeout=60)
    final = await query_recorded_facts(FactQueryRequest(limit=100), {"user_id": user})
    assert final.total == concurrency * 4
    record_property("component", "sqlite_fact_reads_writes_no_llm")
    record_property("concurrency", concurrency)
    record_property("operations", len(latencies))
    record_property("p95_seconds", sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1])
