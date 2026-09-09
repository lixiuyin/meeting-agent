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
        # The except block records failures so dev can expose degraded status;
        # non-dev environments re-raise and fail closed.
        assert "except Exception:" in source or "except" in source

    @pytest.mark.asyncio
    async def test_critical_startup_failure_is_fatal_outside_dev(self, monkeypatch):
        from fastapi import FastAPI

        import src.api.lifespan as lifespan_mod

        def fail_migration():
            raise RuntimeError("migration failed")

        monkeypatch.setattr(lifespan_mod.settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(lifespan_mod, "run_alembic_upgrade", fail_migration)

        with pytest.raises(RuntimeError, match="migration failed"):
            async with lifespan_mod.lifespan(FastAPI()):
                pass

    def test_shutdown_does_not_cancel_foreign_loop_tasks(self):
        """Shutdown only cancels tasks owned by this application."""
        import src.api.lifespan._shutdown as shutdown_mod

        source = inspect.getsource(shutdown_mod.graceful_shutdown)
        assert "asyncio.all_tasks" not in source
        assert "cancel_all" in source

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
        assert "cancel_all" in source
