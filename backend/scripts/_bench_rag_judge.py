"""LLM-as-judge scorers for RAG answer quality."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from src.services.chain._judge_prompts import (
    ANSWER_CORRECTNESS_PROMPT,
    ANSWER_RELEVANCE_PROMPT,
    CITATION_QUALITY_PROMPT,
    CONTEXT_PRECISION_PROMPT,
    CONTEXT_RECALL_PROMPT,
    FAITHFULNESS_PROMPT,
    MULTI_TURN_QUALITY_PROMPT,
)
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "qwen/qwen3.8-flash"


def _url_host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def get_llm_runtime_config(model_override: str | None = None) -> dict:
    """Return a non-secret LLM identity for benchmark reproducibility."""
    from src.core.config import settings

    return {
        "binding": settings.LLM_BINDING,
        "model": model_override or settings.LLM_MODEL,
        "base_url_host": _url_host(settings.LLM_BASE_URL),
        "reasoning_effort": getattr(settings, "LLM_REASONING_EFFORT", None),
    }


RAG_ANSWER_METHOD = {
    "name": "ragas-aligned-llm-judge",
    "version": 1,
    "implementation": "in-repository",
    "notes": (
        "RAGAS metric contracts are adapted to the meeting artifact domain; "
        "this is not the upstream ragas Python package."
    ),
}


def _average_precision_at_k(relevant_indices: list[int], *, chunk_count: int) -> float:
    """Return rank-sensitive average precision for judged relevant chunks.

    The LLM labels relevance only. The harness owns aggregation so a judge
    cannot return a score inconsistent with its own labels.
    """
    relevant = sorted(set(relevant_indices))
    if not relevant or chunk_count <= 0:
        return 0.0
    precision_sum = sum(sum(previous <= rank for previous in relevant) / rank for rank in relevant)
    return precision_sum / len(relevant)


def _call_judge(prompt: str, temperature: float = 0.0, *, llm: Any | None = None) -> dict | None:
    """Call the judge LLM and parse the JSON response.

    Returns None if parsing fails after retries.
    """
    judge_llm = llm or get_llm()
    for attempt in range(2):
        final_prompt = (
            prompt if attempt == 0 else prompt + "\n\nSTRICT: RESPOND ONLY WITH VALID JSON."
        )
        try:
            response = judge_llm.invoke(final_prompt, temperature=temperature)
            content = response.content if hasattr(response, "content") else str(response)
            # Try to extract JSON from markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            parsed = json.loads(content.strip())
            if isinstance(parsed.get("score"), (int, float)) and 0 <= parsed["score"] <= 1:
                result = {
                    "score": float(parsed["score"]),
                    "justification": str(parsed.get("justification", "")),
                    "attempts": attempt + 1,
                    "parse_retries": attempt,
                }
                if isinstance(parsed.get("relevant_chunk_indices"), list):
                    result["relevant_chunk_indices"] = parsed["relevant_chunk_indices"]
                return result
        except Exception:
            logger.warning("Judge JSON parse failed (attempt %d)", attempt + 1, exc_info=True)
    return None


def judge_faithfulness(answer: str, context: str, *, llm: Any | None = None) -> dict | None:
    """Score faithfulness of answer to context. Returns {score, justification} or None."""
    prompt = FAITHFULNESS_PROMPT.format(answer=answer, context=context)
    return _call_judge(prompt, llm=llm)


def judge_answer_relevance(question: str, answer: str, *, llm: Any | None = None) -> dict | None:
    """Score whether answer addresses the question. Returns {score, justification} or None."""
    prompt = ANSWER_RELEVANCE_PROMPT.format(question=question, answer=answer)
    return _call_judge(prompt, llm=llm)


def judge_context_precision(
    question: str, chunks: list[str], *, llm: Any | None = None
) -> dict | None:
    """Score relevance of retrieved chunks to the question."""
    numbered = "\n\n".join(f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks))
    prompt = CONTEXT_PRECISION_PROMPT.format(question=question, chunks=numbered)
    result = _call_judge(prompt, llm=llm)
    if result is None:
        return None
    indices = result.get("relevant_chunk_indices", [])
    valid_indices = sorted(
        {
            index
            for index in indices
            if isinstance(index, int) and not isinstance(index, bool) and 1 <= index <= len(chunks)
        }
    )
    result["judge_score"] = result["score"]
    result["score"] = _average_precision_at_k(valid_indices, chunk_count=len(chunks))
    result["relevant_chunk_indices"] = valid_indices
    result["aggregation"] = "average_precision_at_k"
    return result


def judge_context_recall(
    question: str, reference_answer: str, chunks: list[str], *, llm: Any | None = None
) -> dict | None:
    """Score whether retrieved chunks contain information needed for the reference answer."""
    numbered = "\n\n".join(f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks))
    prompt = CONTEXT_RECALL_PROMPT.format(
        question=question, reference_answer=reference_answer, chunks=numbered
    )
    return _call_judge(prompt, llm=llm)


def judge_answer_correctness(
    question: str, reference_answer: str, answer: str, *, llm: Any | None = None
) -> dict | None:
    """Score semantic correctness against a withheld reference answer."""
    prompt = ANSWER_CORRECTNESS_PROMPT.format(
        question=question,
        reference_answer=reference_answer,
        answer=answer,
    )
    return _call_judge(prompt, llm=llm)


def judge_citation_quality(
    answer: str, chunks: list[str], *, llm: Any | None = None
) -> dict | None:
    """Score inline citation entailment, completeness, and validity."""
    numbered = "\n\n".join(f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks))
    prompt = CITATION_QUALITY_PROMPT.format(answer=answer, chunks=numbered)
    return _call_judge(prompt, llm=llm)


def judge_multi_turn_quality(
    *,
    history: list[dict[str, str]],
    question: str,
    answer: str,
    context: str,
    answerability: str,
    reference_answer: str | None,
    expected_behavior: str | None,
    llm: Any | None = None,
) -> dict | None:
    """Judge four turn-level dimensions in one isolated, auditable call."""
    judge_llm = llm or get_llm()
    history_text = (
        "\n".join(
            f"Turn {index}: Q={item['question']} A={item['answer']}"
            for index, item in enumerate(history, start=1)
        )
        or "(none)"
    )
    prompt = MULTI_TURN_QUALITY_PROMPT.format(
        history=history_text,
        question=question,
        answer=answer,
        context=context or "(none)",
        answerability=answerability,
        reference_answer=reference_answer or "(not applicable)",
        expected_behavior=expected_behavior or "(not applicable)",
    )
    required = ("faithfulness", "appropriateness", "naturalness", "completeness")
    for attempt in range(2):
        final_prompt = (
            prompt if attempt == 0 else prompt + "\nSTRICT: RESPOND ONLY WITH VALID JSON."
        )
        try:
            response = judge_llm.invoke(final_prompt, temperature=0.0)
            raw = response.content if hasattr(response, "content") else response
            content = raw if isinstance(raw, str) else str(raw)
            if "```json" in content:
                content = content.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in content:
                content = content.split("```", 1)[1].split("```", 1)[0]
            parsed = json.loads(content.strip())
            metrics: dict[str, dict] = {}
            for metric in required:
                item = parsed.get(metric)
                if not isinstance(item, dict):
                    raise ValueError(f"missing judge metric: {metric}")
                score = item.get("score")
                if (
                    not isinstance(score, (int, float))
                    or isinstance(score, bool)
                    or not 0 <= score <= 1
                ):
                    raise ValueError(f"invalid {metric} score")
                metrics[metric] = {
                    "score": float(score),
                    "justification": str(item.get("justification", "")),
                }
            return {"metrics": metrics, "attempts": attempt + 1, "parse_retries": attempt}
        except Exception:
            logger.warning(
                "Multi-turn judge JSON parse failed (attempt %d)",
                attempt + 1,
                exc_info=True,
            )
    return None


def get_judge_config(model_override: str | None = None) -> dict:
    """Return judge model and embedder config for reproducibility.

    Host-only URL extraction avoids leaking paths that may contain tokens.
    """
    from src.core.config import settings

    return {
        "llm": get_llm_runtime_config(model_override),
        "embedder": {
            "binding": settings.EMBEDDING_BINDING,
            "model": settings.EMBEDDING_MODEL,
            "base_url_host": _url_host(settings.EMBEDDING_BASE_URL),
        },
        "judge_isolation": {
            "separate_instance": True,
            "same_model_as_generator": (model_override or settings.LLM_MODEL) == settings.LLM_MODEL,
        },
    }
