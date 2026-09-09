# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python dependencies
# ---------------------------------------------------------------------------
# No apt build tools needed: llama-cpp-python is excluded via
# --no-install-package below, and all other deps ship as wheels.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
FROM ${PYTHON_IMAGE} AS builder

# Install uv from official image (pinned for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/

WORKDIR /build

# Dependency layer (cached unless pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock* ./

# --no-dev: exclude dev tools (pytest, ruff) from production image
# --no-cache: keep image lean
# --no-install-package llama-cpp-python: avoid compilation OOM
# --compile-bytecode: pre-compile .pyc for faster cold start
# --frozen: install the reviewed lock without resolving again during the build.
RUN uv sync --frozen --no-dev --no-cache --no-install-package llama-cpp-python --compile-bytecode

# Source layer
COPY src/ ./src/
COPY skills/ ./skills/
COPY config/ ./config/
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# Ensure data mount point exists with correct ownership at build time
RUN mkdir -p /build/data

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal image with only runtime dependencies
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Runtime system deps only (no build tools).
# - ffmpeg: AV processing (asr/_audio_clip.py, processor/_processors/av.py)
# - libreoffice-impress: legacy .ppt/.doc/.xls → modern format conversion via soffice
# - curl: HEALTHCHECK below
# OCR is provided by cloud providers (Marker / MinerU / Paddle); we do not
# ship local tesseract — pytesseract is not a declared Python dep and the
# parser gracefully falls back when it is unavailable.
# Apply the latest security updates available for the pinned Debian base.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    ffmpeg \
    libreoffice-impress \
    curl \
    gosu \
    && apt-get -o Acquire::Retries=3 upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Runtime uses the locked virtualenv; remove the unused base-image installer.
RUN python -m pip uninstall -y pip

# Create non-root user at build time (no gosu needed)
RUN useradd --create-home --uid 10001 appuser

# Copy application from builder with correct ownership
COPY --from=builder --chown=appuser:appuser /build /app

# Copy entrypoint script for runtime permission fix (L-5)
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app

# Ensure data directory is owned by appuser
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# Start as root only long enough for the entrypoint to repair bind-mount
# ownership; Compose grants CHOWN/SETUID/SETGID and the entrypoint immediately
# drops to appuser before launching Python.
USER root

# Add .venv to PATH so uvicorn / python are available without `uv run`
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000

EXPOSE 8000

# Standalone image health follows readiness; orchestration should use the
# distinct /health/live endpoint as its liveness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/ready || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
