"""
Intent matching service with multi-layer architecture.

Layer 1: Keyword matching (fast, exact)
Layer 2: Semantic similarity (embedding-based)
Layer 3: LLM routing (complex disambiguation)
"""

import asyncio
import contextlib
import logging
import re
import threading
from typing import Any, TypeVar

import numpy as np
from cachetools import TTLCache

from src.core.config import settings as _settings

from .models import SkillMatchResult, SkillSummary

logger = logging.getLogger(__name__)

T = TypeVar("T")


class KeywordMatcher:
    """
    Fast keyword-based intent matching.

    Checks required keywords, optional keywords, and excluded keywords.
    Returns confidence score based on match ratio.
    """

    def match(self, query: str, config: dict[str, Any]) -> tuple[float, dict]:
        """
        Match query against keyword configuration.

        Args:
            query: User input query
            config: Keyword config with required, optional, excluded, patterns

        Returns:
            Tuple of (score, details_dict)
        """
        query_lower = query.lower()
        details = {
            "required_match": 0.0,
            "optional_match": 0.0,
            "regex_match": 0.0,
        }

        # Check required keywords (all must match)
        required = config.get("required", [])
        if required:
            matched = sum(1 for kw in required if kw.lower() in query_lower)
            if matched < len(required):
                return 0.0, {"reason": "missing_required_keywords"}
            details["required_match"] = 1.0

        # Check optional keywords (more matches = higher score)
        optional = config.get("optional", [])
        if optional:
            matched = sum(1 for kw in optional if kw.lower() in query_lower)
            details["optional_match"] = matched / len(optional)

        # Check excluded keywords (any match = reject)
        excluded = config.get("excluded", [])
        for kw in excluded:
            if kw.lower() in query_lower:
                return 0.0, {"reason": f"excluded_keyword: {kw}"}

        # Check regex patterns
        patterns = config.get("patterns", [])
        for pattern in patterns:
            if re.search(pattern, query, re.IGNORECASE):
                details["regex_match"] = 1.0
                break

        # Calculate weighted score based only on configured dimensions
        # (e.g. do not penalize missing regex when no patterns are defined).
        weights = config.get("weights", {"required": 0.4, "optional": 0.4, "regex": 0.2})

        weighted_sum = 0.0
        total_weight = 0.0

        if required:
            required_weight = float(weights.get("required", 0.4))
            weighted_sum += details["required_match"] * required_weight
            total_weight += required_weight

        if optional:
            optional_weight = float(weights.get("optional", 0.4))
            weighted_sum += details["optional_match"] * optional_weight
            total_weight += optional_weight

        if patterns:
            regex_weight = float(weights.get("regex", 0.2))
            weighted_sum += details["regex_match"] * regex_weight
            total_weight += regex_weight

        score = weighted_sum / total_weight if total_weight > 0 else 0.0

        return score, details


class SemanticMatcher:
    """
    Semantic similarity matching using embeddings.

    Pre-computes embeddings for skill examples at load time.
    Computes cosine similarity between query and skill examples.
    """

    def __init__(self):
        self._embeddings = None
        self._cache: dict[str, np.ndarray] = {}
        # TTL cache for query embeddings — avoids re-calling the embedding API
        # for repeated or similar queries within the TTL window.
        self._query_embed_cache: TTLCache = TTLCache(maxsize=512, ttl=600)
        self._query_embed_lock = threading.Lock()

    def _get_embeddings(self):
        """Lazy load embeddings service."""
        if self._embeddings is None:
            from src.services.embedder import get_embeddings

            self._embeddings = get_embeddings()
        return self._embeddings

    def precompute_skill_embeddings(self, skill: SkillSummary) -> bool:
        """Pre-compute and cache embeddings for skill examples."""
        texts = [*skill.intent_matching.examples, skill.description]
        try:
            embeddings = self._get_embeddings().embed_documents(texts)
        except (ValueError, RuntimeError) as exc:
            logger.warning("Embedding failed for skill %s: %s", skill.name, exc)
            return False
        self._cache[skill.name] = np.array(embeddings)
        logger.debug("Cached %d embeddings for skill: %s", len(texts), skill.name)
        return True

    def embed_query(self, query: str) -> np.ndarray:
        """Embed query once so it can be reused across multiple skills.

        Results are TTL-cached so repeated queries skip the embedding API call.
        """
        with self._query_embed_lock:
            cached = self._query_embed_cache.get(query)
        if cached is not None:
            return cached
        result = np.array(self._get_embeddings().embed_query(query)).reshape(1, -1)
        with self._query_embed_lock:
            self._query_embed_cache[query] = result
        return result

    def match_with_embedding(
        self, skill: SkillSummary, query_embedding: np.ndarray
    ) -> tuple[float, dict]:
        """
        Compute semantic similarity using a pre-computed query embedding.

        Args:
            skill: Skill definition
            query_embedding: Pre-computed query embedding (1, dim)

        Returns:
            Tuple of (score, details_dict)
        """
        if skill.name not in self._cache and not self.precompute_skill_embeddings(skill):
            return 0.0, {"reason": "embedding_failed"}

        skill_embeddings = self._cache[skill.name]

        from sklearn.metrics.pairwise import cosine_similarity

        similarities = cosine_similarity(query_embedding, skill_embeddings)[0]

        top_k = min(3, len(similarities))
        top_scores = np.sort(similarities)[-top_k:]
        score = float(np.mean(top_scores))

        best_example_idx = np.argmax(similarities[:-1]) if len(similarities) > 1 else 0
        best_example = (
            skill.intent_matching.examples[best_example_idx]
            if skill.intent_matching.examples
            and best_example_idx < len(skill.intent_matching.examples)
            else ""
        )

        return score, {"top_similarities": top_scores.tolist(), "best_match_example": best_example}

    def match(self, query: str, skill: SkillSummary) -> tuple[float, dict]:
        """
        Compute semantic similarity between query and skill.

        Args:
            query: User input query
            skill: Skill definition

        Returns:
            Tuple of (score, details_dict)
        """
        query_embedding = self.embed_query(query)
        return self.match_with_embedding(skill, query_embedding)


