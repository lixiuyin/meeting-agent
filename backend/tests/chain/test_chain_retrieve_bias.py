"""Tests for query-intent content-type bias in retrieval reranking."""

from src.services.chain._retrieve_filters import _apply_content_type_bias
from src.services.chain._retrieve_utils import _dedup_docs, _filter_low_information_chunks


def test_apply_content_type_bias_prefers_table_when_query_mentions_table():
    docs = [
        {"content": "plain text", "metadata": {"content_type": "text"}, "score": 0.5},
        {
            "content": "Revenue table by quarter with region breakdown and YoY deltas",
            "metadata": {"content_type": "table"},
            "score": 0.45,
        },
    ]
    # Returns a new sorted list; does not mutate the input.
    result = _apply_content_type_bias("show me the table summary", docs)
    assert result[0]["metadata"]["content_type"] == "table"
    assert result[0]["score"] > result[1]["score"]


def test_apply_content_type_bias_prefers_figure_when_query_mentions_figure():
    docs = [
        {"content": "plain text", "metadata": {"content_type": "text"}, "score": 0.5},
        {
            "content": "Figure 2 shows latency distribution over deployment windows",
            "metadata": {"content_type": "image_caption"},
            "score": 0.45,
        },
    ]
    result = _apply_content_type_bias("what does the figure depict", docs)
    assert result[0]["metadata"]["content_type"] == "image_caption"
    assert result[0]["score"] > result[1]["score"]


def test_apply_content_type_bias_prefers_image_asset_when_query_mentions_figure():
    docs = [
        {"content": "plain text", "metadata": {"content_type": "text"}, "score": 0.5},
        {
            "content": "Image OCR: architecture diagram with service dependencies",
            "metadata": {"content_type": "image_asset"},
            "score": 0.45,
        },
    ]
    result = _apply_content_type_bias("show me the key image", docs)
    assert result[0]["metadata"]["content_type"] == "image_asset"
    assert result[0]["score"] > result[1]["score"]


def test_apply_content_type_bias_avoids_overboosting_low_info_table_noise():
    docs = [
        {"content": "high signal explanation", "metadata": {"content_type": "text"}, "score": 0.5},
        {"content": "Page 12 of 44", "metadata": {"content_type": "table"}, "score": 0.45},
    ]
    result = _apply_content_type_bias("show me the table summary", docs)
    assert result[0]["metadata"]["content_type"] == "text"


def test_filter_low_information_chunks_drops_footer_noise_from_tail():
    docs = [
        {"content": "important evidence about roadmap risks", "metadata": {}, "score": 0.9},
        {"content": "detailed mitigation actions and owners", "metadata": {}, "score": 0.85},
        {"content": "Page 3 of 28", "metadata": {}, "score": 0.4},
        {"content": "All rights reserved", "metadata": {}, "score": 0.35},
    ]
    filtered = _filter_low_information_chunks(docs)
    assert len(filtered) == 2
    assert all("page " not in d["content"].lower() for d in filtered)


def test_filter_low_information_chunks_preserves_minimum_context_window():
    docs = [
        {"content": "Page 1 of 20", "metadata": {}, "score": 0.9},
        {"content": "Page 2 of 20", "metadata": {}, "score": 0.8},
    ]
    filtered = _filter_low_information_chunks(docs)
    assert filtered == docs


def test_dedup_docs_honors_score_direction():
    duplicate = "same chunk payload " * 20
    all_results = [
        [{"content": duplicate, "metadata": {"id": 1}, "score": 0.2}],
        [{"content": duplicate, "metadata": {"id": 2}, "score": 0.8}],
    ]
    lower = _dedup_docs(all_results, lower_is_better=True)
    higher = _dedup_docs(all_results, lower_is_better=False)
    assert lower[0]["metadata"]["id"] == 1
    assert higher[0]["metadata"]["id"] == 2
