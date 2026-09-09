"""Deterministic fault runners for process-quality evaluation.

All fixtures live under ``bench_environment`` paths. Provider-facing work is
replaced with deterministic local doubles while the real ingest/chat pipeline
and target step execute their normal error handling and trace attribution.
"""

from __future__ import annotations

from contextlib import ExitStack, suppress
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from ._bench_process import execute_failure_cases

_FAULT_TEXT = (
    "Deterministic benchmark content with sufficient length for process fault "
    "attribution and no external provider dependency."
)


class _StaticTextProcessor:
    async def process(self, _ctx):
        from src.services.processor._processors._types import FileArtefact

        return FileArtefact(
            text=_FAULT_TEXT,
            structured_json=None,
            structured_kind=None,
            metrics={"word_count": len(_FAULT_TEXT.split())},
            parsed_doc=None,
        )


class _FailingTextProcessor:
    async def process(self, _ctx):
        raise RuntimeError("injected extraction failure")


class _DeterministicLLM(BaseChatModel):
    answer: str = "Deterministic cited benchmark answer [1]."

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])

    @property
    def _llm_type(self) -> str:
        return "process-benchmark"

    @property
    def _identifying_params(self) -> dict:
        return {}


def _failure_payload(trace) -> dict:
    payload = trace.to_dict()
    error_spans = [
        span
        for span in payload["spans"]
        if isinstance(span, dict) and span.get("status") == "error"
    ]
    causal_spans = [span for span in error_spans if span.get("label") != "pipeline"]
    candidates = causal_spans or error_spans
    candidates.sort(key=lambda span: (span.get("sequence", 10**9), span.get("start_offset_ms", 0)))
    error_span = candidates[0] if candidates else None
    return {
        **payload,
        "terminal_status": "error" if error_span else "success",
        "error_span": error_span.get("label") if error_span else None,
        "error_type": error_span.get("error_type") if error_span else None,
    }


