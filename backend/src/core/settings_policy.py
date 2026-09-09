"""Runtime settings change classification and enforcement policy."""

from __future__ import annotations

from typing import Any

from .index_manifest import INDEX_CONFIG_FIELDS

RESTART_REQUIRED_FIELDS = frozenset(
    {
        "DB_PATH",
        "UPLOAD_DIR",
        "VECTOR_DB_DIR",
        "CUSTOM_SKILLS_DIR",
        "DURABLE_JOB_EXECUTION_MODE",
        "DURABLE_JOB_WORKERS",
        "HOST",
        "PORT",
    }
)

RESETTABLE_FIELDS = frozenset(
    {
        "LLM_BINDING",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_HOST",
        "EMBEDDING_API_KEY",
        "RERANKER_BINDING",
        "RERANKER_MODEL",
        "RERANKER_API_KEY",
        "RERANKER_BASE_URL",
        "QUERY_REWRITE_MODEL",
        "RAGANYTHING_WORKING_DIR",
    }
)


def classify_settings_changes(before: Any, after: Any) -> dict[str, list[str]]:
    """Classify changed Settings fields by their safe activation boundary."""
    changed = {
        name
        for name in after.__class__.model_fields
        if getattr(before, name) != getattr(after, name)
    }
    reindex = changed & set(INDEX_CONFIG_FIELDS)
    restart = changed & set(RESTART_REQUIRED_FIELDS)
    resettable = changed & set(RESETTABLE_FIELDS) - reindex - restart
    hot = changed - reindex - restart - resettable
    return {
        "hot": sorted(hot),
        "resettable": sorted(resettable),
        "reindex_required": sorted(reindex),
        "restart_required": sorted(restart),
    }


def settings_activation_policy(settings_type: type[Any]) -> dict[str, list[str]]:
    """Describe the activation boundary for every runtime Settings field."""
    all_fields = set(settings_type.model_fields)
    reindex = all_fields & set(INDEX_CONFIG_FIELDS)
    restart = all_fields & set(RESTART_REQUIRED_FIELDS)
    resettable = all_fields & set(RESETTABLE_FIELDS) - reindex - restart
    hot = all_fields - reindex - restart - resettable
    return {
        "hot": sorted(hot),
        "resettable": sorted(resettable),
        "reindex_required": sorted(reindex),
        "restart_required": sorted(restart),
    }
