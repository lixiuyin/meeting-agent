"""T3: Verify per-file reindex lock prevents concurrent delete+upsert (R-H3)."""

import threading
import time

import pytest

from src.services.rag._indexer import _acquire_file_reindex_lock


@pytest.mark.unit
class TestIndexerConcurrentReindex:
    def test_file_reindex_lock_is_exclusive(self):
        """Same (meeting_id, file_id) should serialize via the lock."""
        lock1 = _acquire_file_reindex_lock(1, 100)
        lock2 = _acquire_file_reindex_lock(1, 100)
        assert lock1 is lock2, "Same key should return the same lock"

    def test_different_files_use_different_locks(self):
        """Different file_ids should get independent locks."""
        lock_a = _acquire_file_reindex_lock(1, 100)
        lock_b = _acquire_file_reindex_lock(1, 200)
        assert lock_a is not lock_b, "Different files need different locks"

    def test_lock_actually_serializes(self):
        """Verify the lock blocks concurrent access."""
        results = []
        lock = _acquire_file_reindex_lock(99, 999)

        def _worker(worker_id: int):
            with lock:
                results.append(f"enter_{worker_id}")
                time.sleep(0.1)
                results.append(f"exit_{worker_id}")

        t1 = threading.Thread(target=_worker, args=(1,))
        t2 = threading.Thread(target=_worker, args=(2,))
        t1.start()
        time.sleep(0.01)  # ensure t1 acquires first
        t2.start()
        t1.join()
        t2.join()

        # Should be serialized: enter_1 -> exit_1 -> enter_2 -> exit_2
        assert results == ["enter_1", "exit_1", "enter_2", "exit_2"], (
            f"Expected serialized order, got {results}"
        )
