"""Temporary environment for benchmark harness isolation."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


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

    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    vectordb_dir.mkdir(parents=True, exist_ok=True)

    old_environ = {
        "DATA_DIR": os.environ.get("DATA_DIR"),
        "UPLOAD_DIR": os.environ.get("UPLOAD_DIR"),
        "VECTOR_DB_DIR": os.environ.get("VECTOR_DB_DIR"),
    }

    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["VECTOR_DB_DIR"] = str(vectordb_dir)

    # Monkey-patch constants before any src import happens later
    import src.core.constants as constants_module

    constants_module.DATA_DIR = data_dir
    constants_module.UPLOAD_DIR = upload_dir
    constants_module.VECTOR_DB_DIR = vectordb_dir
    constants_module.DB_PATH = data_dir / "meetings.db"

    # Propagate path changes to the already-imported settings singleton
    # (src.core.config may have been pulled in by earlier imports, e.g.
    #  benchmark.py -> _bench_rag_phase1 -> _bench_amicorpus -> src.core.database)
    try:
        import src.core.config as _config_module

        _config_module.settings.DB_PATH = data_dir / "meetings.db"
        _config_module.settings.UPLOAD_DIR = upload_dir
        _config_module.settings.VECTOR_DB_DIR = vectordb_dir
    except Exception:
        pass

    # Reset singletons that cache the vectorstore path
    try:
        from src.services.rag._vectorstore import reset_vectorstore
        reset_vectorstore()
    except Exception:
        pass

    try:
        yield tmpdir
    finally:
        for key, value in old_environ.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmpdir, ignore_errors=True)
