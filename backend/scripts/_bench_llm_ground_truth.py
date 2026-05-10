"""LLM-based expected-chunk filtering with disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent.parent / "benchmark-results" / ".llm_ground_truth_cache.json"


_PROMPT_TEMPLATE = """You are a relevance judge for a retrieval benchmark.

Query: {query}
Expected Answer: {expected_answer}

Below are candidate text chunks. Select ONLY the chunks that contain information directly needed to answer the query.

Candidates:
{candidates}

Respond with valid JSON only: {{"relevant_ids": ["chunk_id_1", ...]}}
If no chunk is relevant, return {{"relevant_ids": []}}."""


def _cache_key(query: str, expected_answer: str, candidate_ids: list[str]) -> str:
    raw = json.dumps({"q": query, "a": expected_answer, "ids": candidate_ids}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_cache() -> dict[str, list[str]]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load LLM ground-truth cache")
    return {}


def _save_cache(cache: dict[str, list[str]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.warning("Failed to save LLM ground-truth cache")


def _build_prompt(query: str, expected_answer: str, candidates: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in candidates:
        cid = c["chunk_id"]
        text = c.get("text", "")[:400]  # truncate to keep prompt size reasonable
        lines.append(f"[id: {cid}] {text}")
    return _PROMPT_TEMPLATE.format(
        query=query,
        expected_answer=expected_answer,
        candidates="\n".join(lines),
    )


def _parse_json_response(content: str) -> list[str] | None:
    """Extract {"relevant_ids": [...]} from LLM output."""
    try:
        # Strip reasoning tags (DeepSeek / QwQ etc.)
        if "</think>" in content:
            content = content.split("</think>", 1)[-1]
        # Strip markdown code fences if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        parsed = json.loads(content.strip())
        if isinstance(parsed, dict) and "relevant_ids" in parsed:
            ids = parsed["relevant_ids"]
            if isinstance(ids, list):
                return [str(i) for i in ids]
        # Fallback: if LLM returned a plain list
        if isinstance(parsed, list):
            return [str(i) for i in parsed]
    except Exception:
        pass
    return None


def llm_filter_relevant_chunks(
    query: str,
    expected_answer: str,
    candidate_chunks: list[dict[str, Any]],
) -> list[str]:
    """Return chunk IDs judged relevant by LLM.

    Uses a disk cache so re-runs are free. Falls back to empty list on any
    failure (caller should use heuristic result instead).
    """
    if not candidate_chunks:
        return []

    candidate_ids = [c["chunk_id"] for c in candidate_chunks]
    key = _cache_key(query, expected_answer, candidate_ids)
    cache = _load_cache()
    if key in cache:
        logger.debug("LLM ground-truth cache hit")
        return cache[key]

    prompt = _build_prompt(query, expected_answer, candidate_chunks)

    try:
        from src.services.llm import get_llm

        llm = get_llm()
        for attempt in range(2):
            if attempt == 1:
                prompt += "\n\nSTRICT: Respond ONLY with valid JSON."
            response = llm.invoke(prompt, temperature=0.0)
            content = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_json_response(content)
            if parsed is not None:
                # Validate IDs exist in candidate pool
                valid = [cid for cid in parsed if cid in candidate_ids]
                cache[key] = valid
                _save_cache(cache)
                return valid
            logger.warning(
                "LLM ground-truth parse failed (attempt %d). Raw content:\n%s",
                attempt + 1,
                content[:800],
            )
    except Exception:
        logger.warning("LLM ground-truth call failed", exc_info=True)

    return []
