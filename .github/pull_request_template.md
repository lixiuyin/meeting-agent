## Summary

Brief description of changes.

## Changes

-

## Test Plan

- [ ] Backend tests pass: `cd backend && uv run python -m pytest`
- [ ] Frontend tests pass: `cd frontend && npm run test:run`
- [ ] Backend lint: `cd backend && uv run ruff check src/`
- [ ] Frontend lint: `cd frontend && npm run lint`
- [ ] Type check: `cd backend && uv run pyright` / `cd frontend && npm run type-check`
- [ ] Manually tested:

## Governance Checklist

- [ ] If behavior or reliability changed, SLO impact reviewed (`backend/docs/operations/slo.md`)
- [ ] If operational procedure changed, related runbook updated (`backend/docs/operations/runbooks/`)
- [ ] If architectural decision changed, ADR added/updated (`docs/adr/` and/or `backend/docs/adr/`)
- [ ] If data handling changed, backup/restore/retention docs updated
- [ ] If schema changed, Alembic revision added and validated (`backend/docs/operations/alembic.md`)

## Release & Security Checklist

- [ ] For release-impacting changes, release notes impact is described
- [ ] Dependency/security changes validated in CI (`pip-audit`, `bandit`, `npm audit`)
- [ ] SBOM generation remains green in CI artifacts (backend/frontend)
