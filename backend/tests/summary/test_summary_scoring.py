"""Tests for summary source similarity scoring."""

from unittest.mock import MagicMock, patch

from src.services.chain._generate_helpers import _score_summary


def _make_mock_embeddings(doc_vector):
    """Create a mock embeddings object that returns doc_vector for embed_documents.

    The batched scoring path used by ``_score_summary`` calls
    ``embed_documents`` with a single-element list, so we mirror the same
    return shape here.
    """
    mock = MagicMock()
    mock.embed_query.return_value = doc_vector
    mock.embed_documents.return_value = [doc_vector]
    return mock


def test_score_summary_returns_value_between_0_and_1():
    q_emb = [1.0, 0.0, 0.0]
    d_emb = [0.9, 0.1, 0.0]
    with patch("src.services.embedder.get_embeddings", return_value=_make_mock_embeddings(d_emb)):
        score = _score_summary(q_emb, "some summary text")
    assert 0.0 <= score <= 1.0
    assert score > 0.5


def test_score_summary_orthogonal_vectors_low():
    q_emb = [1.0, 0.0, 0.0]
    d_emb = [0.0, 1.0, 0.0]
    with patch("src.services.embedder.get_embeddings", return_value=_make_mock_embeddings(d_emb)):
        score = _score_summary(q_emb, "unrelated text")
    assert score < 0.5


def test_score_summary_identical_vectors_high():
    q_emb = [1.0, 2.0, 3.0]
    with patch("src.services.embedder.get_embeddings", return_value=_make_mock_embeddings(q_emb)):
        score = _score_summary(q_emb, "identical text")
    assert score == 1.0


def test_score_summary_fallback_on_error():
    with patch("src.services.embedder.get_embeddings", side_effect=RuntimeError("no emb")):
        score = _score_summary([1.0, 0.0], "text")
    assert score == 0.5


def test_score_summary_rounds_to_4_decimals():
    q_emb = [1.0, 0.0, 0.0]
    d_emb = [0.707, 0.707, 0.0]
    with patch("src.services.embedder.get_embeddings", return_value=_make_mock_embeddings(d_emb)):
        score = _score_summary(q_emb, "text")
    assert score == round(score, 4)


def test_score_summary_truncates_long_summary():
    q_emb = [1.0, 0.0, 0.0]
    d_emb = [1.0, 0.0, 0.0]
    mock = _make_mock_embeddings(d_emb)
    long_summary = "x" * 5000
    with patch("src.services.embedder.get_embeddings", return_value=mock):
        _score_summary(q_emb, long_summary)
    # Should truncate to first 2000 chars before embedding (batched path
    # passes the truncated text inside a single-element list to embed_documents).
    mock.embed_documents.assert_called_once_with([long_summary[:2000]])
