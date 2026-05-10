#!/usr/bin/env bash
set -euo pipefail

HEALTH_URL="${HEALTH_URL:-http://localhost:7008/api/v1/health}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-120}"
SLEEP_SECONDS=2

elapsed=0
until curl --silent --fail "${HEALTH_URL}" >/dev/null 2>&1; do
  if [ "${elapsed}" -ge "${MAX_WAIT_SECONDS}" ]; then
    echo "Health check timeout: ${HEALTH_URL}" >&2
    exit 1
  fi
  sleep "${SLEEP_SECONDS}"
  elapsed=$((elapsed + SLEEP_SECONDS))
done

echo "Service healthy: ${HEALTH_URL}"
