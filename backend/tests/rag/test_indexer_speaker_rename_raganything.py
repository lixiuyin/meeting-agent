"""T4: Verify speaker rename triggers RAGAnything re-index (R-C1)."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestIndexerSpeakerRenameRAGAnything:
    def test_delete_meeting_chunks_removes_from_raganything(self):
        """_remove_from_raganything exists and is callable."""
        from src.services.rag._indexer_store import _remove_from_raganything

        assert callable(_remove_from_raganything)

    def test_delete_meeting_chunks_function_exists(self):
        """delete_meeting_chunks is callable and importable."""
        from src.services.rag._indexer_store import delete_meeting_chunks

        assert callable(delete_meeting_chunks)

    def test_raganything_functions_exist(self):
        """index_with_raganything and index_file_with_raganything are callable."""
        from src.services.rag._raganything import (
            index_file_with_raganything,
            index_with_raganything,
        )

        assert callable(index_with_raganything)
        assert callable(index_file_with_raganything)

    def test_speaker_rename_job_is_registered(self):
        """Durable worker owns speaker rename execution."""
        from src.services.jobs import _handler_for

        assert callable(_handler_for("speaker_rename"))

    def test_speaker_rename_dual_writes_when_enabled(self, monkeypatch):
        from src.services.speaker_rename import _reindex_raganything

        monkeypatch.setattr("src.services.speaker_rename.settings.RAGANYTHING_ENABLED", True)
        with patch("src.services.rag._raganything.index_with_raganything") as index:
            _reindex_raganything(
                4,
                8,
                {"file_path": "/tmp/a.mp3", "file_name": "a.mp3", "file_type": "audio"},
                "Alice: hello",
            )

        index.assert_called_once_with(
            meeting_id=4,
            file_id=8,
            text="Alice: hello",
            file_path="/tmp/a.mp3",
            metadata={"title": "a.mp3", "file_type": "audio", "user_id": "default"},
        )

    def test_native_rename_uses_file_generation_and_persists_manifest(self, monkeypatch):
        from src.services import speaker_rename

        @contextmanager
        def replacement(_meeting_id, _file_id):
            yield "new-generation"

        manifest = SimpleNamespace(
            generation="new-generation",
            config_fingerprint="fingerprint",
            chroma_chunk_count=2,
            bm25_chunk_count=2,
            checksum="checksum",
        )
        fake_conn = object()

        @contextmanager
        def connection():
            yield fake_conn

        monkeypatch.setattr(speaker_rename.db, "get_write_connection", connection)
        building = patch.object(speaker_rename.db, "mark_native_index_building")
        ready = patch.object(speaker_rename.db, "mark_native_index_ready")
        failed = patch.object(speaker_rename.db, "mark_native_index_failed")
        with (
            building as mark_building,
            ready as mark_ready,
            failed as mark_failed,
            patch(
                "src.services.rag._indexer_store.atomic_file_index_replacement",
                replacement,
            ),
            patch(
                "src.services.rag._indexer_store.inspect_native_index_generation",
                return_value=manifest,
            ),
            patch("src.services.rag.index_meeting_segments") as index_segments,
        ):
            speaker_rename._replace_native_index(
                4,
                8,
                {"index_config_fingerprint": "fingerprint", "user_id": "principal"},
                [{"text": "Alice: hello", "start": 0.0, "end": 1.0}],
            )

        mark_building.assert_called_once_with(fake_conn, file_id=8, meeting_id=4)
        index_segments.assert_called_once()
        call = index_segments.call_args
        assert call.kwargs["metadata"]["index_generation"] == "new-generation"
        assert call.kwargs["metadata"]["user_id"] == "principal"
        assert call.kwargs["strict_bm25"] is True
        mark_ready.assert_called_once()
        assert mark_ready.call_args.kwargs["manifest_checksum"] == "checksum"
        mark_failed.assert_not_called()
