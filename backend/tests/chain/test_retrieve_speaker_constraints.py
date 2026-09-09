from unittest.mock import patch

from src.services.chain._retrieve_filters import _apply_speaker_filter
from src.services.rag._query_analysis import analyze_query


def test_file_level_presence_does_not_attribute_another_speakers_chunk() -> None:
    analysis = analyze_query("What did Alice decide?", ["Alice", "Bob"])
    docs = [
        {
            "content": "Bob: I approved the release.",
            "metadata": {"file_id": 11, "speaker": "Bob", "speakers_in_chunk": "Bob"},
            "score": 0.9,
        }
    ]

    with (
        patch("src.services.chain._retrieve_filters.get_connection"),
        patch(
            "src.services.chain._retrieve_filters.get_file_ids_for_speakers",
            return_value={11},
        ),
    ):
        assert _apply_speaker_filter(docs, analysis, [1]) == []


def test_explicit_speaker_metadata_wins_over_a_mention_in_chunk_text() -> None:
    analysis = analyze_query("What did Alice decide?", ["Alice", "Bob"])
    docs = [
        {
            "content": "Bob: Alice asked me to approve the release.",
            "metadata": {"file_id": 11, "speaker": "Bob"},
            "score": 0.9,
        }
    ]

    with patch("src.services.chain._retrieve_filters.get_file_ids_for_speakers", return_value={11}):
        assert _apply_speaker_filter(docs, analysis, [1]) == []


def test_legacy_chunk_text_can_still_prove_the_speaker() -> None:
    analysis = analyze_query("What did Alice decide?", ["Alice", "Bob"])
    docs = [
        {
            "content": "Alice: I approved the release.",
            "metadata": {"file_id": 11},
            "score": 0.9,
        }
    ]

    with patch("src.services.chain._retrieve_filters.get_file_ids_for_speakers", return_value={11}):
        assert _apply_speaker_filter(docs, analysis, [1]) == docs
