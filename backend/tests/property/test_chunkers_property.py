"""Property-based tests for chunking helpers."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.services.rag._chunkers import _split_by_structure


def _text_strategy():
    line = st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",),
            blacklist_characters=("\n", "\r"),
        ),
        min_size=1,
        max_size=60,
    )
    return st.lists(line, min_size=1, max_size=30).map("\n".join)


@pytest.mark.unit
@pytest.mark.property
@given(text=_text_strategy(), max_chunk_size=st.integers(min_value=20, max_value=300))
def test_split_by_structure_preserves_text(text: str, max_chunk_size: int) -> None:
    chunks = _split_by_structure(text, max_chunk_size=max_chunk_size)
    assert chunks
    assert "\n".join(chunks) == text


@pytest.mark.unit
@pytest.mark.property
@given(
    text=_text_strategy(),
    small=st.integers(min_value=20, max_value=120),
    large=st.integers(min_value=121, max_value=300),
)
def test_larger_chunk_size_does_not_increase_chunk_count(text: str, small: int, large: int) -> None:
    small_chunks = _split_by_structure(text, max_chunk_size=small)
    large_chunks = _split_by_structure(text, max_chunk_size=large)
    assert len(large_chunks) <= len(small_chunks)
