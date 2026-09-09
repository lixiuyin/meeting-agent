#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" == Darwin && "${MEETING_AGENT_PROTECTED_RUN:-}" != 1 ]]; then
  exec "${repo_root}/backend/.venv/bin/python" "${repo_root}/scripts/run-protected.py" -- bash "$0" "$@"
fi
e2e_data_dir="$(mktemp -d "${TMPDIR:-/tmp}/meeting-agent-e2e.XXXXXX")"
backend_port="${E2E_BACKEND_PORT:-17008}"
frontend_port="${E2E_FRONTEND_PORT:-18307}"
timestamp="$(date -u '+%Y-%m-%dT%H-%M-%SZ')"
metrics_path="${E2E_METRICS_OUTPUT:-${repo_root}/backend/benchmark-results/e2e-smoke_${timestamp}.json}"
session_summary_enabled="${E2E_SESSION_SUMMARY_ENABLED:-false}"
api_key="${E2E_API_KEY:-}"
environment="${E2E_ENVIRONMENT:-dev}"
principal_pepper="${E2E_PRINCIPAL_PEPPER:-e2e-only-principal-pepper-change-me}"
backend_pid=""
frontend_pid=""

if [[ "${environment}" != "dev" && -z "${api_key}" ]]; then
  echo "E2E_API_KEY is required when E2E_ENVIRONMENT=${environment}" >&2
  exit 2
fi

cleanup() {
  local exit_status=$?
  for pid in "${frontend_pid}" "${backend_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
  for pid in "${frontend_pid}" "${backend_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      for _ in 1 2 3 4 5; do
        kill -0 "${pid}" 2>/dev/null || break
        sleep 0.2
      done
      kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
  if [[ "${exit_status}" -ne 0 ]]; then
    mkdir -p "$(dirname "${metrics_path}")"
    cp "${e2e_data_dir}/backend.log" "${metrics_path%.json}.backend.log" 2>/dev/null || true
    echo "Backend E2E log tail:" >&2
    tail -n 80 "${e2e_data_dir}/backend.log" 2>/dev/null >&2 || true
    echo "Frontend E2E log tail:" >&2
    tail -n 40 "${e2e_data_dir}/frontend.log" 2>/dev/null >&2 || true
  fi
  case "${e2e_data_dir}" in
    "${TMPDIR:-/tmp}"/meeting-agent-e2e.*) rm -rf -- "${e2e_data_dir}" ;;
    *) echo "Refusing to remove unexpected E2E directory: ${e2e_data_dir}" >&2 ;;
  esac
  return "${exit_status}"
}
trap cleanup EXIT INT TERM

export MEETING_AGENT_DATA_DIR="${e2e_data_dir}"
export DATA_DIR="${e2e_data_dir}"
export DB_PATH="${e2e_data_dir}/meetings.db"
export UPLOAD_DIR="${e2e_data_dir}/uploads"
export VECTOR_DB_DIR="${e2e_data_dir}/vectordb"
export LOG_DIR="${e2e_data_dir}/logs"
export CUSTOM_SKILLS_DIR="${e2e_data_dir}/skills"
export MEETING_AUTO_SUMMARIZE_FILES=false
export COMBINED_EXTRACTION_ENABLED=false
export SESSION_SUMMARY_ENABLED="${session_summary_enabled}"
export E2E_PRINCIPAL_ID="e2e_isolated_principal"

cd "${repo_root}"
unset MEETING_AGENT_DISABLE_DOTENV

E2E_SOURCE_REVISION="$(git rev-parse HEAD)"
export E2E_SOURCE_REVISION
read -r E2E_DATASET_FINGERPRINT E2E_HARNESS_FINGERPRINT E2E_IMPLEMENTATION_FINGERPRINT < <(
  cd backend
  uv run python -c 'from scripts.benchmark import _capture_e2e_fingerprints; p = _capture_e2e_fingerprints(); print(p["dataset_fingerprint_sha256"], p["harness_fingerprint_sha256"], p["implementation_fingerprint_sha256"])'
)
export E2E_DATASET_FINGERPRINT
export E2E_HARNESS_FINGERPRINT
export E2E_IMPLEMENTATION_FINGERPRINT

(
  cd backend
  exec env \
    PYTHONUNBUFFERED=1 \
    DATA_DIR="${e2e_data_dir}" \
    DB_PATH="${e2e_data_dir}/meetings.db" \
    UPLOAD_DIR="${e2e_data_dir}/uploads" \
    VECTOR_DB_DIR="${e2e_data_dir}/vectordb" \
    LOG_DIR="${e2e_data_dir}/logs" \
    API_KEY="${api_key}" \
    ENVIRONMENT="${environment}" \
    PRINCIPAL_PEPPER="${principal_pepper}" \
    PRINCIPAL_ID="${E2E_PRINCIPAL_ID}" \
    CORS_ORIGINS="http://127.0.0.1:${frontend_port}" \
    TRUSTED_HOSTS="127.0.0.1,localhost" \
    RAGANYTHING_ENABLED=false \
    MEETING_AUTO_SUMMARIZE_FILES=false \
    COMBINED_EXTRACTION_ENABLED=false \
    SESSION_SUMMARY_ENABLED="${session_summary_enabled}" \
    .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port "${backend_port}"
) >"${e2e_data_dir}/backend.log" 2>&1 &
backend_pid=$!

HEALTH_URL="http://127.0.0.1:${backend_port}/api/v1/health/ready" \
  MAX_WAIT_SECONDS=180 ./scripts/wait-for-health.sh

(
  cd frontend
  exec env \
    VITE_API_KEY="${api_key}" \
    VITE_BACKEND_PROXY_TARGET="http://127.0.0.1:${backend_port}" \
    ./node_modules/.bin/vite --host 127.0.0.1 --port "${frontend_port}" --strictPort
) >"${e2e_data_dir}/frontend.log" 2>&1 &
frontend_pid=$!

frontend_waited=0
until curl --silent --fail "http://127.0.0.1:${frontend_port}/" >/dev/null 2>&1; do
  if [[ "${frontend_waited}" -ge 60 ]]; then
    echo "Frontend readiness timeout on port ${frontend_port}" >&2
    exit 1
  fi
  sleep 1
  frontend_waited=$((frontend_waited + 1))
done

export PLAYWRIGHT_FULL_STACK=1
export PLAYWRIGHT_SKIP_WEBSERVER=1
export PLAYWRIGHT_BASE_URL="http://127.0.0.1:${frontend_port}"
export E2E_METRICS_OUTPUT="${metrics_path}"
export VITE_API_KEY="${api_key}"

cd frontend
if [[ "$#" -gt 0 ]]; then
  e2e_specs=("$@")
else
  e2e_specs=(e2e/full-stack/upload-and-chat.spec.ts)
fi
npx playwright test --project=chromium --workers=1 "${e2e_specs[@]}"
if [[ -f "${metrics_path}" ]]; then
  echo "E2E smoke metrics: ${metrics_path}"
else
  echo "Selected browser tests passed; no upload/chat metrics were generated."
fi
