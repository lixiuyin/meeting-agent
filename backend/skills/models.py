"""
Pydantic models for Skill definitions loaded from Markdown files.
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentMatchingConfig(BaseModel):
    """Configuration for intent matching mechanism."""

    method: Literal["keyword", "semantic", "llm", "hybrid"] = Field(
        default="hybrid",
        description="Matching method: keyword, semantic, llm, or hybrid",
    )
    threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score to trigger skill",
    )
    priority: int = Field(
        default=10,
        description="Priority for conflict resolution (lower = higher priority)",
    )

    # Keyword matching configuration
    keywords: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword matching rules",
    )

    # Example queries for semantic matching
    examples: list[str] = Field(
        default_factory=list,
        description="Example queries for semantic similarity matching",
    )

    # LLM routing configuration
    llm_routing: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": False},
        description="LLM-based routing configuration",
    )


class ExecutionConfig(BaseModel):
    """Configuration for skill execution."""

    mode: Literal["prompt_integrated", "standard"] = Field(
        default="prompt_integrated",
        description=(
            "Execution mode: prompt_integrated (skill config passed to LLM prompt, "
            "generates structured output in one pass) or standard (reserved for future use)"
        ),
    )
    timeout: int = Field(
        default=120,
        description="Execution timeout in seconds",
    )
    save_history: bool = Field(
        default=True,
        description="Whether to save execution history",
    )


class OutputConfig(BaseModel):
    """Configuration for output formatting.

    In the current prompt-integrated execution mode, the LLM directly generates
    structured output based on the sections configuration. The template_file
    and post_process fields are retained for backward compatibility but are
    not actively used.
    """

    format: Literal["markdown", "json", "structured"] = Field(
        default="markdown",
        description="Output format type",
    )
    sections: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Defined sections for structured output. "
            "Used by prompt_integrated mode to guide LLM generation"
        ),
    )
    template_file: str | None = Field(
        default=None,
        description="(Deprecated) Jinja2 template file. Not used in prompt_integrated mode.",
    )
    post_process: list[str] = Field(
        default_factory=list,
        description="(Deprecated) Post-processing steps. Not used in prompt_integrated mode.",
    )


class SkillMetadata(BaseModel):
    """Metadata for skill documentation."""

    author: str = Field(default="")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    category: str = Field(default="")
    use_cases: list[str] = Field(default_factory=list)


class SkillSummary(BaseModel):
    """Lightweight skill metadata for fast intent matching.

    Contains only the fields needed by the matcher — no execution config,
    output sections, documentation, or templates. Produced by
    ``SkillLoader.load_summaries()`` and cached in memory.
    """

    name: str
    display_name: str
    description: str
    intent_matching: IntentMatchingConfig = Field(default_factory=IntentMatchingConfig)
    base_path: Path | None = Field(default=None)


class SkillDefinition(BaseModel):
    """
    Complete skill definition loaded from skill.md file.

    This is the core data model that represents a skill's configuration,
    including intent matching rules, execution parameters, and output format.
    """

    # Basic information
    name: str = Field(..., description="Unique identifier for the skill")
    version: str = Field(default="1.0.0", description="Semantic version")
    display_name: str = Field(..., description="Human-readable display name")
    description: str = Field(..., description="Detailed description of skill functionality")

    # Configuration sections
    intent_matching: IntentMatchingConfig = Field(
        default_factory=IntentMatchingConfig,
        description="Intent matching configuration",
    )
    execution: ExecutionConfig = Field(
        default_factory=ExecutionConfig,
        description="Execution configuration",
    )
    output: OutputConfig = Field(
        default_factory=OutputConfig,
        description="Output formatting configuration",
    )
    metadata: SkillMetadata = Field(
        default_factory=SkillMetadata,
        description="Skill metadata",
    )

    # Runtime fields
    documentation: str = Field(
        default="",
        description="Markdown documentation content",
    )
    base_path: Path | None = Field(
        default=None,
        description="Path to skill directory",
    )

    model_config = ConfigDict(extra="allow")


class SkillMatchResult(BaseModel):
    """Result of intent matching for a skill."""

    skill: SkillSummary
    score: float = Field(ge=0.0, le=1.0)
    matched: bool
    details: dict[str, Any] = Field(default_factory=dict)
    ambiguous: bool = Field(default=False)
    alternatives: list[SkillSummary] = Field(default_factory=list)


class SkillExecutionContext(BaseModel):
    """Context passed to skill execution."""

    query: str
    user_id: str
    session_id: str | None = None
    meeting_ids: list[int] | None = None
    rag_result: dict[str, Any] | None = None  # RAG output (legacy field)
    parameters: dict[str, Any] = Field(default_factory=dict)


class SkillExecutionResult(BaseModel):
    """Result of skill execution."""

    skill_name: str
    content: str
    format: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