class LLMRouter:
    """
    LLM-based routing for complex intent disambiguation.

    Only invoked when multiple skills have similar confidence scores.
    Uses few-shot prompting for accurate routing decisions.
    """

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        """Lazy load LLM service."""
        if self._llm is None:
            from src.services.llm import get_llm

            self._llm = get_llm()
        return self._llm

    async def route(self, query: str, candidates: list[SkillSummary]) -> tuple[str, float, str]:
        """
        Use LLM to select best skill from candidates.

        Args:
            query: User input query
            candidates: List of candidate skills (typically top 2-3)

        Returns:
            Tuple of (selected_skill_name, confidence, reasoning)
        """
        skills_desc = "\n\n".join(
            [
                f"Skill: {s.name}\n"
                f"Description: {s.description[:100]}...\n"
                f"Examples: {', '.join(s.intent_matching.examples[:2])}"
                for s in candidates
            ]
        )

        prompt = f"""You are a skill routing agent. Select the best skill for the user's query.

User Query: "{query}"

Candidate Skills:
{skills_desc}

Respond in this exact format:
SKILL: <skill_name>
CONFIDENCE: <0.0-1.0>
REASONING: <brief explanation>
"""

        try:
            response = await asyncio.wait_for(
                self._get_llm().ainvoke(prompt),
                timeout=_settings.SKILL_ROUTE_TIMEOUT_S,
            )
        except TimeoutError:
            logger.warning("LLM route timed out, returning first candidate")
            return candidates[0].name, 0.5, "timeout_fallback"
        content = response.content if hasattr(response, "content") else str(response)

        # Parse response
        skill_name = candidates[0].name  # Default fallback
        confidence = 0.5
        reasoning = ""

        for line in content.strip().split("\n"):
            if line.startswith("SKILL:"):
                skill_name = line.split(":", 1)[1].strip()
            elif line.startswith("CONFIDENCE:"):
                with contextlib.suppress(ValueError):
                    confidence = float(line.split(":", 1)[1].strip())
            elif line.startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        return skill_name, confidence, reasoning


