.PHONY: help kill-ports dev dev-be dev-fe cli lint lint-be lint-fe test test-be test-fe qa build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---- Development ----

kill-ports: ## Kill processes on dev ports (8000, 5173)
	@for port in 8000 5173; do \
		pids=$$(lsof -ti :$$port 2>/dev/null) && { echo "Killing PID(s) on port $$port: $$pids"; echo $$pids | xargs kill -9 2>/dev/null || true; } || true; \
	done

dev: kill-ports ## Run backend + frontend dev servers
	@rm -rf data/logs; mkdir -p data/logs; \
	LOG_FILE=data/logs/dev-console.log; \
	{ \
		echo "\n========================================================================"; \
		echo "  DEV SESSION STARTED -- $$(date '+%Y-%m-%d %H:%M:%S %Z')"; \
		echo "========================================================================"; \
		set -euo pipefail; \
		trap 'if [ -n "$${BACKEND_PID:-}" ]; then kill "$$BACKEND_PID" 2>/dev/null || true; fi' EXIT INT TERM; \
		( cd backend && LOG_LEVEL=$${LOG_LEVEL:-DEBUG} uv run python -m uvicorn src.main:app \
			--reload --port 8000 \
			--reload-dir src \
			--reload-dir config \
			--reload-dir skills \
			--reload-include '*.py' \
			--reload-include '*.yaml' \
			--reload-include '*.yml' \
			--reload-include '*.md' \
		) & \
		BACKEND_PID=$$!; \
		echo "Backend starting (PID=$$BACKEND_PID), waiting for /api/v1/health ..."; \
		HEALTH_URL=http://127.0.0.1:8000/api/v1/health MAX_WAIT_SECONDS=180 ./scripts/wait-for-health.sh; \
		echo "Backend is ready, starting frontend ..."; \
		cd frontend && npm run dev; \
	} 2>&1 | tee -a "$$LOG_FILE"

dev-be: kill-ports ## Run backend dev server
	cd backend && LOG_LEVEL=$${LOG_LEVEL:-DEBUG} uv run python -m uvicorn src.main:app \
		--reload --port 8000 \
		--reload-dir src \
		--reload-dir config \
		--reload-dir skills \
		--reload-include '*.py' \
		--reload-include '*.yaml' \
		--reload-include '*.yml' \
		--reload-include '*.md'

dev-fe: kill-ports ## Run frontend dev server
	cd frontend && npm run dev

cli: ## Run backend interactive CLI frontend
	cd backend && uv run python -m scripts.cli_agent

# ---- Linting ----

lint: lint-be lint-fe ## Lint everything

lint-be: ## Lint backend (ruff)
	cd backend && ruff check src/ tests/ && ruff format --check src/ tests/

lint-fe: ## Lint frontend (eslint + prettier)
	cd frontend && npm run lint && npm run format:check

# ---- Testing ----

test: test-be test-fe ## Run all tests

test-be: ## Run backend tests
	cd backend && uv run python -m pytest -v

test-fe: ## Run frontend tests
	cd frontend && npm run test:run

# ---- One-click QA ----

qa: ## One-click full pipeline: lint + tests + full-stack e2e
	@set -euo pipefail; \
	echo "==> Backend quality gate"; \
	cd backend && uv sync --dev && uv run ruff check src/ tests/ && uv run python -m pytest -m "not benchmark" --ignore=tests/e2e; \
	echo "==> Frontend quality gate"; \
	cd ../frontend && npm ci && npm run lint && npm run type-check && npm run test:run; \
	echo "==> Start full stack and run E2E"; \
	cd ../frontend && npx playwright install chromium; \
	cd .. && docker compose up -d --build; \
	./scripts/wait-for-health.sh; \
	cd frontend && PLAYWRIGHT_BASE_URL=http://localhost:8307 PLAYWRIGHT_SKIP_WEBSERVER=1 npm run e2e:ci -- \
		e2e/full-stack/upload-and-chat.spec.ts \
		e2e/session-resume.spec.ts \
		e2e/memory-crud.spec.ts \
		e2e/settings-rebuild.spec.ts \
		e2e/websocket-notify.spec.ts \
		e2e/stream-abort.spec.ts; \
	echo "QA finished. Docker services are still running."

# ---- Build ----

build: ## Docker build
	docker compose build

# ---- Cleanup ----

clean: ## Remove generated files
	rm -rf backend/data backend/__pycache__ backend/src/__pycache__
	rm -rf frontend/dist frontend/node_modules/.cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
