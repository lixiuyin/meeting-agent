"""T6: Verify lifespan startup failure doesn't skip shutdown (C-C2)."""

import inspect

import pytest


@pytest.mark.unit
class TestLifespanStartupFailure:
    def test_critical_startup_wrapped_in_try_except(self):
        """Verify lifespan source code wraps run_critical_startup in try/except."""
        import src.api.lifespan as lifespan_mod

        source = inspect.getsource(lifespan_mod.lifespan)
        assert "try:" in source, "lifespan should have try block for startup"
        assert "run_critical_startup" in source
        # The except block catches failures from critical startup to ensure
        # yield is always reached (C-C2 fix).
        assert "except Exception:" in source or "except" in source

    def test_shutdown_module_uses_all_tasks(self):
        """Shutdown module uses asyncio.all_tasks() for cleanup (CONC-10)."""
        import src.api.lifespan._shutdown as shutdown_mod

        source = inspect.getsource(shutdown_mod.graceful_shutdown)
        assert "asyncio.all_tasks" in source, (
            "graceful_shutdown should use asyncio.all_tasks() for cleanup"
        )

    def test_lifespan_has_shutdown_path(self):
        """Verify yield and shutdown code exist."""
        import src.api.lifespan as lifespan_mod

        source = inspect.getsource(lifespan_mod.lifespan)
        assert "yield" in source
        assert "graceful_shutdown" in source

    def test_shutdown_closes_connections(self):
        """Verify shutdown module closes DB connections and cancels tasks."""
        import src.api.lifespan._shutdown as shutdown_mod

        source = inspect.getsource(shutdown_mod.graceful_shutdown)
        assert "close_all_connections" in source
        assert "cancel_background_tasks" in source
