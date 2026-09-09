"""Project-wide constants and path definitions.

Tunable engine constants live here so they are discoverable without
digging through service implementations.  Per-package constants in
individual ``_constants.py`` / ``_generate_helpers.py`` modules
are still the primary location for package-internal values.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Engine tuning constants — conservative defaults, intended to be
# overridden via ``core/config.py`` when the value is user-facing.
# ---------------------------------------------------------------------------

# Token estimation
CHARS_PER_TOKEN = 3.5  # conservative estimate for mixed CJK/EN text

# Streaming timeouts (seconds)
PRE_TOKEN_HEARTBEAT_TIMEOUT_S = 15.0
ACLOSE_TIMEOUT_S = 5.0
GLOBAL_HEARTBEAT_INTERVAL_S = 30.0

# Fact extraction
FACT_EXTRACT_MAX_RETRIES = 2
FACT_EXTRACT_BASE_DELAY_S = 1.0
EXTRACTION_CIRCUIT_BREAKER_THRESHOLD = 10

# PROJECT_ROOT resolves to `backend/` locally and `/app` inside Docker. It hosts
# the source tree (`src/`) and `config/`, so CONFIG_DIR stays anchored here.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# DATA_DIR is environment-adaptive so local runs and the Docker volume mount
# (`./data:/app/data` in docker-compose.yaml) agree on a single canonical
# location:
#   * Source checkout: use `<repo-root>/data/` (sibling of `backend/`). Detected
#     by the presence of a `backend/` directory one level above PROJECT_ROOT.
#   * Docker (`/app`): no sibling `backend/` exists, so fall back to
#     `PROJECT_ROOT / "data"` which matches the volume mount at `/app/data`.
_REPO_ROOT_CANDIDATE = PROJECT_ROOT.parent
_DEFAULT_DATA_DIR = (
    _REPO_ROOT_CANDIDATE / "data"
    if (_REPO_ROOT_CANDIDATE / "backend").is_dir()
    else PROJECT_ROOT / "data"
)
DATA_DIR = Path(os.getenv("DATA_DIR", str(_DEFAULT_DATA_DIR))).expanduser()

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads"))).expanduser()
VECTOR_DB_DIR = Path(os.getenv("VECTOR_DB_DIR", str(DATA_DIR / "vectordb"))).expanduser()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "meetings.db"))).expanduser()
LOG_DIR = Path(os.getenv("LOG_DIR", str(DATA_DIR / "logs"))).expanduser()
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "main.yaml"