async def capture_process_failure_traces(expectations: dict, benchmark_root: Path) -> list[dict]:
    """Execute every declared local fault through its production pipeline boundary."""
    from src.core.config import settings
    from src.core.database import (
        create_meeting,
        create_meeting_file,
        get_write_connection,
        init_db,
    )
    from src.services.chain._api import _run_pipeline_inner
    from src.services.chain._context import PipelineContext
    from src.services.processor import process_meeting_file

    init_db()

    def _create_fault_file(case_name: str) -> int:
        file_path = benchmark_root / "uploads" / f"{case_name}.txt"
        file_path.write_text(_FAULT_TEXT, encoding="utf-8")
        with get_write_connection() as conn:
            meeting_id = create_meeting(
                conn,
                title=f"Process fault: {case_name}",
                meeting_date="2026-01-01",
                user_id="benchmark",
            )
            return create_meeting_file(
                conn,
                meeting_id=meeting_id,
                file_type="txt",
                file_name=file_path.name,
                file_path=str(file_path),
                user_id="benchmark",
            )

    async def _missing_file() -> dict:
        return _failure_payload(await process_meeting_file(2_147_483_647))

    async def _extraction_failure() -> dict:
        file_id = _create_fault_file("extraction")
        with (
            patch(
                "src.services.processor._pipeline._resolve_processor",
                return_value=_FailingTextProcessor(),
            ),
            patch.object(settings, "RAGANYTHING_ENABLED", False),
            patch.object(settings, "MEETING_AUTO_SUMMARIZE_FILES", False),
        ):
            trace = await process_meeting_file(file_id)
        return _failure_payload(trace)

    async def _index_failure() -> dict:
        file_id = _create_fault_file("index")

        def _raise_index_failure(*_args, **_kwargs) -> None:
            raise ConnectionError("injected index failure")

        with (
            patch(
                "src.services.processor._pipeline._resolve_processor",
                return_value=_StaticTextProcessor(),
            ),
            patch(
                "src.services.processor._pipeline.index_meeting",
                side_effect=_raise_index_failure,
            ),
            patch.object(settings, "RAGANYTHING_ENABLED", False),
            patch.object(settings, "MEETING_AUTO_SUMMARIZE_FILES", False),
        ):
            trace = await process_meeting_file(file_id)
        return _failure_payload(trace)

    async def _persistence_failure() -> dict:
        file_id = _create_fault_file("persistence")

        def _raise_persistence_failure(*_args, **_kwargs) -> None:
            raise OSError("injected persistence failure")

        with (
            patch(
                "src.services.processor._pipeline._resolve_processor",
                return_value=_StaticTextProcessor(),
            ),
            patch("src.services.processor._pipeline.index_meeting", return_value=None),
            patch(
                "src.services.processor._pipeline.update_meeting_file_artefact",
                side_effect=_raise_persistence_failure,
            ),
            patch.object(settings, "RAGANYTHING_ENABLED", False),
            patch.object(settings, "MEETING_AUTO_SUMMARIZE_FILES", False),
        ):
            trace = await process_meeting_file(file_id)
        return _failure_payload(trace)

    async def _noop_async(_ctx) -> None:
        return None

    async def _successful_retrieve(ctx) -> None:
        ctx.trace.start_span("retrieve", "retrieve")
        ctx.docs = []
        ctx.trace.finish_span("retrieve")

    def _chat_context() -> PipelineContext:
        return PipelineContext(
            question="What are the benchmark action items?",
            user_id="benchmark",
            file_ids=[1],
            query_embedding=[0.0],
            llm=_DeterministicLLM(),
        )

    async def _run_chat_fault(*extra_patches) -> dict:
        ctx = _chat_context()
        common_patches = (
            patch("src.services.chain._api.rewrite_query_step", _noop_async),
            patch("src.services.chain._api.load_memories", _noop_async),
            patch("src.services.chain._api.load_session_context", _noop_async),
            patch("src.services.chain._api.load_entity_context", _noop_async),
            patch("src.services.chain._api.load_history", _noop_async),
            patch("src.services.chain._api.perform_web_search", _noop_async),
            patch.object(settings, "MULTI_QUERY_ENABLED", False),
        )
        with ExitStack() as stack:
            for manager in (*common_patches, *extra_patches):
                stack.enter_context(manager)
            with suppress(Exception):
                await _run_pipeline_inner(ctx)
        return _failure_payload(ctx.trace)

    async def _chat_retrieval_failure() -> dict:
        async def _raise_retrieval_failure(*_args, **_kwargs):
            raise ConnectionError("injected retrieval failure")

        return await _run_chat_fault(
            patch(
                "src.services.chain._steps_retrieve._retrieve_scoped",
                side_effect=_raise_retrieval_failure,
            )
        )

    async def _chat_generation_failure() -> dict:
        return await _run_chat_fault(
            patch("src.services.chain._api.retrieve_documents", _successful_retrieve),
            patch(
                "src.services.chain._steps_generate._invoke_chain_with_retry",
                side_effect=RuntimeError("injected generation failure"),
            ),
        )

    async def _chat_persistence_failure() -> dict:
        return await _run_chat_fault(
            patch("src.services.chain._api.retrieve_documents", _successful_retrieve),
            patch(
                "src.core.database.add_turn",
                side_effect=OSError("injected message persistence failure"),
            ),
        )

    return await execute_failure_cases(
        expectations,
        runners={
            "ingest_missing_file": _missing_file,
            "ingest_extraction_failure": _extraction_failure,
            "ingest_index_failure": _index_failure,
            "ingest_persistence_failure": _persistence_failure,
            "chat_retrieval_failure": _chat_retrieval_failure,
            "chat_generation_failure": _chat_generation_failure,
            "chat_persistence_failure": _chat_persistence_failure,
        },
    )
