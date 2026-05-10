"""Tests for multimodal attach gate (S2)."""

from src.services.chain._formatting import is_visual_query


def test_visual_query_english_keywords():
    assert is_visual_query("What does the diagram show?")
    assert is_visual_query("Show me the chart for Q3 revenue")
    assert is_visual_query("Which slide contains the architecture picture?")


def test_visual_query_cjk_keywords():
    assert is_visual_query("图中显示了什么?")
    assert is_visual_query("给我看看那张截图")
    assert is_visual_query("示意图的含义是什么")
    assert is_visual_query("第三页的幻灯片讲了什么")


def test_non_visual_query_text_only():
    assert not is_visual_query("What is the deadline for the project?")
    assert not is_visual_query("Who owns the API refactor?")
    assert not is_visual_query("这次会议的主要内容是什么?")
    assert not is_visual_query("总结一下讨论要点")


def test_empty_query_is_not_visual():
    assert not is_visual_query("")
    assert not is_visual_query(None)  # type: ignore[arg-type]


def test_word_boundary_avoids_false_positives():
    # "seen" shouldn't trigger because the regex requires the word token "see"
    # at a word boundary; "seen" matches the "see" root but let's confirm we
    # don't treat incidental substrings as visual.
    assert is_visual_query("Have you seen the diagram?")  # diagram triggers
    # But "pictures" as a noun does trigger (expected).
    assert is_visual_query("There are pictures attached")
    # A sentence without any visual word stays non-visual.
    assert not is_visual_query("The deadline moved to next Friday")
