"""Property-based tests for Reciprocal Rank Fusion."""

from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from src.services.rag._rrf import _rrf_dedup_key, _rrf_merge, _rrf_merge_multi

_doc_strategy = st.builds(
    lambda content, chunk_id, meeting_id, chunk_index: {
        "content": content,
        "metadata": {
            k: v
            for k, v in [
                ("chunk_id", chunk_id),
                ("meeting_id", meeting_id),
                ("chunk_index", chunk_index),
            ]
            if v is not None
        },
    },
    content=st.text(min_size=1, max_size=200),
    chunk_id=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    meeting_id=st.one_of(st.none(), st.integers(min_value=0, max_value=9999)),
    chunk_index=st.one_of(st.none(), st.integers(min_value=0, max_value=999)),
)


@given(doc=_doc_strategy)
@settings(max_examples=100)
def test_rrf_dedup_key_idempotent(doc: dict):
    assert _rrf_dedup_key(doc) == _rrf_dedup_key(doc)


@given(doc=_doc_strategy)
@settings(max_examples=100)
def test_rrf_dedup_key_never_empty(doc: dict):
    assert len(_rrf_dedup_key(doc)) > 0


@given(
    vector_results=st.lists(_doc_strategy, max_size=20),
    bm25_results=st.lists(_doc_strategy, max_size=20),
    top_k=st.integers(min_value=1, max_value=50),
)
@settings(max_examples=200)
def test_rrf_merge_output_invariants(
    vector_results: list[dict],
    bm25_results: list[dict],
    top_k: int,
):
    with patch("src.services.rag._rrf.settings") as mock_settings:
        mock_settings.HYBRID_ALPHA = 0.7
        mock_settings.RRF_K_PARAM = 60
        result = _rrf_merge(vector_results, bm25_results, top_k)

    assert len(result) <= top_k
    if result:
        for item in result:
            assert 0.0 <= item["score"] <= 1.0, f"Score out of range: {item['score']}"
        scores = [item["score"] for item in result]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], "Scores not in descending order"


@given(
    vector_results=st.lists(_doc_strategy, max_size=5),
    bm25_results=st.lists(_doc_strategy, max_size=5),
    top_k=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100)
def test_rrf_merge_deduplicates(
    vector_results: list[dict],
    bm25_results: list[dict],
    top_k: int,
):
    with patch("src.services.rag._rrf.settings") as mock_settings:
        mock_settings.HYBRID_ALPHA = 0.5
        mock_settings.RRF_K_PARAM = 60
        result = _rrf_merge(vector_results, bm25_results, top_k)

    keys = [_rrf_dedup_key(item) for item in result]
    assert len(keys) == len(set(keys)), "Duplicate keys in merged output"


@given(
    results_a=st.lists(_doc_strategy, min_size=1, max_size=10),
    results_b=st.lists(_doc_strategy, min_size=1, max_size=10),
    top_k=st.integers(min_value=1, max_value=20),
    weight_a=st.floats(min_value=0.1, max_value=1.0),
    weight_b=st.floats(min_value=0.1, max_value=1.0),
)
@settings(max_examples=100)
def test_rrf_merge_multi_output_invariants(
    results_a: list[dict],
    results_b: list[dict],
    top_k: int,
    weight_a: float,
    weight_b: float,
):
    result = _rrf_merge_multi(
        [(results_a, weight_a), (results_b, weight_b)],
        top_k,
    )
    assert len(result) <= top_k
    if result:
        for item in result:
            assert 0.0 <= item["score"] <= 1.0
        scores = [item["score"] for item in result]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]


def test_rrf_merge_empty_inputs():
    with patch("src.services.rag._rrf.settings") as mock_settings:
        mock_settings.HYBRID_ALPHA = 0.5
        mock_settings.RRF_K_PARAM = 60
        assert _rrf_merge([], [], 10) == []
