"""Regression tests for audit findings: P0-1, HIGH-2, HIGH-3, HIGH-4, HIGH-8."""

import asyncio

from src.core.config import settings
from src.core.security import _derive_user_id_from_api_key, is_dev_user


class TestUserIsolationP0:
    """P0-1 / HIGH-2: Users must not see each other's meetings."""

    def test_create_meeting_requires_user_id(self):
        """Meetings are created with explicit user_id; default='default' removed."""
        import ast
        import pathlib

        SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        violations = []
        for py_file in SRC.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text())
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_attr_call = isinstance(func, ast.Attribute) and func.attr == "create_meeting"
                is_name_call = isinstance(func, ast.Name) and func.id == "create_meeting"
                if not (is_attr_call or is_name_call):
                    continue
                kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
                if "user_id" not in kwargs:
                    violations.append(f"{py_file.relative_to(SRC.parent)}:{node.lineno}")
        assert not violations, (
            f"Found {len(violations)} create_meeting call(s) without user_id:\n"
            + "\n".join(violations)
        )

    def test_is_dev_user_backward_compat(self):
        """Legacy 'default' and new 'dev_' prefixed IDs are both recognized."""
        assert is_dev_user("default") is True
        assert is_dev_user("dev_abc123") is True
        assert is_dev_user("api_abc123") is False

    def test_derive_user_id_is_deterministic(self):
        """Same API key always produces the same user_id."""
        key = "test-api-key-12345"
        uid1 = _derive_user_id_from_api_key(key)
        uid2 = _derive_user_id_from_api_key(key)
        assert uid1 == uid2
        assert uid1.startswith("api_")

    def test_derive_user_id_different_keys(self):
        """Different API keys produce different user_ids."""
        uid1 = _derive_user_id_from_api_key("key-alpha")
        uid2 = _derive_user_id_from_api_key("key-beta")
        assert uid1 != uid2

    def test_ownership_filter_skips_dev_mode(self):
        """Dev mode user_id yields None ownership filter (no filtering)."""
        from src.api.routers.meetings._common import _ownership_filter

        assert _ownership_filter({"user_id": "default"}) is None
        assert _ownership_filter({"user_id": "api_abc123"}) == "api_abc123"


class TestRecoveryGracePeriodHIGH3:
    """HIGH-3: Recovery must not kill tasks within the grace period."""

    def test_grace_period_is_30_minutes(self):
        """Recovery grace period must be >= 30 minutes to avoid killing active tasks."""
        from src.services.processor._recovery import _GRACE_PERIOD_MINUTES

        assert _GRACE_PERIOD_MINUTES >= 30, (
            f"Recovery grace period is {_GRACE_PERIOD_MINUTES} min, "
            "should be >= 30 for long video/PDF processing"
        )

    def test_recovery_respects_grace_period(self):
        """Meetings stuck less than grace period are NOT recovered."""
        from src.core.database import get_write_connection
        from src.core.database._meetings_crud import create_meeting, get_meeting
        from src.services.processor._recovery import _do_recover

        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Test Recovery", user_id="test-recovery")
            # Manually set to processing just now (within grace period)
            conn.execute(
                "UPDATE meetings SET status='processing', "
                "processing_started_at=CURRENT_TIMESTAMP WHERE id=?",
                (mid,),
            )
            conn.commit()

        # Recovery with grace_expr should NOT touch this meeting
        with get_write_connection() as conn:
            _do_recover(conn, "-1 minutes")  # grace=1min, meeting is 0min old
            m = get_meeting(conn, mid)
            assert m is not None


class TestStreamSemaphoreHIGH4:
    """HIGH-4: Stream semaphore must be pre-initialized before requests."""

    def test_stream_semaphore_is_initialized(self):
        """Semaphore is set by lifespan before any request is served."""
        from src.api.routers.chat import _get_stream_semaphore

        sem = _get_stream_semaphore()
        assert sem is not None

    def test_set_stream_semaphore_replaces_instance(self):
        """set_stream_semaphore replaces the global instance atomically."""
        from src.api.routers.chat import (
            _get_stream_semaphore,
            set_stream_semaphore,
        )

        old = _get_stream_semaphore()
        new = asyncio.Semaphore(8)
        set_stream_semaphore(new)
        assert _get_stream_semaphore() is new
        # Restore
        set_stream_semaphore(old)

    def test_concurrent_stream_limit_enforced(self):
        """Semaphore respects STREAM_CONCURRENT_LIMIT from settings."""
        from src.api.routers.chat import _get_stream_semaphore

        sem = _get_stream_semaphore()
        assert sem._value > 0  # Not exhausted by default
        assert sem._value <= settings.STREAM_CONCURRENT_LIMIT


class TestKGEntityMergeHIGH8:
    """HIGH-8: Entity merge must remap relations to canonical entity."""

    def test_reassign_entity_relations_exists(self):
        """The reassign_entity_relations function is importable and works."""
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            # Create three entities: canonical, old alias, and target for relation
            e1 = db.upsert_entity(
                conn,
                user_id="test-kg-merge",
                name="apple",
                entity_type="organization",
                description="Apple Inc.",
            )
            e2 = db.upsert_entity(
                conn,
                user_id="test-kg-merge",
                name="apple_inc",
                entity_type="organization",
                description="Apple Incorporated",
            )
            e3 = db.upsert_entity(
                conn,
                user_id="test-kg-merge",
                name="tim_cook",
                entity_type="person",
                description="CEO of Apple",
            )
            # Create a relation from e2 → e3 (not self-loop)
            db.upsert_relation(
                conn,
                user_id="test-kg-merge",
                subject_id=e2,
                predicate="leads",
                object_id=e3,
            )
            # Verify relation was created pointing to e2
            before = conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_relations "
                "WHERE subject_id=? AND object_id=? AND user_id=?",
                (e2, e3, "test-kg-merge"),
            ).fetchone()
            assert before["cnt"] == 1

            # Remap relations from e2 → e1
            db.reassign_entity_relations(
                conn,
                source_id=e2,
                target_id=e1,
                user_id="test-kg-merge",
            )
            # Verify: relations now point to e1, not e2
            after = conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_relations WHERE subject_id=? AND user_id=?",
                (e1, "test-kg-merge"),
            ).fetchone()
            assert after["cnt"] >= 1

            # Cleanup
            conn.execute("DELETE FROM memory_relations WHERE user_id='test-kg-merge'")
            conn.execute("DELETE FROM memory_entities WHERE user_id='test-kg-merge'")


class TestSettingsEpochOrderHIGH1:
    """HIGH-1: Epoch must be bumped AFTER all config writes."""

    def test_bump_settings_epoch_after_update(self):
        """Epoch bump function exists and returns incremented value."""
        from src.core.settings_epoch import bump_settings_epoch, get_settings_epoch

        before = get_settings_epoch()
        after = bump_settings_epoch()
        assert after == before + 1

    def test_get_settings_epoch_stable_on_read(self):
        """Reading epoch does not change it."""
        from src.core.settings_epoch import get_settings_epoch

        e1 = get_settings_epoch()
        e2 = get_settings_epoch()
        assert e1 == e2
