"""Property-based tests for memory parser helpers."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.services.memory._parsers import _compute_expiry, _is_fact_supported, _is_semantic_duplicate


@pytest.mark.unit
@pytest.mark.property
@given(ttl_days=st.integers(min_value=-1, max_value=3650))
def test_compute_expiry_contract(ttl_days: int) -> None:
    expiry = _compute_expiry(ttl_days)
    if ttl_days <= 0:
        assert expiry is None
    else:
        assert expiry is not None


@pytest.mark.unit
@pytest.mark.property
@given(
    word=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=2, max_size=20
    )
)
def test_identical_key_is_detected_as_duplicate(word: str) -> None:
    key = f"{word} project"
    assert _is_semantic_duplicate(key, [key]) is True


@pytest.mark.unit
@pytest.mark.property
@given(
    entity=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=3, max_size=20
    ),
    value=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=3, max_size=20
    ),
)
def test_fact_supported_when_source_contains_key_and_value(entity: str, value: str) -> None:
    key = f"{entity} roadmap"
    answer = f"{entity} roadmap includes {value} milestone"
    assert _is_fact_supported(key, value, "what changed", answer, min_overlap=1) is True
