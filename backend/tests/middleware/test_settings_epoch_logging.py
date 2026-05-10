"""Tests for settings_epoch logging (replaces contextlib.suppress)."""

import logging


class TestSettingsEpochLogging:
    def test_bump_epoch_clears_cache_on_success(self):
        """bump_settings_epoch should clear caches and increment epoch."""
        from src.core.settings_epoch import bump_settings_epoch, get_settings_epoch

        cleared = []

        def clear_fn():
            cleared.append(True)

        from src.core.settings_epoch import register_epoch_cache

        register_epoch_cache(clear_fn)
        old_epoch = get_settings_epoch()
        new_epoch = bump_settings_epoch()
        assert new_epoch > old_epoch
        assert len(cleared) >= 1

    def test_bump_epoch_logs_on_cache_failure(self, caplog):
        """Cache clear failure should log a warning, not silently swallow."""
        from src.core import settings_epoch as mod

        def bad_clear():
            raise RuntimeError("boom")

        mod.register_epoch_cache(bad_clear)
        with caplog.at_level(logging.WARNING, logger="src.core.settings_epoch"):
            mod.bump_settings_epoch()
        assert any("Failed to clear epoch cache" in r.message for r in caplog.records)

    def test_epoch_increments_monotonically(self):
        """Epoch should increment monotonically."""
        from src.core.settings_epoch import bump_settings_epoch, get_settings_epoch

        prev = get_settings_epoch()
        for _ in range(3):
            curr = bump_settings_epoch()
            assert curr > prev
            prev = curr
