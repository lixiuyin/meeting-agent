from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from ...core.config import SettingsSnapshot
from ...core.file_scope import FileScope
from ...core.operating_modes import MemoryMode, RetrievalProfile
from ...core.trace import TraceContext

if TYPE_CHECKING:
    from ..rag._query_analysis import QueryAnalysis
    from ..rag._query_plan import QueryPlan


@dataclass
class PipelineContext:
    """Mutable context passed through the RAG pipeline steps.

    Fields are grouped by lifecycle stage:
    - Input fields (set before pipeline starts)
    - Intermediate fields (populated by pipeline steps)
    - Output field (final result set by generate_answer)
    """

    # --- Input fields ---
    question: str
    session_id: str | None = None
    user_id: str = "default"
    meeting_ids: list[int] | None = None
    file_ids: list[int] | None = None
    memory_scope_override: tuple[int, ...] | None = None
    restored_project_ids: tuple[str, ...] = ()
    resolved_file_scope: FileScope | None = None
    top_k: int | None = None
    use_web_search: bool = False
    web_search_mode: str = "off"
    web_search_results: int | None = None
    file_types: list[str] | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    valid_at: datetime.datetime | None = None
    known_at: datetime.datetime | None = None
    rag_mode: str | None = None
    retrieval_profile: RetrievalProfile = "balanced"
    memory_mode: MemoryMode = "balanced"
    continuation_mode: str = "latest"

    trace: TraceContext = field(default_factory=TraceContext)
    # Singletons resolved once at pipeline entry so all steps use a consistent
    # instance even if settings change mid-request (B2 ctx injection).
    llm: BaseChatModel | None = None
    embeddings: Embeddings | None = None
    settings_epoch: int = 0
    settings_snapshot: SettingsSnapshot | None = None

    # --- Intermediate fields (populated by pipeline steps) ---
    rewritten_query: str = ""
    query_scope_notice: str | None = None
    docs: list[dict] = field(default_factory=list)
    scope_file_ids: list[int] = field(default_factory=list)
    query_analysis: QueryAnalysis | None = None
    query_plan: QueryPlan | None = None
    known_speakers: list[str] = field(default_factory=list)
    meeting_priors: dict[int, float] = field(default_factory=dict)
    memory_context: str = ""
    recalled_memory_entries: list[object] = field(default_factory=list)
    memory_sources: list[dict] = field(default_factory=list)
    session_context: str = ""
    session_task_state: dict = field(default_factory=dict)
    restored_source_context: str = ""
    snapshot_restored: bool = False
    snapshot_restore_status: str = "not_requested"
    snapshot_restore_error: str = ""
    frozen_combined_context: str = ""
    frozen_snapshot_source_ai_message_id: int | None = None
    entity_context: str = ""
    web_context: str = ""
    web_results: list[dict] = field(default_factory=list)
    history_messages: list[BaseMessage] = field(default_factory=list)
    raw_history_messages: list[BaseMessage] | None = None
    session_created: bool = False
    meeting_context: str = ""
    combined_context: str = ""
    past_session_refs: list[dict] = field(default_factory=list)
    query_embedding: list[float] | None = None
    saved_message_ids: list[int] = field(default_factory=list)

    # --- Output field ---
    answer: str = ""
    # User-visible generation quality signal. Degraded answers are persisted,
    # while transport/UI layers render the reason separately from answer text.
    degraded: bool = False
    degradation_reason: str | None = None
    failed_extraction_count: int = 0  # Tracks fact extraction failures for alerting

    # Skill match outcome (populated by the pipeline when skill matching runs
    # concurrently with retrieve). Readers need skill_name/skill_confidence for
    # telemetry; full definition is resolved inside the pipeline itself.
    skill_name: str | None = None
    skill_confidence: float | None = None

    # M-1: Track context truncation for user-facing signal
    dropped_chunks: int = 0
    retrieval_candidate_count: int = 0
    # M-8: Signal that history loading failed so generation can inject a warning
    history_load_failed: bool = False
    # Accumulates names of background tasks that failed (non-blocking).
    background_errors: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Result returned by the RAG pipeline after ask() completes."""

    answer: str
    sources: list[dict]
    session_id: str
    web_results: list[dict] | None = None
    past_sessions: list[dict] | None = None
    extraction_failed: bool = False  # True if background fact extraction failed after retries
    trace: dict | None = None  # Serialized TraceContext for response
    skill_used: str | None = None
    skill_confidence: float | None = None
    context_truncated: int | None = None  # M-1: Number of chunks dropped by token budget
    background_errors: list[str] = field(default_factory=list)
    # Non-empty when background tasks (extraction, etc.) fail; surfaced to
    # the frontend so it can show a non-blocking warning.
    degraded: bool = False
    degradation_reason: str | None = None
