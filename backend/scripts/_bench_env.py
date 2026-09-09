"""Temporary environment for benchmark harness isolation."""

import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_PATH_ENV_KEYS = (
    "DATA_DIR",
    "DB_PATH",
    "UPLOAD_DIR",
    "VECTOR_DB_DIR",
    "LOG_DIR",
    "CUSTOM_SKILLS_DIR",
)


def release_isolated_chroma(root: Path) -> None:
    """Stop only systems inside an owned, quiescent benchmark/test directory.

    Chroma caches systems process-wide; dropping LangChain wrappers does not
    release them. Never call this for a live application or shared corpus.
    """
    from chromadb.api.shared_system_client import SharedSystemClient

    root = root.resolve()
    if root == Path(root.anchor) or root == Path.home():
        raise ValueError("Refusing a broad Chroma shutdown target")
    for identifier, system in list(SharedSystemClient._identifier_to_system.items()):
        path = Path(identifier)
        if path.is_absolute() and path.resolve().is_relative_to(root):
            system.stop()
            SharedSystemClient._identifier_to_system.pop(identifier, None)
            SharedSystemClient._identifier_to_refcount.pop(identifier, None)


def _reset_path_bound_services() -> None:
    """Release connections/singletons before changing or deleting their paths."""
    with contextlib.suppress(Exception):
        from src.services.memory._vectorstore import reset_memory_vectorstore

        reset_memory_vectorstore()
    with contextlib.suppress(Exception):
        from src.core.database import close_all_connections

        close_all_connections()
    with contextlib.suppress(Exception):
        from src.services.rag._meeting_summary_vectorstore import (
            reset_meeting_summary_vectorstore,
        )
        from src.services.rag._raganything import reset_raganything
        from src.services.rag._summary_vectorstore import reset_summary_vectorstore
        from src.services.rag._vectorstore import reset_vectorstore

        reset_vectorstore()
        reset_summary_vectorstore()
        reset_meeting_summary_vectorstore()
        reset_raganything()


def _patch_imported_path_aliases(*, data_dir: Path, log_dir: Path) -> None:
    """Update modules that imported path constants by value."""
    aliases = {
        "src.core.trace": {"LOG_DIR": log_dir},
        "src.core.logging": {"LOG_DIR": log_dir},
        "src.services.rag._raganything": {"DATA_DIR": data_dir},
    }
    for module_name, values in aliases.items():
        module = sys.modules.get(module_name)
        if module is not None:
            for key, value in values.items():
                setattr(module, key, value)

    memory_common = sys.modules.get("src.services.memory._common")
    if memory_common is not None:
        memory_common._SESSION_CACHE_PATH = data_dir / "session_cache.json"
        memory_common._SESSION_CACHE_PATH_LEGACY = data_dir / "session_cache.pkl"


@contextlib.contextmanager
def bench_environment():
    """Yield a temporary directory configured for benchmark runs.

    Sets environment variables before any src.* import so that the
    benchmark uses an isolated database and vector store.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="benchmark_"))
    data_dir = tmpdir / "data"
    upload_dir = tmpdir / "uploads"
    vectordb_dir = tmpdir / "vectordb"
    log_dir = tmpdir / "logs"

    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    vectordb_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    old_environ = {key: os.environ.get(key) for key in _PATH_ENV_KEYS}

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_PATH"] = str(data_dir / "meetings.db")
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["VECTOR_DB_DIR"] = str(vectordb_dir)
    os.environ["LOG_DIR"] = str(log_dir)
    os.environ["CUSTOM_SKILLS_DIR"] = str(tmpdir / "skills")

    # Monkey-patch constants before any src import happens later
    import src.core.constants as constants_module

    path_values = {
        "DATA_DIR": data_dir,
        "DB_PATH": data_dir / "meetings.db",
        "UPLOAD_DIR": upload_dir,
        "VECTOR_DB_DIR": vectordb_dir,
        "LOG_DIR": log_dir,
    }
    old_constants = {key: getattr(constants_module, key) for key in path_values}
    for key, value in path_values.items():
        setattr(constants_module, key, value)

    # Propagate path changes to the already-imported settings singleton
    # (src.core.config may have been pulled in by earlier imports, e.g.
    #  benchmark.py -> _bench_rag_phase1 -> _bench_amicorpus -> src.core.database)
    import src.core.config as _config_module

    setting_keys = ("DB_PATH", "UPLOAD_DIR", "VECTOR_DB_DIR", "CUSTOM_SKILLS_DIR")
    path_values["CUSTOM_SKILLS_DIR"] = tmpdir / "skills"
    old_settings = {key: getattr(_config_module.settings, key) for key in setting_keys}
    for key in setting_keys:
        setattr(_config_module.settings, key, path_values[key])

    _patch_imported_path_aliases(data_dir=data_dir, log_dir=log_dir)
    _reset_path_bound_services()

    try:
        yield tmpdir
    finally:
        _reset_path_bound_services()
        for key, value in old_constants.items():
            setattr(constants_module, key, value)
        for key, value in old_settings.items():
            setattr(_config_module.settings, key, value)
        _patch_imported_path_aliases(
            data_dir=old_constants["DATA_DIR"],
            log_dir=old_constants["LOG_DIR"],
        )
        for key, value in old_environ.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        release_isolated_chroma(tmpdir)
        shutil.rmtree(tmpdir, ignore_errors=True)


@contextlib.contextmanager
def seeded_bench_environment(*, source_db: Path, source_vector_dir: Path):
    """Clone a real corpus into the normal isolated benchmark environment.

    SQLite's backup API produces a consistent snapshot even when the source
    application is running. Vector data is copied after path-bound services
    have been reset, so benchmark writes never touch the production corpus.
    """
    source_db = source_db.resolve()
    source_vector_dir = source_vector_dir.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"Source database not found: {source_db}")
    if not source_vector_dir.is_dir():
        raise FileNotFoundError(f"Source vector directory not found: {source_vector_dir}")

    with bench_environment() as tmpdir:
        target_db = tmpdir / "data" / "meetings.db"
        with (
            contextlib.closing(
                sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)
            ) as source,
            contextlib.closing(sqlite3.connect(target_db)) as target,
        ):
            source.backup(target)
        shutil.copytree(source_vector_dir, tmpdir / "vectordb", dirs_exist_ok=True)
        _reset_path_bound_services()
        yield tmpdir
