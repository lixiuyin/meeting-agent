"""Combined fact + entity extraction coordinator.

Replaces the sequential memory.auto_extract_facts + kg.extract_entities calls
with a single LLM round-trip that returns both, cutting per-turn extraction
cost roughly in half.

The merged path is gated by `settings.COMBINED_EXTRACTION_ENABLED`; callers
should fall back to the legacy two-call path when disabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ...core.config import settings
from ..tokenizer import count_tokens

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 3.5


def _empty_facts_need_review(evidence: str | None) -> bool:
    """Retry a silent omission of an explicit durable assertion, never fabricate one."""
    from ...core.untrusted_material import has_embedded_directive

    return bool(
        evidence
        and not has_embedded_directive(evidence)
        and re.search(
            r"\b(?:prefers?|owns|deadline|retention|decided|assigned)\b|"
            r"首选|偏好|负责|截止|决定|留存|保留期限",
            evidence,
            re.IGNORECASE,
        )
    )


def should_skip_extraction(question: str, answer: str) -> bool:
    """Skip extraction on trivial or empty turns to save tokens."""
    if not answer or not question:
        return True
    return len(answer.strip()) < settings.EXTRACTION_MIN_ANSWER_CHARS


def truncate_for_extraction(text: str, max_tokens: int, model: str) -> str:
    """Cap text at max_tokens using a char-based pre-filter + tokenizer refinement."""
    if not text or max_tokens <= 0:
        return text
    if count_tokens(text, model) <= max_tokens:
        return text
    char_budget = int(max_tokens * _CHARS_PER_TOKEN)
    candidate = text[:char_budget]
    while candidate and count_tokens(candidate, model) > max_tokens:
        candidate = candidate[: int(len(candidate) * 0.9)]
    return f"{candidate}\n\n[truncated]"


def _parse_combined(content: str) -> dict[str, list[dict]] | None:
    """Parse the combined extraction JSON. Tolerant of fenced code blocks,
    reasoning-model thinking tags, common LLM formatting artifacts (trailing
    commas, missing braces), and truncated output (mid-string cutoff)."""
    if not content:
        return None
    stripped = _strip_thinking_tags(content).strip()
    if stripped.startswith("```"):
        lines = [ln for ln in stripped.splitlines() if not ln.strip().startswith("```")]
        stripped = "\n".join(lines).strip()

    candidates: list[str] = [stripped]
    # If top-level braces are missing, try to extract the first JSON object
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

    # Last-resort salvage path: trim incomplete tail and close open brackets
    salvaged = _salvage_truncated_json(stripped)
    if salvaged and salvaged not in candidates:
        candidates.append(salvaged)

    for raw in candidates:
        fixed = _fix_trailing_commas(raw)
        for payload in (raw, fixed):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            facts = data.get("facts") or []
            entities = data.get("entities") or []
            relations = data.get("relations") or []
            if (
                isinstance(facts, list)
                and isinstance(entities, list)
                and isinstance(relations, list)
            ):
                return {"facts": facts, "entities": entities, "relations": relations}

    logger.warning("Combined extraction JSON parse failed: %s", stripped[:200])
    return None


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> blocks and lone </think> prefixes emitted by
    reasoning models (Qwen3-thinking, DeepSeek-R1, gpt-oss, etc.).

    Handles two cases:
      1. Complete pair: ``<think>...</think>JSON`` → strip whole block.
      2. Provider stripped opening tag: ``</think>JSON`` → drop everything up
         to and including the closing tag.
    """
    import re

    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "</think>" in text.lower():
        idx = text.lower().rfind("</think>")
        text = text[idx + len("</think>") :]
    return text


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or } — a common LLM JSON mistake."""
    import re

    return re.sub(r",(\s*[}\]])", r"\1", text)


def _salvage_truncated_json(text: str) -> str | None:
    """Best-effort recovery of JSON truncated mid-output (token-limit cutoff).

    Trims the incomplete tail back to the last clean structural boundary
    (closing bracket or comma at array depth) and appends matching closers
    for any still-open ``{`` / ``[``. Returns None when no salvage point
    exists, so callers can fall through to other candidates.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]

    stack: list[str] = []
    in_string = False
    escape = False
    last_safe = -1
    for i, ch in enumerate(body):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                stack.pop()
            last_safe = i
        elif ch == "," and stack:
            last_safe = i - 1  # exclude the comma itself

    if last_safe < 0:
        return None

    truncated = body[: last_safe + 1]
    stack = []
    in_string = False
    escape = False
    for ch in truncated:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()

    closer = {"{": "}", "[": "]"}
    closing = "".join(closer[c] for c in reversed(stack))
    return truncated + closing


async def run_combined_extraction(
    user_id: str,
    question: str,
    answer: str,
    session_id: str | None = None,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    evidence_message_ids: list[int] | None = None,
    evidence_text: str | None = None,
    source_event_time: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Extract facts + entities + relations in a single LLM call, then dispatch.

    Returns extraction counts. Unparseable/non-text combined output falls back
    to the legacy dedicated extractors; provider failures propagate so durable
    jobs can retry instead of recording false success.
    """
    # Retrieved or ingested evidence can contain a complete durable assertion
    # in very few characters (especially CJK). The chat-length cost heuristic
    # must never suppress authoritative evidence.
    if not evidence_text and should_skip_extraction(question, answer):
        return {"facts_added": 0, "entities_added": 0, "relations_added": 0}

    from ..knowledge_graph import settings as _kg_settings
    from ..knowledge_graph._storage import _store_entities, _store_relations
    from ..llm import (
        cached_retry_invoke,
        escape_prompt_data,
        get_combined_extraction_prompt,
        get_llm,
    )
    from ..memory import memory_service

    memory_enabled = settings.MEMORY_AUTO_EXTRACT
    kg_enabled = _kg_settings.KNOWLEDGE_GRAPH_ENABLED
    if not memory_enabled and not kg_enabled:
        return {"facts_added": 0, "entities_added": 0, "relations_added": 0}

    capped_answer = truncate_for_extraction(
        answer, settings.EXTRACTION_INPUT_MAX_TOKENS, settings.LLM_MODEL
    )
    capped_question = truncate_for_extraction(
        question, settings.EXTRACTION_INPUT_MAX_TOKENS // 4, settings.LLM_MODEL
    )

    existing_ctx = ""
    if memory_enabled:
        try:
            existing = memory_service.search_important(user_id, min_importance=2, limit=5)
            if existing:
                keys_only = ", ".join(m["key"] for m in existing)
                existing_ctx = f"Known memory keys (avoid duplicates): {keys_only}\n\n"
        except Exception:
            logger.debug(
                "Could not load existing memory keys for extraction context",
                exc_info=True,
            )

    llm = get_llm()
    prompt_template = get_combined_extraction_prompt()
    prompt = prompt_template.format(
        question=escape_prompt_data(capped_question),
        answer=escape_prompt_data(capped_answer),
        user_context=escape_prompt_data(existing_ctx),
    )

    try:
        response: Any = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
    except Exception:
        logger.warning("Combined extraction LLM call failed", exc_info=True)
        raise

    content = response.content
    if isinstance(content, list):
        text_parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = "\n".join(text_parts)
        if not content.strip():
            logger.warning("Combined extraction received non-text-only response")
            return await _run_legacy_extraction_fallback(
                user_id=user_id,
                question=question,
                answer=answer,
                session_id=session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text,
                source_event_time=source_event_time,
                evidence_refs=evidence_refs,
                memory_enabled=memory_enabled,
                kg_enabled=kg_enabled,
            )

    parsed = _parse_combined(content)
    if not parsed:
        return await _run_legacy_extraction_fallback(
            user_id=user_id,
            question=question,
            answer=answer,
            session_id=session_id,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
            evidence_message_ids=evidence_message_ids,
            evidence_text=evidence_text,
            source_event_time=source_event_time,
            evidence_refs=evidence_refs,
            memory_enabled=memory_enabled,
            kg_enabled=kg_enabled,
        )

    # M-6: Validate parsed output to prevent storage bloat from malformed
    # LLM output (overlong keys, invalid entity types, etc.).
    parsed = _validate_parsed_extraction(parsed)

    # Prewarm embedding cache for everything we are about to store. The
    # downstream per-fact memory_service.set() and per-entity vs.upsert()
    # each internally call embed_documents([single_text]) on a separate HTTP
    # request, which is an N+1 (5 facts + 8 entities = 13 sequential HTTP
    # calls). Issuing one batched embed_documents up front populates the
    # per-text LRU cache so each downstream call becomes a cache hit.
    await _prewarm_extraction_embeddings(
        {
            "facts": parsed["facts"] if memory_enabled else [],
            "entities": parsed["entities"] if kg_enabled else [],
            "relations": parsed["relations"] if kg_enabled else [],
        }
    )

    facts_added = 0
    fallback_used = 0
    if memory_enabled:
        dispatch_diagnostics: dict[str, int] = {}
        facts_added = await _dispatch_facts(
            user_id,
            capped_question,
            capped_answer,
            parsed["facts"],
            session_id,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
            evidence_message_ids=evidence_message_ids,
            evidence_text=evidence_text,
            source_event_time=source_event_time,
            evidence_refs=evidence_refs,
            diagnostics=dispatch_diagnostics,
        )
        if facts_added == 0 and (
            (parsed["facts"] and dispatch_diagnostics.get("validated", 0) == 0)
            or (not parsed["facts"] and _empty_facts_need_review(evidence_text))
        ):
            fallback = await _run_legacy_extraction_fallback(
                user_id=user_id,
                question=question,
                answer=answer,
                session_id=session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text,
                source_event_time=source_event_time,
                evidence_refs=evidence_refs,
                memory_enabled=True,
                kg_enabled=False,
            )
            facts_added = fallback["facts_added"]
            fallback_used = 1

    entities_added = 0
    relations_added = 0
    if kg_enabled:
        try:
            entities_added = await _store_entities(
                user_id,
                parsed["entities"],
                session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
            )
            relations_added = await _store_relations(
                user_id,
                parsed["relations"],
                session_id,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text or question,
            )
        except Exception:
            logger.warning("Entity/relation storage failed", exc_info=True)
            # A durable extraction job must retry partial graph writes instead
            # of reporting a clean success with missing provenance edges.
            raise

    return {
        "facts_added": facts_added,
        "facts_candidates": len(parsed["facts"]) if memory_enabled else 0,
        "facts_rejected": max(0, len(parsed["facts"]) - facts_added) if memory_enabled else 0,
        "fallback_used": fallback_used,
        "entities_added": entities_added,
        "relations_added": relations_added,
    }


async def _run_legacy_extraction_fallback(
    *,
    user_id: str,
    question: str,
    answer: str,
    session_id: str | None,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    evidence_message_ids: list[int] | None,
    evidence_text: str | None,
    source_event_time: str | None,
    evidence_refs: list[dict[str, Any]] | None,
    memory_enabled: bool,
    kg_enabled: bool,
) -> dict[str, int]:
    """Run dedicated extractors after combined-output failure."""
    from ..knowledge_graph import kg_service
    from ..memory import memory_service

    facts_added = 0
    if memory_enabled:
        facts_added = await memory_service.auto_extract_facts(
            user_id,
            question,
            answer,
            session_id=session_id,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
            evidence_message_ids=evidence_message_ids,
            evidence_text=evidence_text,
            source_event_time=source_event_time,
            evidence_refs=evidence_refs,
        )
    graph_result = {"entities_added": 0, "relations_added": 0}
    if kg_enabled:
        graph_result = await kg_service.extract_entities(
            user_id,
            question,
            answer,
            session_id=session_id,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
            evidence_message_ids=evidence_message_ids,
            evidence_text=evidence_text,
            raise_on_error=True,
        )
    return {
        "facts_added": facts_added,
        "facts_candidates": 0,
        "facts_rejected": 0,
        "fallback_used": 1,
        "entities_added": int(graph_result.get("entities_added", 0)),
        "relations_added": int(graph_result.get("relations_added", 0)),
    }


_VALID_ENTITY_TYPES = frozenset(
    {"person", "project", "topic", "organization", "tool", "concept", "location"}
)

# M-6: Field length limits to prevent storage bloat from malformed LLM output.
_MAX_FACT_KEY_LEN = 200
_MAX_FACT_VALUE_LEN = 2000
_MAX_ENTITY_NAME_LEN = 200
_MAX_ENTITY_DESC_LEN = 1000
_MAX_RELATION_NAME_LEN = 200


def _validate_parsed_extraction(parsed: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Drop entries with overlong fields or invalid entity types (M-6).

    Returns a filtered copy — does not mutate the input.
    """
    facts = [
        f
        for f in parsed.get("facts", [])
        if isinstance(f.get("key"), str)
        and len(f["key"]) <= _MAX_FACT_KEY_LEN
        and isinstance(f.get("value"), str)
        and len(f["value"]) <= _MAX_FACT_VALUE_LEN
    ]
    entities = [
        e
        for e in parsed.get("entities", [])
        if isinstance(e.get("type"), str)
        and e["type"] in _VALID_ENTITY_TYPES
        and isinstance(e.get("name"), str)
        and len(e["name"]) <= _MAX_ENTITY_NAME_LEN
        and (not e.get("description") or len(str(e["description"])) <= _MAX_ENTITY_DESC_LEN)
    ]
    relations = [
        r
        for r in parsed.get("relations", [])
        if isinstance(r.get("subject"), str)
        and r["subject"].strip()
        and len(r["subject"]) <= _MAX_RELATION_NAME_LEN
        and isinstance(r.get("object"), str)
        and r["object"].strip()
        and len(r["object"]) <= _MAX_RELATION_NAME_LEN
        and isinstance(r.get("predicate", "related_to"), str)
    ]
    dropped_f = len(parsed.get("facts", [])) - len(facts)
    dropped_e = len(parsed.get("entities", [])) - len(entities)
    dropped_r = len(parsed.get("relations", [])) - len(relations)
    if dropped_f or dropped_e or dropped_r:
        logger.warning(
            "Extraction validation dropped: %d facts, %d entities, %d relations",
            dropped_f,
            dropped_e,
            dropped_r,
        )
    return {"facts": facts, "entities": entities, "relations": relations}


def _build_fact_embed_text(key: str, value: str, category: str | None) -> str:
    """Reproduce the exact text MemoryVectorStore.upsert embeds for a fact.

    Must stay byte-identical with the formula in
    ``services/memory/_vectorstore.py:upsert`` so the prewarmed cache hits
    on the downstream single-text upsert call.
    """
    return f"[{category or 'general'}] {key}: {value}" if category else f"{key}: {value}"


def _build_entity_embed_text(entity_type: str, name: str, description: str | None) -> str:
    """Reproduce the exact text EntityVectorStore.upsert embeds for an entity.

    Must stay byte-identical with the formula in
    ``services/knowledge_graph/_vectorstore.py:upsert``.
    """
    text = f"{entity_type}: {name}"
    if description:
        text += f" — {description}"
    return text


async def _prewarm_extraction_embeddings(parsed: dict[str, list[dict]]) -> None:
    """Batch-embed every text that downstream extraction storage will need.

    Without this, a single combined extraction with N facts and M entities
    triggers N + M sequential single-text HTTP embedding calls (each
    `vs.upsert` calls `add_documents([doc])` which calls
    `embed_documents([single_text])`). When the embedding provider is slow
    (e.g. OpenRouter), these sequential calls accumulate, saturating the
    HTTP client and starving any concurrent chat-stream LLM call from
    being dispatched — which surfaces to the user as the "Stream stalled"
    timeout in the frontend.

    We compute every text we know we will store, dedupe, and issue ONE
    batched embed_documents call. The per-text LRU cache in
    ``_QueryCachedEmbeddings`` then satisfies each downstream upsert
    without further HTTP traffic.
    """
    import asyncio as _asyncio

    from ..embedder import get_embeddings

    facts = parsed.get("facts") or []
    entities = parsed.get("entities") or []
    if not facts and not entities:
        return

    texts: set[str] = set()
    for raw in facts:
        if not isinstance(raw, dict):
            continue
        key = (raw.get("key") or "").strip()
        value = (raw.get("value") or "").strip()
        if not key or not value:
            continue
        category = raw.get("category")
        category = category.strip() if isinstance(category, str) and category.strip() else None
        texts.add(_build_fact_embed_text(key, value, category))

    for ent in entities:
        if not isinstance(ent, dict):
            continue
        name = (ent.get("name") or "").strip().lower()
        if not name:
            continue
        entity_type = (ent.get("type") or ent.get("entity_type") or "concept").lower().strip()
        if entity_type not in _VALID_ENTITY_TYPES:
            entity_type = "concept"
        description = ent.get("description")
        description = (
            description.strip() if isinstance(description, str) and description.strip() else None
        )
        texts.add(_build_entity_embed_text(entity_type, name, description))
        # Entity alias resolution issues `embed_query(name)`; the per-text
        # cache is shared between query and documents, so caching the bare
        # name lets the resolver hit it too.
        texts.add(name)

    if not texts:
        return

    try:
        embeddings = get_embeddings()
        # Single batched HTTP call — populates the LRU cache for every text.
        await _asyncio.to_thread(embeddings.embed_documents, list(texts))
    except Exception:
        # Non-fatal: if the prewarm fails the downstream calls just embed
        # individually as before. We never want to break extraction over
        # an optimization.
        logger.warning("Extraction embedding prewarm failed", exc_info=True)


async def _dispatch_facts(
    user_id: str,
    question: str,
    answer: str,
    raw_facts: list[dict],
    session_id: str | None,
    *,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    evidence_message_ids: list[int] | None = None,
    evidence_text: str | None = None,
    source_event_time: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, int] | None = None,
) -> int:
    """Reuse memory service parsing + dedup + storage on pre-parsed fact dicts."""
    from ..memory import memory_service
    from ..memory._extractor import extract_facts

    if not raw_facts:
        return 0

    # Feed raw_facts back through the existing extract_facts parser to benefit
    # from its validation, importance clamping, and TTL → expires_at conversion.
    facts = extract_facts(
        content=json.dumps(raw_facts, ensure_ascii=False),
        question=question,
        answer=answer,
        max_facts=settings.MEMORY_MAX_FACTS_PER_TURN,
        evidence_text=evidence_text,
    )
    if diagnostics is not None:
        diagnostics["validated"] = len(facts)
    added = 0
    failures: list[str] = []
    for fact in facts:
        try:
            stored = await memory_service.store_extracted_fact(
                user_id,
                key=fact.key,
                value=fact.value,
                importance=fact.importance,
                expires_at=fact.expires_at,
                category=fact.category,
                confidence=fact.confidence,
                fact_type=fact.fact_type,
                project_id=fact.project_id,
                subject=fact.subject,
                predicate=fact.predicate,
                object_value=fact.object_value,
                valid_from=fact.valid_from or source_event_time,
                valid_to=fact.valid_to,
                evidence_quote=fact.evidence_quote,
                action_status=fact.action_status,
                assignee=fact.assignee,
                due_at=fact.due_at,
                question=question,
                answer=answer,
                session_id=session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text,
                evidence_refs=evidence_refs,
            )
            added += int(stored)
        except Exception:
            failures.append(fact.key)
            logger.warning("Failed to persist extracted fact %s", fact.key, exc_info=True)
    if failures:
        raise RuntimeError("failed to persist extracted facts: " + ", ".join(failures))
    return added
