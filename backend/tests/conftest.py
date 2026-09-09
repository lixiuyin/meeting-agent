"""Pytest configuration and shared fixtures"""

import logging
import os
import sqlite3
import tempfile
import warnings
from pathlib import Path

import pytest

# Suppress known third-party SWIG deprecations emitted by optional PDF/OCR deps on Python 3.12.
warnings.filterwarnings(
    "ignore",
    message=r".*builtin type SwigPyPacked has no __module__ attribute.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*builtin type SwigPyObject has no __module__ attribute.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*builtin type swigvarlink has no __module__ attribute.*",
    category=DeprecationWarning,
)

# Create a temporary directory for test data BEFORE importing app modules
_test_db_dir = tempfile.mkdtemp()
os.environ["MEETING_AGENT_DISABLE_DOTENV"] = "1"
os.environ["DATA_DIR"] = _test_db_dir
os.environ["DB_PATH"] = str(Path(_test_db_dir) / "meetings.db")
os.environ["UPLOAD_DIR"] = str(Path(_test_db_dir) / "uploads")
os.environ["VECTOR_DB_DIR"] = str(Path(_test_db_dir) / "vectordb")
os.environ["LOG_DIR"] = str(Path(_test_db_dir) / "logs")
os.environ["CUSTOM_SKILLS_DIR"] = str(Path(_test_db_dir) / "skills")
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://test.example.com/v1"
os.environ["EMBEDDING_API_KEY"] = "test-embed-key"
os.environ["API_KEY"] = ""
os.environ["DISABLE_RATE_LIMIT"] = "1"
os.environ["ENVIRONMENT"] = "dev"
os.environ["RERANKER_BINDING"] = ""
os.environ["RERANKER_MODEL"] = "cohere/rerank-4-pro"
os.environ["RERANKER_API_KEY"] = ""
os.environ["RERANKER_BASE_URL"] = ""

# Monkey-patch constants BEFORE importing config/settings
# This ensures tests use a temp database, not the real one
import src.core.constants as constants_module  # noqa: E402

constants_module.DATA_DIR = Path(_test_db_dir)
constants_module.UPLOAD_DIR = Path(_test_db_dir) / "uploads"
constants_module.VECTOR_DB_DIR = Path(_test_db_dir) / "vectordb"
constants_module.DB_PATH = Path(_test_db_dir) / "meetings.db"
constants_module.LOG_DIR = Path(_test_db_dir) / "logs"

# Create directories
constants_module.DATA_DIR.mkdir(parents=True, exist_ok=True)
constants_module.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
constants_module.VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)
constants_module.LOG_DIR.mkdir(parents=True, exist_ok=True)


class UnsafeTestDatabaseError(RuntimeError):
    """Stop before fixtures can migrate or clean an application database."""


# Config may already have been imported by a programmatic pytest launcher.
# Environment changes cannot repair a settings instance that captured real paths.
from src.core.config import settings as _test_settings  # noqa: E402

for _field in ("DB_PATH", "UPLOAD_DIR", "VECTOR_DB_DIR"):
    if (
        not Path(getattr(_test_settings, _field))
        .resolve()
        .is_relative_to(Path(_test_db_dir).resolve())
    ):
        raise UnsafeTestDatabaseError(
            "Refusing to run tests against non-owned application paths; "
            "start pytest in a fresh process before importing src modules"
        )


# Now we can import app modules (they'll use the patched constants)
from src.core.database import SCHEMA_SQL, init_db  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _initialize_app_db():
    """Ensure the application database is initialized before any test runs.

    ASGITransport does not trigger the ASGI lifespan, so init_db() must
    be called explicitly for API integration tests.
    """
    init_db()


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for test data"""
    return tmp_path


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database file path (used by db_conn)"""
    return tmp_path / "test.db"


