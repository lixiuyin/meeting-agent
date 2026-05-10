"""T2: Verify NULL last_accessed memories still decay (M-H1)."""

import pytest

from src.services.memory._decay import _compute_decay_score


@pytest.mark.unit
class TestMemoryDecayNullLastAccessed:
    def test_null_last_accessed_falls_back_to_created_at(self):
        """Memory with NULL last_accessed should use created_at for decay."""
        from datetime import UTC, datetime, timedelta

        created_at = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        score = _compute_decay_score(5, last_accessed=None, created_at=created_at)
        assert score < 5.0, f"Expected decayed score < 5.0, got {score}"
        assert score > 0.0, f"Expected score > 0.0, got {score}"

    def test_null_both_decays_as_one_year(self):
        """Both NULL → treated as 365 days old (HIGH-10: zombie memory fix)."""
        score = _compute_decay_score(5, last_accessed=None, created_at=None)
        assert score < 5.0, f"Should decay, got {score}"
        assert score > 0.0, f"Should be positive, got {score}"

    def test_recent_memory_barely_decays(self):
        """A memory accessed recently should decay very little."""
        from datetime import UTC, datetime, timedelta

        recent = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        score = _compute_decay_score(5, last_accessed=recent)
        assert score > 4.9, f"Recent memory should barely decay, got {score}"

    def test_old_memory_decays_significantly(self):
        """A memory from 100 days ago should show meaningful decay."""
        from datetime import UTC, datetime, timedelta

        old = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        score = _compute_decay_score(5, last_accessed=old)
        assert score < 3.0, f"Old memory should decay significantly, got {score}"
