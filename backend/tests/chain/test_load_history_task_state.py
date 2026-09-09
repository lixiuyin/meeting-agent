"""Task-state compatibility regressions for historical session continuation."""

import json
from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from src.services.chain import PipelineContext
from src.services.chain._steps_context import load_history


@pytest.mark.asyncio
async def test_load_history_restores_current_v3_task_state(monkeypatch) -> None:
    state = {
        "schema_version": 3,
        "root_objective": "Audit every Orbit action item",
        "objective": "Audit every Orbit action item",
        "open_questions": ["Which actions are overdue?"],
        "turn_count": 4,
    }
    ctx = PipelineContext(
        question="Continue",
        session_id="resume-v3",
        user_id="u1",
        raw_history_messages=[],
    )
    monkeypatch.setattr(
        "src.services.chain._steps_context.db.get_connection", lambda: nullcontext(None)
    )
    monkeypatch.setattr(
        "src.services.chain._steps_context.db.get_session",
        lambda *_args, **_kwargs: {"task_state_json": json.dumps(state)},
    )
    monkeypatch.setattr(
        "src.services.memory._history_context.load_incremental_history",
        AsyncMock(return_value=None),
    )

    await load_history(ctx)

    assert ctx.session_task_state == state
