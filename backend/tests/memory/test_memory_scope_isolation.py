"""Tests for meeting/file scope isolation in memory and KG retrieval.

Covers the fix that prevents cross-meeting context pollution when a chat
question targets a specific meeting or file:

- Extracted memories are persisted with their originating scope IDs.
- ``search_semantic`` filters by both ``meeting_ids`` and ``file_ids``.
- Strict mode excludes untagged (global) memories except ``user_profile``.
- Knowledge-graph entities are tagged with and filtered by scope.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.memory import MemoryService


class TestSearchSemanticFileScope:
    @pytest.mark.asyncio
    async def test_file_ids_filter_excludes_non_matching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.core.config import settings

        service = MemoryService()
        user_id = "scope_file_user"

        vector_results = [
            {"key": "file_a", "score": 0.01, "meeting_ids": None, "file_ids": [11]},
            {"key": "file_b", "score": 0.02, "meeting_ids": None, "file_ids": [22]},
            {"key": "meeting_a", "score": 0.03, "meeting_ids": [1], "file_ids": None},
            {"key": "global", "score": 0.04, "meeting_ids": None, "file_ids": None},
        ]
        batch = {
            k: {"value": f"val {k}", "importance": 3, "updated_at": "2026-01-01"}
            for k in ("file_a", "file_b", "meeting_a", "global")
        }
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = vector_results

        monkeypatch.setattr(settings, "GLOBAL_MEMORY_LIMIT", 5)
        monkeypatch.setattr(settings, "SCOPED_MEMORY_STRICT", False)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value=batch,
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            entries = await service.search_semantic(user_id, query="q", limit=10, file_ids=[11])

        keys = [e.key for e in entries]
        assert "file_a" in keys  # matches file scope
        assert "file_b" not in keys  # wrong file
        assert "meeting_a" not in keys  # meeting-scoped, file filter doesn't match
        assert "global" in keys  # global allowed in non-strict mode

    @pytest.mark.asyncio
    async def test_strict_mode_excludes_globals_except_user_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.core.config import settings

        service = MemoryService()
        user_id = "strict_user"

        vector_results = [
            {
                "key": "scoped",
                "score": 0.01,
                "meeting_ids": [1],
                "file_ids": None,
                "category": "fact",
            },
            {
                "key": "global_fact",
                "score": 0.02,
                "meeting_ids": None,
                "file_ids": None,
                "category": "fact",
            },
            {
                "key": "user_name",
                "score": 0.03,
                "meeting_ids": None,
                "file_ids": None,
                "category": "user_profile",
            },
        ]
        batch = {
            "scoped": {
                "value": "scoped",
                "importance": 3,
                "category": "fact",
                "updated_at": "2026-01-01",
            },
            "global_fact": {
                "value": "global",
                "importance": 3,
                "category": "fact",
                "updated_at": "2026-01-01",
            },
            "user_name": {
                "value": "Alice",
                "importance": 5,
                "category": "user_profile",
                "updated_at": "2026-01-01",
            },
        }
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = vector_results

        monkeypatch.setattr(settings, "SCOPED_MEMORY_STRICT", True)
        monkeypatch.setattr(settings, "GLOBAL_MEMORY_LIMIT", 5)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value=batch,
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            entries = await service.search_semantic(user_id, query="q", limit=10, meeting_ids=[1])

        keys = [e.key for e in entries]
        assert "scoped" in keys
        assert "user_name" in keys  # user_profile always passes
        assert "global_fact" not in keys  # strict mode drops non-profile globals

    @pytest.mark.asyncio
    async def test_no_scope_returns_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = MemoryService()
        user_id = "no_scope_user"

        vector_results = [
            {"key": "a", "score": 0.01, "meeting_ids": [1], "file_ids": None},
            {"key": "b", "score": 0.02, "meeting_ids": [2], "file_ids": None},
            {"key": "c", "score": 0.03, "meeting_ids": None, "file_ids": None},
        ]
        batch = {
            k: {"value": k, "importance": 3, "updated_at": "2026-01-01"} for k in ("a", "b", "c")
        }
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = vector_results

        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value=batch,
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            entries = await service.search_semantic(user_id, query="q", limit=10)

        assert {"a", "b", "c"} == {e.key for e in entries}


class TestMemoryScopePersistence:
    def test_set_memory_persists_scope_ids(self) -> None:
        """set_memory() writes meeting_ids/file_ids to user_memories row."""
        from src.core import database as db
        from src.core.database import get_connection, get_write_connection
        from src.core.database._scopes import get_scopes

        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id="scope_persist",
                key="fact1",
                value="v1",
                meeting_ids=[42, 43],
                file_ids=[7],
            )

        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM user_memories WHERE user_id=? AND key=?",
                ("scope_persist", "fact1"),
            ).fetchone()
            assert row is not None
            mids, fids = get_scopes(conn, kind="memory", owner_id=int(row["id"]))

        assert sorted(mids) == [42, 43]
        assert fids == [7]

    def test_same_value_reconfirmation_accumulates_scope(self) -> None:
        """The same fact may be confirmed by additional meetings."""
        from src.core import database as db
        from src.core.database import get_connection, get_write_connection
        from src.core.database._scopes import get_scopes

        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id="scope_union",
                key="k1",
                value="v1",
                meeting_ids=[10],
            )
            db.set_memory(
                conn,
                user_id="scope_union",
                key="k1",
                value="v1",
                meeting_ids=[20],
            )
            # Same ID again — should dedupe via INSERT OR IGNORE
            db.set_memory(
                conn,
                user_id="scope_union",
                key="k1",
                value="v1",
                meeting_ids=[10],
            )

        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM user_memories WHERE user_id=? AND key=?",
                ("scope_union", "k1"),
            ).fetchone()
            assert row is not None
            mids, _ = get_scopes(conn, kind="memory", owner_id=int(row["id"]))

        assert set(mids) == {10, 20}

    def test_value_change_cannot_silently_broaden_scope(self) -> None:
        from src.core import database as db
        from src.core.database import get_connection, get_write_connection
        from src.core.database.memories import MemoryScopeConflictError

        with get_write_connection() as conn:
            db.set_memory(
                conn, user_id="scope_conflict", key="owner", value="Alice", meeting_ids=[1]
            )

        with pytest.raises(MemoryScopeConflictError):
            with get_write_connection() as conn:
                db.set_memory(
                    conn,
                    user_id="scope_conflict",
                    key="owner",
                    value="Bob",
                    meeting_ids=[2],
                )

        with get_connection() as conn:
            row = db.get_memory_full(conn, user_id="scope_conflict", key="owner")
        assert row["value"] == "Alice"
        assert row["meeting_ids"] == "1"


class TestLegacyScopeFlag:
    """Migration v29 flags pre-scope rows so they don't pollute scoped queries."""

    def test_existing_untagged_rows_are_flagged_legacy(self) -> None:
        """Rows inserted without scope IDs default is_legacy_scope=0 under the
        new path, but migration v29 backfills pre-existing rows to 1."""
        from src.core.database import get_connection, get_write_connection

        # Directly insert a row with NULL scope to simulate pre-migration state,
        # then run the UPDATE that migration v29 performs.
        with get_write_connection() as conn:
            conn.execute(
                "INSERT INTO user_memories (user_id, key, value, source) VALUES (?, ?, ?, ?)",
                ("legacy_user", "old_fact", "pre-scope value", "manual"),
            )
            conn.execute(
                "UPDATE user_memories SET is_legacy_scope=1 "
                "WHERE meeting_ids IS NULL AND file_ids IS NULL"
            )

        with get_connection() as conn:
            row = conn.execute(
                "SELECT is_legacy_scope FROM user_memories WHERE user_id=? AND key=?",
                ("legacy_user", "old_fact"),
            ).fetchone()

        assert row is not None
        assert row["is_legacy_scope"] == 1

    @pytest.mark.asyncio
    async def test_legacy_global_excluded_from_scoped_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy-flagged globals are dropped from scoped queries but show
        up in unscoped queries."""
        from src.core.config import settings

        service = MemoryService()
        user_id = "legacy_scope_user"

        vector_results = [
            {
                "key": "scoped_fact",
                "score": 0.01,
                "meeting_ids": [1],
                "file_ids": None,
                "category": None,
            },
            {
                "key": "legacy_fact",
                "score": 0.02,
                "meeting_ids": None,
                "file_ids": None,
                "category": None,
            },
            {
                "key": "new_global_fact",
                "score": 0.03,
                "meeting_ids": None,
                "file_ids": None,
                "category": None,
            },
        ]
        batch = {
            "scoped_fact": {
                "value": "v1",
                "importance": 3,
                "updated_at": "2026-01-01",
                "is_legacy_scope": 0,
            },
            "legacy_fact": {
                "value": "old",
                "importance": 3,
                "updated_at": "2026-01-01",
                "is_legacy_scope": 1,
            },
            "new_global_fact": {
                "value": "new",
                "importance": 3,
                "updated_at": "2026-01-01",
                "is_legacy_scope": 0,
            },
        }
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = vector_results

        monkeypatch.setattr(settings, "SCOPED_MEMORY_STRICT", False)
        monkeypatch.setattr(settings, "GLOBAL_MEMORY_LIMIT", 5)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value=batch,
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            scoped = await service.search_semantic(user_id, query="q", limit=10, meeting_ids=[1])
            unscoped = await service.search_semantic(user_id, query="q", limit=10)

        scoped_keys = [e.key for e in scoped]
        unscoped_keys = [e.key for e in unscoped]

        assert "scoped_fact" in scoped_keys
        assert "new_global_fact" in scoped_keys
        assert "legacy_fact" not in scoped_keys  # excluded under scope

        # All three visible in unscoped query — legacy memories are preserved
        # and still accessible when no meeting/file is selected.
        assert {"scoped_fact", "legacy_fact", "new_global_fact"} <= set(unscoped_keys)


class TestOversampleFactor:
    """Verify the oversample factor is applied when scope is active."""

    @pytest.mark.asyncio
    async def test_scope_active_triggers_larger_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.core.config import settings

        service = MemoryService()
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = []

        monkeypatch.setattr(settings, "MEMORY_SEARCH_OVERSAMPLE_FACTOR", 7)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value={},
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            await service.search_semantic("user", query="q", limit=5, meeting_ids=[1])

        # With meeting_ids passed, fetch_multiplier should be 7 (not the 2 default)
        assert mock_vs.similarity_search.called
        kwargs = mock_vs.similarity_search.call_args.kwargs
        assert kwargs.get("fetch_multiplier") == 7

    @pytest.mark.asyncio
    async def test_no_scope_uses_default_multiplier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.config import settings

        service = MemoryService()
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = []

        monkeypatch.setattr(settings, "MEMORY_SEARCH_OVERSAMPLE_FACTOR", 7)
        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                return_value={},
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                return_value=["preselected"],
            ),
            patch.object(service, "search_important", return_value=[]),
        ):
            await service.search_semantic("user", query="q", limit=5)

        kwargs = mock_vs.similarity_search.call_args.kwargs
        assert kwargs.get("fetch_multiplier") == 2


class TestImportantFallbackScope:
    @pytest.mark.asyncio
    async def test_database_fallback_preserves_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.config import settings

        service = MemoryService()
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = []
        important = [
            {
                "key": "meeting_fact",
                "value": "scoped",
                "importance": 4,
                "meeting_ids": "17",
                "file_ids": None,
                "updated_at": "2026-01-01",
            }
        ]
        monkeypatch.setattr(settings, "SCOPED_MEMORY_STRICT", True)

        def _allowed_keys(_conn, *, meeting_ids=None, **_kwargs):
            return ["meeting_fact"] if meeting_ids == [17] else []

        def _rows(_conn, *, keys, **_kwargs):
            return {"meeting_fact": important[0]} if "meeting_fact" in keys else {}

        with (
            patch(
                "src.services.memory._service._search.get_memory_vectorstore",
                return_value=mock_vs,
            ),
            patch(
                "src.services.memory._service._search.db.list_memory_keys_for_scope",
                side_effect=_allowed_keys,
            ),
            patch(
                "src.services.memory._service._search.db.get_memories_batch",
                side_effect=_rows,
            ),
        ):
            matching = await service.search_semantic("user", query="q", limit=5, meeting_ids=[17])
            foreign = await service.search_semantic("user", query="q", limit=5, meeting_ids=[99])

        assert [entry.key for entry in matching] == ["meeting_fact"]
        assert foreign == []


class TestDiversifyByMeeting:
    """Verify _diversify_by_meeting caps per-meeting contribution."""

    def test_caps_per_meeting_preserves_order(self) -> None:
        from src.services.rag._retriever import _diversify_by_meeting

        results = [
            {"content": "a1", "metadata": {"meeting_id": 1}, "score": 0.1},
            {"content": "a2", "metadata": {"meeting_id": 1}, "score": 0.2},
            {"content": "a3", "metadata": {"meeting_id": 1}, "score": 0.3},
            {"content": "a4", "metadata": {"meeting_id": 1}, "score": 0.4},
            {"content": "b1", "metadata": {"meeting_id": 2}, "score": 0.5},
            {"content": "c1", "metadata": {"meeting_id": 3}, "score": 0.6},
        ]
        out = _diversify_by_meeting(results, max_per_meeting=2)

        # First 4 entries (head): 2 from meeting 1, then b1 (meeting 2), c1 (meeting 3)
        head_contents = [r["content"] for r in out[:4]]
        assert head_contents == ["a1", "a2", "b1", "c1"]
        # Overflow from meeting 1 (a3, a4) appended to tail
        tail_contents = [r["content"] for r in out[4:]]
        assert set(tail_contents) == {"a3", "a4"}

    def test_missing_meeting_id_handled(self) -> None:
        from src.services.rag._retriever import _diversify_by_meeting

        # Docs without a meeting_id (e.g. orphan chunks) share the None bucket
        results = [
            {"content": "x", "metadata": {}, "score": 0.1},
            {"content": "y", "metadata": {}, "score": 0.2},
            {"content": "z", "metadata": {}, "score": 0.3},
        ]
        out = _diversify_by_meeting(results, max_per_meeting=2)
        assert [r["content"] for r in out] == ["x", "y", "z"]


class TestFactExtractionPromptSchema:
    """The prompt asks the LLM to produce structured keys."""

    def test_prompt_mentions_schema(self) -> None:
        from src.services.llm._prompts import (
            COMBINED_EXTRACTION_PROMPT,
            FACT_EXTRACTION_PROMPT,
        )

        for prompt in (FACT_EXTRACTION_PROMPT, COMBINED_EXTRACTION_PROMPT):
            assert "category" in prompt
            assert "subject" in prompt
            assert "attribute" in prompt
            assert "user_profile" in prompt


class TestMemoryTimeline:
    def test_timeline_walks_supersede_chain(self) -> None:
        """get_memory_timeline returns the ordered chain of superseded values."""
        from src.core import database as db
        from src.core.database import get_connection, get_write_connection

        user_id = "timeline_user"
        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id=user_id,
                key="profile.user.editor",
                value="VSCode",
                source="auto_extracted",
            )
            conn.execute(
                "UPDATE user_memories SET updated_at='2026-01-01' WHERE user_id=? AND key=?",
                (user_id, "profile.user.editor"),
            )
            db.set_memory(
                conn,
                user_id=user_id,
                key="profile.user.editor_v2",
                value="Cursor",
                source="auto_extracted",
            )
            conn.execute(
                "UPDATE user_memories SET updated_at='2026-03-01' WHERE user_id=? AND key=?",
                (user_id, "profile.user.editor_v2"),
            )
            db.mark_memory_superseded(
                conn,
                user_id=user_id,
                key="profile.user.editor",
                superseded_by="profile.user.editor_v2",
            )
            # Pin updated_at AFTER the supersede write (which bumps it to NOW)
            # so ordering in the test is deterministic.
            conn.execute(
                "UPDATE user_memories SET updated_at='2026-01-01' WHERE user_id=? AND key=?",
                (user_id, "profile.user.editor"),
            )

        with get_connection() as conn:
            chain = db.get_memory_timeline(conn, user_id=user_id, key="profile.user.editor")

        values = [row["value"] for row in chain]
        assert values == ["VSCode", "Cursor"]
        # Older row is marked superseded; newer one is active
        assert chain[0]["superseded_by"] == "profile.user.editor_v2"
        assert chain[1]["superseded_by"] is None

    def test_timeline_walks_reverse_direction(self) -> None:
        """Starting the walk from the newer key still finds the older one."""
        from src.core import database as db
        from src.core.database import get_connection, get_write_connection

        user_id = "timeline_reverse"
        with get_write_connection() as conn:
            db.set_memory(conn, user_id=user_id, key="old", value="v_old", source="auto_extracted")
            db.set_memory(conn, user_id=user_id, key="new", value="v_new", source="auto_extracted")
            db.mark_memory_superseded(conn, user_id=user_id, key="old", superseded_by="new")

        with get_connection() as conn:
            chain = db.get_memory_timeline(conn, user_id=user_id, key="new")

        keys = {row["key"] for row in chain}
        assert keys == {"old", "new"}


class TestExtractionThreading:
    """Verify the extraction write path forwards scope IDs end-to-end."""

    @pytest.mark.asyncio
    async def test_auto_extract_facts_forwards_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auto_extract_facts calls host.set(...) with the provided scope IDs."""
        from typing import ClassVar

        from src.services.memory._service._extraction import _MemoryExtractionMixin

        class FakeHost(_MemoryExtractionMixin):
            calls: ClassVar[list[dict]] = []

            def search_important(self, user_id, min_importance=2, limit=5):
                return []

            def set(
                self,
                user_id,
                key,
                value,
                *,
                source="manual",
                importance=3,
                expires_at=None,
                category=None,
                session_id=None,
                meeting_ids=None,
                file_ids=None,
            ):
                FakeHost.calls.append(
                    {
                        "key": key,
                        "meeting_ids": meeting_ids,
                        "file_ids": file_ids,
                    }
                )

        # Stub the LLM and parser so we get a deterministic fact back.
        class FakeLLM:
            pass

        class FakeResp:
            content = "stub"

        from src.services.memory import _extractor
        from src.services.memory._extractor import ExtractedFact
        from src.services.memory._service import _extraction as ex_mod

        def fake_extract_facts(*, content, question, answer, max_facts):
            return [
                ExtractedFact(
                    key="fact1",
                    value="v1",
                    importance=3,
                    category="fact",
                    expires_at=None,
                )
            ]

        monkeypatch.setattr(ex_mod.settings, "MEMORY_AUTO_EXTRACT", True)
        # Privacy mode must not leave the local ``existing`` collection
        # uninitialized later in the extraction pipeline.
        monkeypatch.setattr(ex_mod.settings, "MEMORY_EXTRACTION_INCLUDE_EXISTING", False)
        monkeypatch.setattr(ex_mod.settings, "MEMORY_EXTRACTION_MODE", "balanced")
        monkeypatch.setattr(ex_mod.settings, "MEMORY_MAX_FACTS_PER_TURN", 3)
        monkeypatch.setattr(ex_mod, "extract_facts", fake_extract_facts)
        monkeypatch.setattr("src.services.llm.cached_retry_invoke", lambda llm, prompt: FakeResp())
        monkeypatch.setattr("src.services.llm.get_llm", lambda: FakeLLM())

        class StubPrompt:
            def format(self, **kwargs):
                return "prompt"

        monkeypatch.setattr("src.services.llm.get_fact_extraction_prompt", lambda: StubPrompt())

        # _is_semantic_duplicate would need embeddings — short-circuit to False
        monkeypatch.setattr(
            "src.services.memory._service._extraction._is_semantic_duplicate",
            lambda key, existing: False,
        )
        _ = _extractor  # silence unused import warning

        host = FakeHost()
        await host.auto_extract_facts(
            "user1",
            "question?",
            "answer text long enough to pass min chars",
            meeting_ids=[99],
            file_ids=[5],
        )

        assert FakeHost.calls, "host.set was not called"
        call = FakeHost.calls[0]
        assert call["meeting_ids"] == [99]
        assert call["file_ids"] == [5]
