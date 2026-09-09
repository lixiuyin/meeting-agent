"""MemoryService consolidation mixin."""

import asyncio
import calendar
import json
import time
from typing import Protocol, cast

from ....core.config import settings as global_settings
from ....core.metrics import MEMORY_MERGE_TOTAL
from .._common import logger
from .._parsers import _parse_consolidation_json, _semantic_cluster_memories, _text_cluster_memories
from . import settings


class _ConsolidationHost(Protocol):
    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
        importance: int = 3,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        supersedes: list[str] | None = None,
    ) -> None: ...


def _union_cluster_scope(cluster: list[dict], field: str) -> list[int] | None:
    """Union normalized provenance IDs across every source memory."""
    values: set[int] = set()
    for memory in cluster:
        raw = memory.get(field)
        parts = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
        for part in parts:
            try:
                values.add(int(str(part).strip()))
            except (TypeError, ValueError):
                continue
    return sorted(values) or None


class _MemoryConsolidationMixin:
    async def _resolve_contradiction(
        self,
        *,
        existing_key: str,
        existing_value: str,
        new_key: str,
        new_value: str,
    ) -> str:
        """Use LLM to resolve a conflict between an existing and a new memory.

        Returns one of: "update" | "contradiction" | "complement".
        Defaults to "complement" on any failure.
        """
        try:
            import re

            from ...llm import (
                cached_retry_invoke,
                escape_prompt_data,
                get_contradiction_resolution_prompt,
                get_llm,
            )

            llm = get_llm()
            prompt_template = get_contradiction_resolution_prompt()
            prompt = prompt_template.format(
                existing_key=escape_prompt_data(existing_key),
                existing_value=escape_prompt_data(existing_value),
                new_key=escape_prompt_data(new_key),
                new_value=escape_prompt_data(new_value),
            )
            response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                logger.warning(
                    "Contradiction resolver received non-text response for %s vs %s; "
                    "defaulting to 'complement' (M-5)",
                    existing_key,
                    new_key,
                )
                return "complement"

            text = content.strip()
            fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1)

            result = __import__("json").loads(text)
            resolution = result.get("resolution", "complement")
            if resolution in ("update", "contradiction", "complement"):
                return resolution
        except Exception:
            logger.warning(
                "Contradiction resolution failed for %s vs %s",
                existing_key,
                new_key,
                exc_info=True,
            )
        return "complement"

    async def _merge_memory_clusters_batch(
        self, user_id: str, clusters: list[tuple[list[dict], str]]
    ) -> int:
        """LLM-merge multiple memory clusters in a single batched prompt (M-MEM-2).

        Sends all clusters at once and parses the structured JSON array output,
        avoiding per-cluster LLM round-trips.  Falls back to per-cluster merging
        when batching fails.
        """
        if not clusters:
            return 0
        if len(clusters) == 1:
            cluster, category = clusters[0]
            ok = await self._merge_memory_cluster(user_id, cluster, category)
            return 1 if ok else 0

        try:
            from ...llm import cached_retry_invoke, get_llm

            cluster_blocks: list[str] = []
            for i, (cluster, category) in enumerate(clusters):
                facts = "\n".join(
                    f"- key: {json.dumps(m['key'])}, value: {json.dumps(m['value'])}"
                    for m in cluster
                )
                cluster_blocks.append(f"Cluster {i} (category: {category}):\n{facts}")
            all_clusters_text = "\n\n".join(cluster_blocks)

            llm = get_llm()
            prompt = (
                "You are consolidating related memories. For each cluster below, "
                "merge the facts into a single consolidated memory. "
                "Return a JSON array with one object per cluster:\n"
                '[{"key": "...", "value": "...", "importance": int, "category": "..."}]\n\n'
                f"{all_clusters_text}"
            )
            response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                return 0

            import re as _re

            text: str = str(content).strip()
            m = _re.search(r"\[.*\]", text, _re.DOTALL)
            if not m:
                return 0
            results = __import__("json").loads(m.group(0))
            if not isinstance(results, list) or len(results) != len(clusters):
                return 0

            consolidated = 0
            host = cast(_ConsolidationHost, self)
            for i, result in enumerate(results):
                if not isinstance(result, dict):
                    continue
                cluster, category = clusters[i]
                consolidated_key = (result.get("key") or "").strip()
                consolidated_value = (result.get("value") or "").strip()
                if not consolidated_key or not consolidated_value:
                    continue

                consolidated_importance = min(
                    settings.MEMORY_MAX_IMPORTANCE,
                    max(
                        settings.MEMORY_MIN_IMPORTANCE,
                        int(result.get("importance", settings.MEMORY_INITIAL_IMPORTANCE + 1)),
                    ),
                )
                consolidated_category = result.get("category") or category

                superseded_keys = [m["key"] for m in cluster if m["key"] != consolidated_key]
                try:
                    host.set(
                        user_id,
                        consolidated_key,
                        consolidated_value,
                        source="consolidated",
                        importance=consolidated_importance,
                        category=consolidated_category,
                        meeting_ids=_union_cluster_scope(cluster, "meeting_ids"),
                        file_ids=_union_cluster_scope(cluster, "file_ids"),
                        supersedes=superseded_keys,
                    )
                except Exception:
                    logger.warning(
                        "HIGH-6: Consolidated write failed for key '%s' — "
                        "skipping supersede marking to prevent data loss",
                        consolidated_key,
                        exc_info=True,
                    )
                    continue
                consolidated += 1
                MEMORY_MERGE_TOTAL.labels(status="success").inc()

            if consolidated:
                logger.debug(
                    "Batch-consolidated %d/%d clusters for user %s",
                    consolidated,
                    len(clusters),
                    user_id,
                )
            return consolidated
        except Exception:
            logger.warning("Batch consolidation failed; falling back to per-cluster", exc_info=True)
            # Fall back to per-cluster merging
            ok = 0
            for cluster, category in clusters:
                try:
                    if await self._merge_memory_cluster(user_id, cluster, category):
                        ok += 1
                except Exception:
                    logger.debug("Per-cluster merge failed", exc_info=True)
            return ok

    async def _merge_memory_cluster(self, user_id: str, cluster: list[dict], category: str) -> bool:
        """LLM-merge a single cluster of related memories into one consolidated fact."""
        try:
            from ...llm import (
                cached_retry_invoke,
                escape_prompt_data,
                get_llm,
                get_memory_consolidation_prompt,
            )

            facts_text = "\n".join(
                f"- key: {json.dumps(m['key'])}, value: {json.dumps(m['value'])}" for m in cluster
            )
            llm = get_llm()
            prompt_template = get_memory_consolidation_prompt()
            prompt = prompt_template.format(facts=escape_prompt_data(facts_text))
            response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                return False

            result = _parse_consolidation_json(content)
            if not result:
                return False

            consolidated_key = (result.get("key") or "").strip()
            consolidated_value = (result.get("value") or "").strip()
            if not consolidated_key or not consolidated_value:
                return False

            consolidated_importance = min(
                settings.MEMORY_MAX_IMPORTANCE,
                max(
                    settings.MEMORY_MIN_IMPORTANCE,
                    int(result.get("importance", settings.MEMORY_INITIAL_IMPORTANCE + 1)),
                ),
            )
            consolidated_category = result.get("category") or category

            superseded_keys = [m["key"] for m in cluster if m["key"] != consolidated_key]
            host = cast(_ConsolidationHost, self)
            try:
                host.set(
                    user_id,
                    consolidated_key,
                    consolidated_value,
                    source="consolidated",
                    importance=consolidated_importance,
                    category=consolidated_category,
                    meeting_ids=_union_cluster_scope(cluster, "meeting_ids"),
                    file_ids=_union_cluster_scope(cluster, "file_ids"),
                    supersedes=superseded_keys,
                )
            except Exception:
                logger.warning(
                    "HIGH-6: Consolidated write failed for key '%s' — "
                    "skipping supersede marking to prevent data loss",
                    consolidated_key,
                    exc_info=True,
                )
                return False

            logger.debug(
                "Consolidated %d memories into '%s' for user %s",
                len(cluster),
                consolidated_key,
                user_id,
            )
            MEMORY_MERGE_TOTAL.labels(status="success").inc()
            return True
        except Exception:
            logger.warning("Cluster merge failed", exc_info=True)
            MEMORY_MERGE_TOTAL.labels(status="error").inc()
            return False

    async def consolidate_memories(self, user_id: str) -> int:
        """Merge semantically related memories into consolidated facts.

        Uses incremental strategy: only memories written in the last
        ``MEMORY_CONSOLIDATION_WINDOW_DAYS`` are used as seeds. Each seed is then
        compared against all existing memories for clustering, keeping
        the comparison bounded.
        """
        if not settings.MEMORY_CONSOLIDATION_ENABLED:
            return 0
        try:
            from ....core import database as db

            cutoff_ts = time.time() - global_settings.MEMORY_CONSOLIDATION_WINDOW_DAYS * 86400
            with db.get_connection() as conn:
                all_memories = db.get_memories_for_consolidation(conn, user_id=user_id, limit=200)

            if len(all_memories) < settings.MEMORY_CONSOLIDATION_MIN_CLUSTER:
                return 0

            # Split into recent seeds and older candidates
            recent: list[dict] = []
            older: list[dict] = []
            for m in all_memories:
                updated_at = m.get("updated_at", "")
                try:
                    ts = calendar.timegm(time.strptime(updated_at, "%Y-%m-%d %H:%M:%S"))
                    if ts >= cutoff_ts:
                        recent.append(m)
                    else:
                        older.append(m)
                except (ValueError, TypeError):
                    logger.warning(
                        "Failed to parse updated_at timestamp for memory %s: %r",
                        m.get("key"),
                        updated_at,
                    )
                    older.append(m)

            # If no recent memories, skip (nothing new to consolidate)
            if not recent:
                return 0

            # Build candidate pool: recent seeds + older memories for pairing
            candidates = recent + older

            by_category: dict[str, list[dict]] = {}
            for m in candidates:
                cat = m.get("category") or "general"
                by_category.setdefault(cat, []).append(m)

            consolidated_count = 0
            # M-MEM-2: Collect all cluster+category pairs for batched LLM merge.
            merge_batch: list[tuple[list[dict], str]] = []
            for category, group in by_category.items():
                if len(group) < settings.MEMORY_CONSOLIDATION_MIN_CLUSTER:
                    continue
                # MEM-8: Stable sort by key for deterministic clustering results.
                group.sort(key=lambda m: (str(m.get("user_id") or ""), str(m.get("key") or "")))
                if settings.MEMORY_SEMANTIC_CLUSTER_ENABLED:
                    clusters = await _semantic_cluster_memories(group)
                else:
                    clusters = _text_cluster_memories(group)
                for cluster in clusters:
                    if len(cluster) < settings.MEMORY_CONSOLIDATION_MIN_CLUSTER:
                        continue
                    if not any(m in recent for m in cluster):
                        continue
                    merge_batch.append((cluster, category))
            if merge_batch:
                consolidated_count = await self._merge_memory_clusters_batch(user_id, merge_batch)

            if consolidated_count:
                from ....core.audit import audit_log

                logger.info(
                    "Incremental consolidation: %d clusters for user %s (seeds=%d, pool=%d)",
                    consolidated_count,
                    user_id,
                    len(recent),
                    len(candidates),
                )
                audit_log(
                    "consolidate",
                    "memory",
                    user_id,
                    user_id=user_id,
                    detail=(
                        f"clusters={consolidated_count} seeds={len(recent)} pool={len(candidates)}"
                    ),
                )
            return consolidated_count
        except Exception:
            logger.warning("Memory consolidation failed for user %s", user_id, exc_info=True)
            return 0
