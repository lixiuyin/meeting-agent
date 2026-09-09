SHELL := /bin/bash

.PHONY: help start stop status kill-ports kill-backend-port kill-frontend-port dev dev-be dev-fe cli lint lint-be lint-fe lint-docs type-check validate-config validate-migrations security-audit release-readiness test test-be test-fe eval-audit contract-openapi e2e-smoke e2e-auth e2e-full-stack qa build clean

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---- Development ----

start: ## Build and start persistent backend/frontend containers
	docker compose up --detach --build --wait

stop: ## Gracefully stop persistent backend/frontend containers
	docker compose stop

status: ## Show persistent service status
	docker compose ps

kill-backend-port: ## Gracefully stop the backend dev port (7008)
	@pids=$$(lsof -tiTCP:7008 -sTCP:LISTEN 2>/dev/null || true); \
	for pid in $$pids; do kill -TERM $$pid 2>/dev/null || true; done; \
	for pid in $$pids; do \
		for _ in 1 2 3 4 5; do kill -0 $$pid 2>/dev/null || break; sleep 0.2; done; \
		kill -0 $$pid 2>/dev/null && kill -KILL $$pid 2>/dev/null || true; \
	done

kill-frontend-port: ## Gracefully stop the frontend dev port (8307)
	@pids=$$(lsof -tiTCP:8307 -sTCP:LISTEN 2>/dev/null || true); \
	for pid in $$pids; do kill -TERM $$pid 2>/dev/null || true; done; \
	for pid in $$pids; do \
		for _ in 1 2 3 4 5; do kill -0 $$pid 2>/dev/null || break; sleep 0.2; done; \
		kill -0 $$pid 2>/dev/null && kill -KILL $$pid 2>/dev/null || true; \
	done

kill-ports: kill-backend-port kill-frontend-port ## Gracefully stop both dev ports

dev: kill-ports ## Run backend + frontend dev servers
	@umask 077; \
		mkdir -p data/logs; chmod 700 data data/logs; \
		LOG_FILE=data/logs/dev-console.log; \
		if [ -f "$$LOG_FILE" ] && [ "$$(wc -c < "$$LOG_FILE")" -gt 10485760 ]; then \
			mv "$$LOG_FILE" "$$LOG_FILE.1"; chmod 600 "$$LOG_FILE.1"; \
		fi; \
		touch "$$LOG_FILE"; chmod 600 "$$LOG_FILE"; \
	{ \
		echo "\n========================================================================"; \
		echo "  DEV SESSION STARTED -- $$(date '+%Y-%m-%d %H:%M:%S %Z')"; \
		echo "========================================================================"; \
		set -euo pipefail; \
		trap 'if [ -n "$${BACKEND_PID:-}" ]; then kill "$$BACKEND_PID" 2>/dev/null || true; fi' EXIT INT TERM; \
		( cd backend && LOG_LEVEL=$${LOG_LEVEL:-DEBUG} uv run python -m uvicorn src.main:app \
			--reload --port 7008 \
			--reload-dir src \
			--reload-dir config \
			--reload-dir skills \
			--reload-include '*.py' \
			--reload-include '*.yaml' \
			--reload-include '*.yml' \
			--reload-include '*.md' \
		) & \
		BACKEND_PID=$$!; \
		echo "Backend starting (PID=$$BACKEND_PID), waiting for /api/v1/health/ready ..."; \
		HEALTH_URL=http://127.0.0.1:7008/api/v1/health/ready MAX_WAIT_SECONDS=180 ./scripts/wait-for-health.sh; \
		echo "Backend is ready, starting frontend ..."; \
		cd frontend && npm run dev; \
	} 2>&1 | tee -a "$$LOG_FILE"

dev-be: kill-backend-port ## Run backend dev server
	umask 077; cd backend && LOG_LEVEL=$${LOG_LEVEL:-DEBUG} uv run python -m uvicorn src.main:app \
		--reload --port 7008 \
		--reload-dir src \
		--reload-dir config \
		--reload-dir skills \
		--reload-include '*.py' \
		--reload-include '*.yaml' \
		--reload-include '*.yml' \
		--reload-include '*.md'