class IntentMatchingService:
    """
    Main service for matching user queries to skills.

    Orchestrates keyword, semantic, and LLM-based matching in layers.
    Returns best matching skill with confidence score.
    """

    def __init__(self):
        self.keyword_matcher = KeywordMatcher()
        self.semantic_matcher = SemanticMatcher()
        self.llm_router = LLMRouter()
        # TTL cache for match results — same (query, skill_set) returns cached
        # result without re-running embedding or LLM calls.
        self._match_cache: TTLCache = TTLCache(maxsize=256, ttl=300)
        self._match_cache_lock = threading.Lock()
        self._NO_MATCH_SENTINEL = object()

    async def match(
        self, query: str, skills: list[SkillSummary], use_llm: bool = True
    ) -> SkillMatchResult | None:
        """
        Find best matching skill for user query.

        Args:
            query: User input query
            skills: List of available skills
            use_llm: Whether to use LLM routing for ambiguous cases

        Returns:
            Best matching result or None if no match
        """
        if not skills:
            return None

        # Check match result cache
        skill_names_key = tuple(sorted(s.name for s in skills))
        cache_key = (query, skill_names_key)
        with self._match_cache_lock:
            cached = self._match_cache.get(cache_key)
        if cached is not None:
            if cached is self._NO_MATCH_SENTINEL:
                return None
            return cached

        # Pre-compute query embedding once if any skill needs semantic matching
        needs_semantic = any(
            s.intent_matching.method in ("semantic", "hybrid") and s.intent_matching.examples
            for s in skills
        )
        query_embedding = None
        if needs_semantic:
            try:
                query_embedding = await asyncio.wait_for(
                    asyncio.to_thread(self.semantic_matcher.embed_query, query),
                    timeout=_settings.SEMANTIC_EMBED_TIMEOUT_S,
                )
            except TimeoutError:
                logger.warning(
                    "semantic embed timed out after %.1fs",
                    _settings.SEMANTIC_EMBED_TIMEOUT_S,
                )
                query_embedding = None
            except Exception as exc:
                logger.warning("semantic embed failed: %s", exc)
                query_embedding = None

        results = []

        for skill in skills:
            config = skill.intent_matching
            scores = {}

            # Layer 1: Keyword matching
            if config.method in ("keyword", "hybrid"):
                kw_score, kw_details = self.keyword_matcher.match(query, config.keywords)
                scores["keyword"] = kw_score
                scores["keyword_details"] = kw_details

            # Layer 2: Semantic matching (reuses pre-computed query embedding)
            if config.method in ("semantic", "hybrid") and config.examples:
                if query_embedding is not None:
                    try:
                        sem_score, sem_details = await asyncio.to_thread(
                            self.semantic_matcher.match_with_embedding,
                            skill,
                            query_embedding,
                        )
                    except Exception as exc:
                        logger.warning("Semantic match failed for %s: %s", skill.name, exc)
                        sem_score, sem_details = 0.0, {"reason": "semantic_error"}
                else:
                    sem_score, sem_details = 0.0, {"reason": "no_query_embedding"}
                scores["semantic"] = sem_score
                scores["semantic_details"] = sem_details

            # Calculate preliminary score
            if config.method == "hybrid":
                kw_weight = config.keywords.get("weight", 0.4) if config.keywords else 0.4
                sem_weight = config.keywords.get("semantic_weight", 0.4) if config.keywords else 0.4
                preliminary_score = (
                    scores.get("keyword", 0) * kw_weight + scores.get("semantic", 0) * sem_weight
                )
            else:
                preliminary_score = scores.get(config.method, 0)

            # Skip if below threshold
            if preliminary_score < config.threshold * 0.5:
                continue

            results.append(
                SkillMatchResult(
                    skill=skill,
                    score=preliminary_score,
                    matched=preliminary_score >= config.threshold,
                    details=scores,
                )
            )

        if not results:
            with self._match_cache_lock:
                self._match_cache[cache_key] = self._NO_MATCH_SENTINEL
            return None

        # Sort by score
        results.sort(key=lambda x: x.score, reverse=True)
        best = results[0]

        # Check for ambiguity (top 2 are close)
        min_similarity_for_llm = getattr(_settings, "SKILL_ROUTING_MIN_SIMILARITY", 0.0)
        if (
            len(results) > 1
            and use_llm
            and best.skill.intent_matching.llm_routing.get("enabled", False)
            and best.score >= min_similarity_for_llm
        ):
            second = results[1]
            if best.score - second.score < 0.1:  # Less than 0.1 difference
                # Layer 3: LLM routing
                top_candidates = results[:3]
                selected_name, llm_conf, reasoning = await self.llm_router.route(
                    query, [r.skill for r in top_candidates]
                )

                # Update score with LLM confidence
                llm_weight = best.skill.intent_matching.llm_routing.get("weight", 0.2)
                if selected_name == best.skill.name:
                    best.score = best.score * (1 - llm_weight) + llm_conf * llm_weight

                best.details["llm_confidence"] = llm_conf
                best.details["llm_reasoning"] = reasoning
                best.ambiguous = True
                best.alternatives = [r.skill for r in results[1:3]]

        # Final threshold check
        if best.score < best.skill.intent_matching.threshold:
            with self._match_cache_lock:
                self._match_cache[cache_key] = self._NO_MATCH_SENTINEL
            return None

        with self._match_cache_lock:
            self._match_cache[cache_key] = best
        return best
