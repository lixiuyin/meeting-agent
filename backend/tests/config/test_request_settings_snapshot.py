"""Request-scoped configuration remains stable during live reloads."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core._config_snapshot import submit_with_context
from src.core.config import (
    activate_settings_snapshot,
    build_retrieval_profile_snapshot,
    build_settings_snapshot,
    settings,
)
from src.services.chain._api import _request_settings_snapshot


def test_snapshot_is_complete_and_immutable(monkeypatch):
    monkeypatch.setattr(settings, "TOP_K", 7)
    snapshot = build_settings_snapshot(epoch=3)

    assert snapshot.epoch == 3
    assert snapshot.TOP_K == 7
    assert snapshot.top_k == 7
    assert set(snapshot.values) == set(type(settings._live).model_fields)

    try:
        snapshot.values["TOP_K"] = 9  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("snapshot values must be immutable")


def test_active_snapshot_pins_global_reads(monkeypatch):
    monkeypatch.setattr(settings, "TOP_K", 5)
    snapshot = build_settings_snapshot(epoch=1)
    settings.TOP_K = 11

    assert settings.TOP_K == 11
    with activate_settings_snapshot(snapshot):
        assert settings.TOP_K == 5
    assert settings.TOP_K == 11


def test_retrieval_profiles_override_only_the_request(monkeypatch):
    monkeypatch.setattr(settings, "TOP_K", 9)
    monkeypatch.setattr(settings, "MULTI_QUERY_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_BINDING", "cohere")
    monkeypatch.setattr(settings, "RERANKER_TOP_N", 6)

    fast = _request_settings_snapshot(1, "fast")
    thorough = _request_settings_snapshot(1, "thorough")

    assert (fast.TOP_K, fast.MULTI_QUERY_ENABLED, fast.RERANKER_BINDING) == (5, False, "")
    assert (thorough.TOP_K, thorough.MULTI_QUERY_ENABLED, thorough.RERANKER_TOP_N) == (
        16,
        True,
        16,
    )
    assert settings.TOP_K == 9


def test_memory_modes_are_coherent_request_local_presets(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_AUTO_EXTRACT", True)
    monkeypatch.setattr(settings, "MEMORY_MAX_FACTS_PER_TURN", 3)
    monkeypatch.setattr(settings, "MEMORY_MAX_CONTEXT_ITEMS", 6)
    monkeypatch.setattr(settings, "GLOBAL_MEMORY_LIMIT", 3)
    monkeypatch.setattr(settings, "MEMORY_MULTI_HOP_ENABLED", True)
    monkeypatch.setattr(settings, "KNOWLEDGE_GRAPH_ENABLED", False)

    disabled = _request_settings_snapshot(1, "balanced", "off")
    focused = _request_settings_snapshot(1, "balanced", "focused")
    deep = _request_settings_snapshot(1, "balanced", "deep")

    assert (
        disabled.MEMORY_AUTO_EXTRACT,
        disabled.MEMORY_MAX_CONTEXT_ITEMS,
        disabled.KNOWLEDGE_GRAPH_ENABLED,
        disabled.SESSION_SUMMARY_ENABLED,
    ) == (False, 0, False, False)
    assert (
        focused.MEMORY_EXTRACTION_MODE,
        focused.MEMORY_MAX_CONTEXT_ITEMS,
        focused.MEMORY_MULTI_HOP_ENABLED,
    ) == ("precise", 3, False)
    assert (
        deep.MEMORY_EXTRACTION_MODE,
        deep.MEMORY_MAX_CONTEXT_ITEMS,
        deep.KNOWLEDGE_GRAPH_ENABLED,
        deep.SESSION_SUMMARY_ENABLED,
    ) == ("aggressive", 8, True, True)
    assert settings.MEMORY_MAX_CONTEXT_ITEMS == 6


def test_operating_modes_derive_from_one_captured_generation(monkeypatch):
    """Preset bounds must use the same generation as the returned snapshot."""
    captured = settings.snapshot_values(type(settings._live).model_fields)
    captured["TOP_K"] = 12
    captured["MEMORY_MAX_CONTEXT_ITEMS"] = 7
    monkeypatch.setattr(settings, "TOP_K", 2)
    monkeypatch.setattr(settings, "MEMORY_MAX_CONTEXT_ITEMS", 1)
    monkeypatch.setattr(
        type(settings),
        "snapshot_values",
        lambda _self, _fields: dict(captured),
    )

    snapshot = build_retrieval_profile_snapshot(
        epoch=9,
        profile="fast",
        memory_mode="focused",
    )

    assert snapshot.TOP_K == 5
    assert snapshot.MEMORY_MAX_CONTEXT_ITEMS == 3


def test_operating_modes_reject_unknown_internal_values():
    with pytest.raises(ValueError, match="Unknown retrieval profile"):
        build_retrieval_profile_snapshot(epoch=1, profile="turbo")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown memory mode"):
        build_retrieval_profile_snapshot(
            epoch=1,
            profile="balanced",
            memory_mode="forever",  # type: ignore[arg-type]
        )


def test_snapshot_propagates_to_managed_executor_threads(monkeypatch):
    monkeypatch.setattr(settings, "VECTOR_SEARCH_TIMEOUT_S", 8.0)
    snapshot = build_settings_snapshot(
        epoch=4,
        overrides={"VECTOR_SEARCH_TIMEOUT_S": 123.0},
    )

    with ThreadPoolExecutor(max_workers=1) as pool, activate_settings_snapshot(snapshot):
        observed = submit_with_context(pool, lambda: settings.VECTOR_SEARCH_TIMEOUT_S).result()

    assert observed == 123.0
