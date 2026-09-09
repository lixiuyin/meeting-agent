# Contributing to Meeting Agent

Thank you for your interest in contributing! This guide covers the basics.

## Development Setup

Use Python 3.12 and Node.js 24 (the repository pins them in
`backend/.python-version` and `.node-version`; the frontend supports Node
`>=22 <26`). Prefer the lockfiles so local development matches CI.

```bash
# Backend
cd backend
uv sync --dev                    # recommended (uses uv.lock; includes pytest, ruff, bandit)
cp .env.example .env
# Edit .env and set LLM_API_KEY

# Frontend
cd frontend
npm ci
```

### Pre-commit Hooks

Install pre-commit hooks after cloning (runs ruff, eslint, prettier, bandit, gitleaks, detect-secrets on every commit):

```bash
pip install pre-commit
pre-commit install
```

### Makefile Targets

From the project root, common development commands are available via `make`:

| Target | Description |
|--------|-------------|
| `make dev` | Run backend + frontend concurrently |
| `make dev-be` | Backend only |
| `make dev-fe` | Frontend only |
| `make lint` | Lint everything (backend + frontend) |
| `make test` | Run all tests |
| `make e2e-auth` | Isolated production-mode API-key browser checks |
| `make e2e-full-stack` | Isolated live-provider browser acceptance suite |
| `make qa` | Full QA: lint + tests + build + both Playwright suites |

## Code Style

### Backend (Python)

- Follow [PEP 8](https://peps.python.org/pep-0008/) enforced by [ruff](https://docs.astral.sh/ruff/)
- Line length: 100 characters
- Type hints on all public function signatures
- Docstrings on all public modules, classes, and functions
- Run `uv run ruff check src/` and `uv run ruff format src/` before committing

### Frontend (TypeScript)

- Follow ESLint + Prettier configuration
- `strict: true` TypeScript mode
- Run `npm run lint` before committing

## Commit Messages

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

## Pull Request Process

1. Work on your dedicated long-lived branch
2. Make your changes with clear, focused commits
3. Add tests for new functionality
4. Ensure all existing tests pass: `make test`. Do not hard-code test counts in
   review notes; the suites evolve continuously.
5. Run linters: `cd backend && uv run ruff check src/` and `cd frontend && npm run lint`
6. Push to your branch and open a PR against `main`
7. CI must pass; a CODEOWNERS review is required before merge

## Documentation

- Repository index (diagrams + product ADRs): [`docs/README.md`](docs/README.md)
- Backend subsystem docs: [`backend/docs/README.md`](backend/docs/README.md)
- Frontend subsystem docs: [`frontend/docs/README.md`](frontend/docs/README.md)
- Agent-oriented map: [`docs/README.md`](docs/README.md)

## Reporting Issues

- Use GitHub Issues
- Include steps to reproduce, expected vs actual behavior
- Specify OS, Python version, and relevant dependency versions
