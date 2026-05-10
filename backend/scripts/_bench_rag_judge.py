"""LLM-as-judge scorers for RAG answer quality."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from src.services.chain._judge_prompts import (
    ANSWER_RELEVANCE_PROMPT,
    CONTEXT_PRECISION_PROMPT,
    CONTEXT_RECALL_PROMPT,
    FAITHFULNESS_PROMPT,
)
from src.services.llm import get_llm

logger = logging.getLogger(__name__)


def _call_judge(prompt: str, temperature: float = 0.0) -> dict | None:
    """Call the judge LLM and parse the JSON response.

    Returns None if parsing fails after retries.
    """
    llm = get_llm()
    for attempt in range(2):
        final_prompt = prompt if attempt == 0 else prompt + "\n\nSTRICT: RESPOND ONLY WITH VALID JSON."
        try:
            response = llm.invoke(final_prompt, temperature=temperature)
            content = response.content if hasattr(response, "content") else str(response)
            # Try to extract JSON from markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            parsed = json.loads(content.strip())
            if isinstance(parsed.get("score"), (int, float)):
                return {
                    "score": float(parsed["score"]),
                    "justification": str(parsed.get("justification", "")),
                }
        except Exception:
            logger.warning("Judge JSON parse failed (attempt %d)", attempt + 1, exc_info=True)
    return None


def judge_faithfulness(answer: str, context: str) -> dict | None:
    """Score faithfulness of answer to context. Returns {score, justification} or None."""
    prompt = FAITHFULNESS_PROMPT.format(answer=answer, context=context)
    return _call_judge(prompt)


def judge_answer_relevance(question: str, answer: str) -> dict | None:
    """Score whether answer addresses the question. Returns {score, justification} or None."""
    prompt = ANSWER_RELEVANCE_PROMPT.format(question=question, answer=answer)
    return _call_judge(prompt)


def judge_context_precision(question: str, chunks: list[str]) -> dict | None:
    """Score relevance of retrieved chunks to the question. Returns {score, justification} or None."""
    numbered = "\n\n".join(
        f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks)
    )
    prompt = CONTEXT_PRECISION_PROMPT.format(question=question, chunks=numbered)
    return _call_judge(prompt)


def judge_context_recall(
    question: str, reference_answer: str, chunks: list[str]
) -> dict | None:
    """Score whether retrieved chunks contain information needed for the reference answer."""
    numbered = "\n\n".join(
        f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks)
    )
    prompt = CONTEXT_RECALL_PROMPT.format(
        question=question, reference_answer=reference_answer, chunks=numbered
    )
    return _call_judge(prompt)


def get_judge_config() -> dict:
    """Return judge model and embedder config for reproducibility.

    Host-only URL extraction avoids leaking paths that may contain tokens.
    """
    from src.core.config import settings

    def _host(url: str | None) -> str:
        if not url:
            return ""
        try:
            return urlparse(url).hostname or ""
        except Exception:
            return ""

    return {
        "llm": {
            "binding": settings.LLM_BINDING,
            "model": settings.LLM_MODEL,
            "base_url_host": _host(settings.LLM_BASE_URL),
        },
        "embedder": {
            "binding": settings.EMBEDDING_BINDING,
            "model": settings.EMBEDDING_MODEL,
            "base_url_host": _host(settings.EMBEDDING_BASE_URL),
        },
    }
