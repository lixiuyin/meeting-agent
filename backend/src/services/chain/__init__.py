"""RAG chain service public API.

Long-running and fire-and-forget work belongs to the shared durable job service;
the chain package only orchestrates request-scoped work.
"""

from ._api import _run_pipeline, ask, ask_stream
from ._context import PipelineContext, PipelineResult
from ._formatting import _build_system_context, _extract_sources, _format_docs
from ._routing import _casual_response, _classify_intent, _is_trivially_short
from ._steps_context import _format_memory_context

__all__ = [
    "PipelineContext",
    "PipelineResult",
    "_build_system_context",
    "_casual_response",
    "_classify_intent",
    "_extract_sources",
    "_format_docs",
    "_format_memory_context",
    "_is_trivially_short",
    "_run_pipeline",
    "ask",
    "ask_stream",
]
