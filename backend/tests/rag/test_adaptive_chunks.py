"""Tests for per-file adaptive chunk depth allocation."""

import pytest

from src.services.chain._retrieve_routing import _compute_adaptive_chunks


class TestComputeAdaptiveChunksUniform:
    """When no scores available, returns uniform int."""

    def test_no_scores_returns_int(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            True,
        )
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2, 3],
            file_scores=None,
            file_metadata=None,
            total_budget=24,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, int)
        assert result == 8  # 24 // 3

    def test_empty_scores_returns_int(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            True,
        )
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2],
            file_scores={},
            file_metadata=None,
            total_budget=16,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, int)

    def test_adaptive_disabled_returns_int(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            False,
        )
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2, 3],
            file_scores={1: 0.9, 2: 0.5, 3: 0.1},
            file_metadata=None,
            total_budget=24,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, int)


class TestComputeAdaptiveChunksAdaptive:
    """When scores available, returns dict with per-file allocation."""

    @pytest.fixture(autouse=True)
    def _enable_adaptive(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            True,
        )
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_SIZE_FACTOR_ENABLED",
            True,
        )

    def test_returns_dict_with_scores(self):
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2, 3],
            file_scores={1: 0.9, 2: 0.5, 3: 0.1},
            file_metadata=None,
            total_budget=24,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == {1, 2, 3}
        # High-score file should get more chunks
        assert result[1] >= result[3]

    def test_respects_min_per_file(self):
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2],
            file_scores={1: 0.01, 2: 0.99},
            file_metadata=None,
            total_budget=20,
            min_per_file=3,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert all(v >= 3 for v in result.values())

    def test_respects_max_per_file(self):
        result = _compute_adaptive_chunks(
            scope_file_ids=[1],
            file_scores={1: 1.0},
            file_metadata=None,
            total_budget=100,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert result[1] <= 16

    def test_size_factor_increases_long_files(self):
        metadata = {
            1: {"file_type": "pdf", "page_count": 50, "duration_seconds": 0.0},
            2: {"file_type": "pdf", "page_count": 2, "duration_seconds": 0.0},
        }
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2],
            file_scores={1: 0.5, 2: 0.5},
            file_metadata=metadata,
            total_budget=20,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        # Same score, but file 1 has more pages => more chunks
        assert result[1] > result[2]

    def test_duration_factor_increases_long_videos(self):
        metadata = {
            1: {"file_type": "video", "page_count": 0, "duration_seconds": 1800.0},
            2: {"file_type": "video", "page_count": 0, "duration_seconds": 120.0},
        }
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2],
            file_scores={1: 0.5, 2: 0.5},
            file_metadata=metadata,
            total_budget=20,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert result[1] > result[2]

    def test_all_files_covered(self):
        fids = list(range(10))
        scores = {f: 0.1 * (f + 1) for f in fids}
        result = _compute_adaptive_chunks(
            scope_file_ids=fids,
            file_scores=scores,
            file_metadata=None,
            total_budget=60,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == set(fids)
        assert all(v >= 2 for v in result.values())
        assert all(v <= 16 for v in result.values())

    def test_zero_scores_handled(self):
        result = _compute_adaptive_chunks(
            scope_file_ids=[1, 2],
            file_scores={1: 0.0, 2: 0.0},
            file_metadata=None,
            total_budget=16,
            min_per_file=2,
            max_per_file=16,
        )
        assert isinstance(result, dict)
        assert all(v >= 2 for v in result.values())


class TestFairRetrieverAdaptive:
    """Verify fair_retrieve_per_file accepts dict chunks_per_file."""

    @pytest.mark.asyncio
    async def test_dict_chunks_per_file(self, monkeypatch):
        from src.services.rag._fair_retriever import fair_retrieve_per_file

        monkeypatch.setattr(
            "src.services.rag._fair_retriever.settings.RAG_HIERARCHICAL_ENABLED",
            False,
        )
        monkeypatch.setattr(
            "src.services.rag._fair_retriever.settings.RAG_MIN_CHUNKS_PER_FILE",
            2,
        )
        monkeypatch.setattr(
            "src.services.rag._fair_retriever.settings.RAG_FAIR_CONCURRENCY",
            4,
        )

        call_log: list[int] = []

        def mock_retrieve(query, **kwargs):
            fid = kwargs["file_ids"][0]
            call_log.append(fid)
            return [
                {
                    "content": f"chunk for {fid}",
                    "metadata": {"file_id": fid, "meeting_id": 1, "chunk_index": 0},
                    "score": 0.5,
                }
            ], None

        monkeypatch.setattr(
            "src.services.rag._fair_retriever.retrieve",
            mock_retrieve,
        )

        allocation = {10: 3, 20: 5}
        result = await fair_retrieve_per_file(
            "test query",
            [10, 20],
            chunks_per_file=allocation,
        )
        assert len(result) == 2
        assert set(call_log) == {10, 20}


class TestComputeChunkBudget:
    """``compute_chunk_budget`` composes adaptive allocation + variant scaling."""

    def test_single_variant_passthrough(self, monkeypatch):
        from src.services.chain._retrieve_routing import compute_chunk_budget

        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            False,
        )
        result = compute_chunk_budget(
            scope_file_ids=[1, 2, 3],
            file_scores=None,
            file_metadata=None,
            target_total=24,
            n_variants=1,
            min_per_file=2,
            max_per_file=16,
        )
        # Uniform path -> 24 // 3 = 8, no variant scaling
        assert result == 8

    def test_multi_variant_scales_uniform(self, monkeypatch):
        from src.services.chain._retrieve_routing import compute_chunk_budget

        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            False,
        )
        result = compute_chunk_budget(
            scope_file_ids=[1, 2, 3],
            file_scores=None,
            file_metadata=None,
            target_total=24,
            n_variants=3,
            min_per_file=2,
            max_per_file=16,
        )
        # Uniform 8 // 3 = 2 (floor honoured)
        assert result == 2

    def test_multi_variant_scales_dict(self, monkeypatch):
        from src.services.chain._retrieve_routing import compute_chunk_budget

        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_ADAPTIVE_CHUNKS",
            True,
        )
        monkeypatch.setattr(
            "src.services.chain._retrieve_routing.settings.RAG_FAIR_SIZE_FACTOR_ENABLED",
            False,
        )
        result = compute_chunk_budget(
            scope_file_ids=[1, 2],
            file_scores={1: 1.0, 2: 0.0},
            file_metadata=None,
            target_total=20,
            n_variants=2,
            min_per_file=2,
            max_per_file=16,
        )
        # Adaptive path -> dict result, then scale by 2 with floor 2
        assert isinstance(result, dict)
        for v in result.values():
            assert v >= 2
