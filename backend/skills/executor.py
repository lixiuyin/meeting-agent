"""
Skill execution engine.

Note: The primary skill execution logic is now integrated directly into
the RAG pipeline (see chain.py). When a skill is matched, its configuration
is passed to generate_answer() which uses get_skill_prompt() to create
a prompt with format requirements. The LLM then generates structured output
in a single pass.

This module is kept for potential future extensions where skills may need
custom execution logic beyond prompt integration.
"""

import logging
from typing import Any

from .models import (
    SkillDefinition,
    SkillExecutionContext,
    SkillExecutionResult,
)

logger = logging.getLogger(__name__)


class SkillExecutor:
    """
    Executes skills using prompt-integrated generation.

    The actual execution happens in chain.py:
    1. Skill configuration (sections, format requirements) is passed to generate_answer()
    2. generate_answer() calls get_skill_prompt() to build a prompt with format requirements
    3. LLM generates structured output following the skill's section format

    This class serves as a coordinator and potential extension point for
    future execution modes that require custom logic.
    """

    def __init__(self):
        """Initialize the skill executor."""
        logger.debug("SkillExecutor initialized")

    async def execute_with_skill(
        self,
        skill: SkillDefinition,
        context: SkillExecutionContext,
        rag_result: dict[str, Any],
    ) -> SkillExecutionResult:
        """
        Execute skill using prompt-integrated generation.

        Note: This method is currently not used directly. The primary execution
        flow is in chain.py where skill configuration is passed directly to
        generate_answer(). This method is kept for potential future use cases
        where custom skill execution logic is needed.

        Args:
            skill: Skill definition with output format requirements
            context: Execution context containing query and user info
            rag_result: Result from RAG pipeline including answer and sources

        Returns:
            Execution result with formatted content
        """
        logger.info(
            "Skill %s execution coordinated (actual generation in chain.py)",
            skill.name,
        )

        # The actual formatting is done by the LLM in chain.py
        # This method serves as a pass-through for compatibility
        return SkillExecutionResult(
            skill_name=skill.name,
            content=rag_result.get("answer", ""),
            format=skill.output.format,
            sources=rag_result.get("sources", []),
            execution_time_ms=0,
            metadata={
                "mode": "prompt_integrated",
                "skill_description": skill.description,
                "sections": [s.get("title", "") for s in skill.output.sections],
            },
        )
