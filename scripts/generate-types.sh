#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" == Darwin && "${MEETING_AGENT_PROTECTED_RUN:-}" != 1 ]]; then
  exec "${ROOT_DIR}/backend/.venv/bin/python" "${ROOT_DIR}/scripts/run-protected.py" -- bash "$0" "$@"
fi
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_TYPES="$ROOT_DIR/frontend/src/api/generated.d.ts"
MODE="${1:-write}"
if [[ "$MODE" != "write" && "$MODE" != "--check" ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d)"
TMP_OPENAPI="$TMP_DIR/openapi.json"
TMP_TYPES="$TMP_DIR/generated.d.ts"
trap 'rm -rf "$TMP_DIR"' EXIT

OUTPUT_TYPES="$FRONTEND_TYPES"
if [[ "$MODE" == "--check" ]]; then
  OUTPUT_TYPES="$TMP_TYPES"
fi

(
  cd "$BACKEND_DIR"
  MEETING_AGENT_DISABLE_DOTENV=1 \
  CUSTOM_SKILLS_DIR="$TMP_DIR/skills" \
  DATA_DIR="$TMP_DIR/data" \
  DB_PATH="$TMP_DIR/data/meetings.db" \
  UPLOAD_DIR="$TMP_DIR/uploads" \
  VECTOR_DB_DIR="$TMP_DIR/vectordb" \
  LOG_DIR="$TMP_DIR/logs" \
    uv run python - <<'PY' > "$TMP_OPENAPI"
import json
from src.main import app

print(json.dumps(app.openapi(), ensure_ascii=False))
PY
)

(cd "$ROOT_DIR/frontend" && npx --no-install openapi-typescript "$TMP_OPENAPI" --output "$OUTPUT_TYPES")
# Format with prettier so the committed file matches repo style;
# otherwise contract-openapi-types CI sees a spurious indentation diff.
(cd "$ROOT_DIR/frontend" && \
  npx --no-install prettier --config .prettierrc --write "$OUTPUT_TYPES" >/dev/null)

if [[ "$MODE" == "--check" ]]; then
  if ! cmp -s "$FRONTEND_TYPES" "$TMP_TYPES"; then
    echo "OpenAPI-generated frontend types are stale. Run ./scripts/generate-types.sh" >&2
    diff -u "$FRONTEND_TYPES" "$TMP_TYPES" || true
    exit 1
  fi
  echo "OpenAPI-generated frontend types are current"
else
  echo "Generated $FRONTEND_TYPES"
fi
