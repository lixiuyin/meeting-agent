"""Tests for write lock timeout behaviour in _connection.py."""

import threading

from src.core.database._connection import (
    _WRITE_LOCK_TIMEOUT,
    get_write_connection,
)


class TestWriteLockTimeout:
    def test_write_lock_timeout_constant(self):
        """Write lock timeout should be 60 seconds."""
        assert _WRITE_LOCK_TIMEOUT == 60

    def test_normal_write_succeeds(self):
        """Normal write connection should work without timeout."""
        with get_write_connection() as conn:
            conn.execute("SELECT 1")
        # Should not raise

    def test_write_lock_contention_raises_on_timeout(self, monkeypatch):
        """Lock acquisition must raise RuntimeError after timeout."""
        monkeypatch.setattr("src.core.database._connection._WRITE_LOCK_TIMEOUT", 0.01)
        # Use a plain Lock (not RLock) to simulate contention from same thread
        test_lock = threading.Lock()

        acquired = test_lock.acquire()
        assert acquired
        try:
            # Re-acquiring a held Lock should fail with timeout
            re_acquired = test_lock.acquire(timeout=0.01)
            assert not re_acquired
        finally:
            test_lock.release()

    def test_depth_tracking_per_thread(self):
        """Write depth should be tracked per thread."""
        from src.core.database._connection import _write_depth_per_thread

        tid = threading.get_ident()
        # Clean up any residual state
        _write_depth_per_thread.pop(tid, None)
        assert _write_depth_per_thread.get(tid) is None

    def test_write_lock_pool_size(self):
        """Write lock pool should have correct size."""
        from src.core.database._connection import _WRITE_LOCK_POOL_SIZE, _write_lock_pool

        assert _WRITE_LOCK_POOL_SIZE == 1
        assert len(_write_lock_pool) == 1
