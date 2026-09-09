from src.services.rag._query_analysis import TemporalHint, analyze_query


def test_authoritative_empty_speaker_list_disables_name_guessing() -> None:
    result = analyze_query("List the action items from the meeting.", known_speakers=[])

    assert result.speaker_names == []
    assert result.topic_query == "List the action items from the meeting."


def test_fallback_name_guessing_requires_explicit_speaker_intent() -> None:
    assert analyze_query("List the action items from the meeting.").speaker_names == []
    assert analyze_query("What did Alex say about the budget?").speaker_names == ["Alex"]


def test_known_speaker_match_remains_authoritative_without_intent_phrase() -> None:
    result = analyze_query("Alex on the budget", known_speakers=["Alex", "Bob"])

    assert result.speaker_names == ["Alex"]
    assert result.topic_query == "on the budget"


def test_temporal_words_do_not_match_inside_unrelated_words() -> None:
    result = analyze_query("Who attended the Q1 planning meeting?", known_speakers=[])

    assert result.temporal_hint is None


def test_standalone_temporal_word_still_matches() -> None:
    result = analyze_query("What was decided at the end?", known_speakers=[])

    assert result.temporal_hint == TemporalHint(ratio_min=0.62, ratio_max=1.0)
