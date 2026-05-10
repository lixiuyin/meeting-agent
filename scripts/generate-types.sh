#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_TYPES="$ROOT_DIR/frontend/src/api/generated.d.ts"
TMP_OPENAPI="$(mktemp)"
trap 'rm -f "$TMP_OPENAPI"' EXIT

(
  cd "$BACKEND_DIR"
  uv run python - <<'PY' > "$TMP_OPENAPI"
import json
from src.main import app

print(json.dumps(app.openapi(), ensure_ascii=False))
PY
)

npx --yes openapi-typescript "$TMP_OPENAPI" --output "$FRONTEND_TYPES"
# Format with prettier so the committed file matches repo style;
# otherwise contract-openapi-types CI sees a spurious indentation diff.
(cd "$ROOT_DIR/frontend" && npx --yes prettier --write src/api/generated.d.ts >/dev/null)
echo "Generated $FRONTEND_TYPES"

