# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python dependencies
# ---------------------------------------------------------------------------
# No apt build tools needed: llama-cpp-python is excluded via
# --no-install-package below, and all other deps ship as wheels.
FROM python:3.12-slim AS builder

# Install uv from official image (pinned for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/

WORKDIR /build

# Dependency layer (cached unless pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock* ./

# --no-dev: exclude dev tools (pytest, ruff) from production image
# --no-cache: keep image lean
# --no-install-package llama-cpp-python: avoid compilation OOM
# --compile-bytecode: pre-compile .pyc for faster cold start
# --mount=type=cache: cache uv downloads across builds
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-cache --no-install-package llama-cpp-python --compile-bytecode

# Source layer
COPY src/ ./src/
COPY skills/ ./skills/
COPY config/ ./config/

# Ensure data mount point exists with correct ownership at build time
RUN mkdir -p /build/data

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal image with only runtime dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Runtime system deps only (no build tools).
# - ffmpeg: AV processing (asr/_audio_clip.py, processor/_processors/av.py)
# - libreoffice-impress: legacy .ppt/.doc/.xls → modern format conversion via soffice
# - curl: HEALTHCHECK below
# OCR is provided by cloud providers (Marker / MinerU / Paddle); we do not
# ship local tesseract — pytesseract is not a declared Python dep and the
# parser gracefully falls back when it is unavailable.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libreoffice-impress \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

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

USER appuser

# Add .venv to PATH so uvicorn / python are available without `uv run`
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Liveness probe — lightweight, checks process is alive
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