@pytest.fixture
def db_conn(db_path):
    """Provide a raw SQLite connection with all tables created."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    yield conn
    conn.close()


@pytest.fixture
def auth_headers():
    """Headers with valid API key for authenticated endpoints"""
    return {"X-API-Key": "test-api-key"}


@pytest.fixture(autouse=True, scope="function")
def clean_test_database(tmp_path_factory):
    """Clean up test data before each test to ensure isolation."""
    from src.core.database import get_write_connection

    yield

    # Clean up after test
    try:
        with get_write_connection() as conn:
            database_path = next(
                row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"
            )
            owned_roots = (Path(_test_db_dir).resolve(), tmp_path_factory.getbasetemp().resolve())
            if not database_path or not any(
                Path(database_path).resolve().is_relative_to(root) for root in owned_roots
            ):
                raise UnsafeTestDatabaseError("Refusing to clean a non-owned test database")
            # Clear all data tables but keep structure
            conn.execute("DELETE FROM chat_messages")
            conn.execute("DELETE FROM session_summaries")
            conn.execute("DELETE FROM chat_sessions")
            conn.execute("DELETE FROM memory_audit_log")
            conn.execute("DELETE FROM memory_relations")
            conn.execute("DELETE FROM entity_scopes")
            conn.execute("DELETE FROM memory_scopes")
            conn.execute("DELETE FROM memory_entities")
            conn.execute("DELETE FROM user_memories")
            conn.execute("DELETE FROM project_files")
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM meeting_summaries")
            conn.execute("DELETE FROM durable_jobs")
            conn.execute("DELETE FROM meeting_files")
            conn.execute("DELETE FROM bm25_index")
            conn.execute("DELETE FROM file_summary_bm25")
            conn.execute("DELETE FROM meetings")
            conn.execute("DELETE FROM idempotency_keys")
            conn.execute("DELETE FROM pending_vector_deletions")
            conn.execute("DELETE FROM account_deletion_requests")
            conn.execute("DELETE FROM index_state")
            conn.execute("DELETE FROM memory_decay_state")
            conn.execute("DELETE FROM bm25_stats")
            conn.execute("DELETE FROM speaker_mappings")
            conn.commit()
    except UnsafeTestDatabaseError:
        raise
    except Exception:
        logging.getLogger(__name__).warning("clean_test_database failed", exc_info=True)


@pytest.fixture(autouse=True, scope="function")
def reset_process_health_state(monkeypatch):
    """Prevent lifespan-failure tests from poisoning later readiness checks.

    The production process exits after a fatal startup failure, but pytest
    intentionally exercises that branch in a reusable interpreter. Reset the
    process-local sentinel for every test so randomized ordering is valid.
    """
    import src.api.lifespan as lifespan_module

    monkeypatch.setattr(lifespan_module, "_critical_startup_error", None)


@pytest.fixture(autouse=True)
def release_test_chroma_directories(request):
    """Temporary test collections must not accumulate across randomized tests."""
    owned_directory = (
        request.getfixturevalue("tmp_path") if "tmp_path" in request.fixturenames else None
    )
    yield
    if owned_directory is not None:
        from scripts._bench_env import release_isolated_chroma

        release_isolated_chroma(owned_directory)


@pytest.fixture(autouse=True, scope="function")
def mock_memory_vectorstore(monkeypatch):
    """Mock memory vectorstore upsert to avoid real embedding API calls in tests."""

    class _FakeVS:
        def upsert(
            self,
            user_id,
            key,
            value,
            importance=3,
            category=None,
            meeting_ids=None,
            file_ids=None,
            generation=None,
        ):
            return f"mem_{user_id}_fake"

        def delete(self, embedding_id):
            pass

        def similarity_search(
            self,
            query,
            user_id,
            top_k=5,
            min_importance=1,
            *,
            fetch_multiplier=2,
            allowed_keys=None,
        ):
            return []

        def is_empty(self):
            return True

    monkeypatch.setattr("src.services.memory._vectorstore.get_memory_vectorstore", _FakeVS)
    monkeypatch.setattr("src.services.memory._service._crud.get_memory_vectorstore", _FakeVS)
    monkeypatch.setattr("src.services.memory._service._search.get_memory_vectorstore", _FakeVS)
    monkeypatch.setattr("src.services.memory._service._index_sync.get_memory_vectorstore", _FakeVS)
