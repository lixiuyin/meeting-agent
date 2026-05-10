"""Property-based tests for memory decay scoring."""

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from src.services.memory._decay import _compute_decay_score


def _ts_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


recent_timestamp = st.builds(
    _ts_str,
    st.builds(
        lambda offset: datetime.now(UTC) - timedelta(seconds=offset),
        st.floats(min_value=0, max_value=86400),
    ),
)

old_timestamp = st.builds(
    _ts_str,
    st.builds(
        lambda days_ago: datetime.now(UTC) - timedelta(days=days_ago),
        st.floats(min_value=1, max_value=365),
    ),
)


@given(
    importance=st.integers(min_value=1, max_value=5),
    last_accessed=st.one_of(st.none(), recent_timestamp, old_timestamp),
    decay_rate=st.floats(min_value=0.001, max_value=0.1),
)
@settings(max_examples=200)
def test_decay_score_bounded(importance: int, last_accessed: str | None, decay_rate: float):
    score = _compute_decay_score(importance, last_accessed, decay_rate)
    assert 0.0 <= score <= float(importance)


@given(
    importance=st.integers(min_value=1, max_value=5),
    decay_rate=st.floats(min_value=0.001, max_value=0.1),
    old_days=st.floats(min_value=10, max_value=365),
)
@settings(max_examples=200)
def test_decay_score_monotonically_decreasing(importance: int, decay_rate: float, old_days: float):
    recent = _ts_str(datetime.now(UTC) - timedelta(hours=1))
    old = _ts_str(datetime.now(UTC) - timedelta(days=old_days))
    score_recent = _compute_decay_score(importance, recent, decay_rate)
    score_old = _compute_decay_score(importance, old, decay_rate)
    assert score_recent >= score_old


@given(
    importance=st.integers(min_value=1, max_value=5),
    decay_rate=st.floats(min_value=0.001, max_value=0.1),
)
@settings(max_examples=50)
def test_decay_score_none_returns_decayed(importance: int, decay_rate: float):
    """When both last_accessed and created_at are None, the memory is treated as
    365 days old (HIGH-10) so it decays naturally instead of staying at full importance."""
    score = _compute_decay_score(importance, None, decay_rate)
    assert 0.0 <= score < float(importance)


@given(
    importance=st.integers(min_value=1, max_value=5),
    bad_ts=st.one_of(st.just("not-a-date"), st.just("2024-13-45 99:99:99")),
)
@settings(max_examples=20)
def test_decay_score_bad_timestamp_returns_importance(importance: int, bad_ts: str):
    assert _compute_decay_score(importance, bad_ts) == float(importance)
