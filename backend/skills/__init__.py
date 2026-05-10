"""
Skills package for Meeting Agent.

Provides a Markdown-based skill system for intent matching and prompt-structured output generation.
"""

from .loader import SkillLoader
from .matcher import IntentMatchingService
from .models import (
    ExecutionConfig,
    IntentMatchingConfig,
    OutputConfig,
    SkillDefinition,
    SkillExecutionContext,
    SkillExecutionResult,
    SkillMatchResult,
    SkillMetadata,
)

__all__ = [
    "ExecutionConfig",
    "IntentMatchingConfig",
    "IntentMatchingService",
    "OutputConfig",
    "SkillDefinition",
    "SkillExecutionContext",
    "SkillExecutionResult",
    "SkillLoader",
    "SkillMatchResult",
    "SkillMetadata",
]
