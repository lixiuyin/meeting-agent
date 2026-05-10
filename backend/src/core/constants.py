"""Project-wide constants and path definitions.

Tunable engine constants live here so they are discoverable without
digging through service implementations.  Per-package constants in
individual ``_constants.py`` / ``_generate_helpers.py`` modules
are still the primary location for package-internal values.
"""

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
DATA_DIR = (
    _REPO_ROOT_CANDIDATE / "data"
    if (_REPO_ROOT_CANDIDATE / "backend").is_dir()
    else PROJECT_ROOT / "data"
)

UPLOAD_DIR = DATA_DIR / "uploads"
VECTOR_DB_DIR = DATA_DIR / "vectordb"
DB_PATH = DATA_DIR / "meetings.db"
LOG_DIR = DATA_DIR / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "main.yaml"