dev-fe: kill-frontend-port ## Run frontend dev server
	cd frontend && npm run dev

cli: ## Run backend interactive CLI frontend
	cd backend && uv run python -m scripts.cli_agent

# ---- Linting ----

lint: lint-be lint-fe ## Lint everything

lint-be: ## Lint backend (ruff)
	cd backend && uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/

lint-fe: ## Lint frontend (eslint + prettier)
	cd frontend && npm run lint && npm run format:check

lint-docs: ## Validate Markdown links, Mermaid fences, and README parity
	python3 scripts/check_docs.py

type-check: ## Type-check backend and frontend
	cd backend && uv run pyright src/
	cd frontend && npm run type-check

validate-config: ## Verify canonical private/public environment layout
	cd backend && uv run python -m scripts.check_env_example

validate-migrations: ## Verify one Alembic head and immutable published revisions
	cd backend && test "$$(uv run alembic heads | wc -l | tr -d ' ')" = "1"
	cd backend && uv run python -m scripts.check_alembic_immutability

security-audit: ## Audit dependencies and enforce the embedded-only Chroma mitigation
	@set -euo pipefail; \
		audit_file=$$(mktemp); \
		trap 'rm -f "$$audit_file"' EXIT; \
		site_packages=$$(cd backend && uv run python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'); \
		uvx pip-audit --path "$$site_packages" --format json >"$$audit_file" || true; \
		cd backend && uv run python -m scripts.check_chroma_advisories --audit "$$audit_file"

release-readiness: ## Validate clean production-quality/human/SLO/security evidence
	cd backend && uv run python -m scripts.check_release_readiness \
		--evidence ../docs/validation/release-readiness.json

# ---- Testing ----

test: test-be test-fe ## Run all tests

test-be: ## Run backend tests
	cd backend && $(if $(filter Darwin,$(shell uname -s)),uv run python ../scripts/run-protected.py -- )uv run python -m pytest -v

test-fe: ## Run frontend tests
	cd frontend && npm run test:run

eval-audit: ## Validate the versioned evaluation protocol (offline, no API calls)
	cd backend && uv run python ../scripts/run-isolated.py -- uv run python -m scripts.benchmark protocol-audit
	cd backend && uv run python ../scripts/run-isolated.py -- uv run python -m scripts.benchmark evidence-governance

contract-openapi: ## Verify generated frontend API types match FastAPI OpenAPI
	./scripts/generate-types.sh --check

e2e-smoke: ## Run an isolated upload -> ready -> cited-chat smoke test
	./scripts/run-isolated-e2e-smoke.sh

e2e-auth: ## Run production-mode API-key browser acceptance tests in isolated storage
	E2E_ENVIRONMENT=production E2E_API_KEY=e2e-test-key-primary \
		./scripts/run-isolated-e2e-smoke.sh e2e/full-stack/auth-isolation.spec.ts

e2e-full-stack: ## Run all production-auth browser acceptance tests in isolated storage
	E2E_ENVIRONMENT=production E2E_API_KEY=e2e-test-key-primary \
		./scripts/run-isolated-e2e-smoke.sh e2e/full-stack

# ---- One-click QA ----

qa: ## One-click full pipeline: lint + tests + full-stack e2e
	cd backend && uv sync --dev
	cd frontend && npm ci
	cd frontend && npx playwright install chromium firefox webkit
	$(MAKE) validate-config validate-migrations lint lint-docs type-check contract-openapi eval-audit test
	cd frontend && npm run build
	$(MAKE) e2e-auth e2e-full-stack

# ---- Build ----

build: ## Docker build
	docker compose build

# ---- Cleanup ----

clean: ## Remove generated caches only (never deletes application data)
	find backend frontend -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist frontend/coverage frontend/node_modules/.cache
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +

purge-data: ## Explicitly delete local application data (CONFIRM_PURGE_DATA=yes required)
	@test "$(CONFIRM_PURGE_DATA)" = "yes" || (echo "Refusing: set CONFIRM_PURGE_DATA=yes" >&2; exit 1)
	rm -rf data
