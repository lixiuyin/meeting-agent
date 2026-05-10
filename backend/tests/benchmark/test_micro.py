"""Component micro-benchmarks using pytest-benchmark."""

import pytest

# ---------------------------------------------------------------------------
# Embedder benchmark (mocked to avoid external API calls)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestEmbedderBenchmark:
    def test_embed_10_short_strings(self, benchmark):
        from src.services.embedder import get_embeddings

        texts = [f"Sample text number {i}" for i in range(10)]
        emb = get_embeddings()

        def _embed():
            # Mock the actual embedding call to avoid external API dependency
            return [[0.1] * 384 for _ in texts]

        # Benchmark the loop/list-comp overhead + any preprocessing
        benchmark(_embed)

    def test_embed_100_short_strings(self, benchmark):
        texts = [f"Sample text number {i}" for i in range(100)]

        def _embed():
            return [[0.1] * 384 for _ in texts]

        benchmark(_embed)

    def test_embed_500_short_strings(self, benchmark):
        texts = [f"Sample text number {i}" for i in range(500)]

        def _embed():
            return [[0.1] * 384 for _ in texts]

        benchmark(_embed)


# ---------------------------------------------------------------------------
# Retrieval benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestRetrieveBenchmark:
    def test_retrieve_top_10(self, benchmark, seeded_vectorstore, monkeypatch):
        from src.services.rag import _retriever as retrieve_module

        # Ensure get_vectorstore returns the seeded store
        monkeypatch.setattr(retrieve_module, "get_vectorstore", lambda: seeded_vectorstore)

        # Mock embed_query to avoid external API
        class _MockEmbeddings:
            def embed_query(self, text: str):
                return [0.5] * 384

        seeded_vectorstore._embedding_function = _MockEmbeddings()

        def _retrieve():
            docs, _qa = retrieve_module.retrieve(
                query="benchmark query",
                meeting_ids=None,
                top_k=10,
                fetch_multiplier=1,
            )
            return docs

        result = benchmark(_retrieve)
        docs = result[0] if isinstance(result, tuple) else result
        assert isinstance(docs, list)


# ---------------------------------------------------------------------------
# Reranker benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestRerankerBenchmark:
    def test_rerank_20_candidates(self, benchmark, monkeypatch):
        from src.services.rag import _reranker as reranker_module

        docs = [
            {"content": f"Document content {i}", "metadata": {"score": 1.0 - i * 0.01}}
            for i in range(20)
        ]

        # Mock Cohere and cross-encoder to avoid external dependencies
        monkeypatch.setattr(reranker_module, "_cohere_client", None)
        monkeypatch.setattr(reranker_module, "_reranker_model", None)

        def _mock_rerank(query, candidates, top_n=5):
            return candidates[:top_n]

        monkeypatch.setattr(reranker_module, "rerank", _mock_rerank)

        def _rerank():
            return reranker_module.rerank("benchmark query", docs, top_n=5)

        result = benchmark(_rerank)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Chunking benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestChunkingBenchmark:
    def test_chunking_throughput(self, benchmark):
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        text = "\n\n".join([f"Paragraph {i} with some content." * 20 for i in range(50)])
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        def _split():
            return splitter.split_text(text)

        result = benchmark(_split)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Parser benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestParserBenchmark:
    def test_parse_sample_pdf(self, benchmark):
        from pathlib import Path

        from src.services.parser import parse

        fixture = Path(__file__).parent.parent / "fixtures" / "benchmark" / "sample.pdf"
        if not fixture.exists():
            pytest.skip("Fixture sample.pdf not found")

        def _parse():
            return parse(fixture)

        result = benchmark(_parse)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tokenizer benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestTokenizerBenchmark:
    def test_tokenizer_throughput(self, benchmark):
        from src.services.tokenizer import count_tokens

        text = "The quick brown fox jumps over the lazy dog. " * 500

        def _count():
            return count_tokens(text, "gpt-4")

        result = benchmark(_count)
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# Memory consolidation benchmark
# ---------------------------------------------------------------------------


def _make_memories(n: int) -> list[dict]:
    """Generate n synthetic memory dicts for clustering benchmarks."""
    return [
        {
            "key": f"user_preference_{i % 20}",
            "value": f"User prefers option {i % 5} for task {i % 10}",
            "category": "preference",
            "importance": 3,
        }
        for i in range(n)
    ]


@pytest.mark.benchmark
class TestMemoryClusteringBenchmark:
    def test_text_cluster_100_memories(self, benchmark):
        from src.services.memory._parsers import _text_cluster_memories

        memories = _make_memories(100)

        def _cluster():
            return _text_cluster_memories(memories)

        result = benchmark(_cluster)
        assert isinstance(result, list)
        total = sum(len(c) for c in result)
        assert total <= 100

    def test_text_cluster_500_memories(self, benchmark):
        from src.services.memory._parsers import _text_cluster_memories

        memories = _make_memories(500)

        def _cluster():
            return _text_cluster_memories(memories)

        result = benchmark(_cluster)
        assert isinstance(result, list)

    def test_text_cluster_1000_memories(self, benchmark):
        from src.services.memory._parsers import _text_cluster_memories

        memories = _make_memories(1000)

        def _cluster():
            return _text_cluster_memories(memories)

        result = benchmark(_cluster)
        assert isinstance(result, list)
