# ADR-005: Enforce MCP and REST user-identity parity

## Status
Accepted

## Context
REST endpoints authenticate with `X-API-Key` and derive principal identity server-side. Some skill
invocation paths still accepted caller-provided `user_id`, creating parity drift and potential
impersonation ambiguity between interfaces.

## Decision
Unify identity handling for skill invocation around authenticated principal identity:
1. REST `/api/v1/skills/invoke` ignores request `user_id` and always uses principal user id.
2. Server logs user-id override attempts as security-relevant warnings.
3. Add explicit regression test coverage to prevent future reintroduction.

## Consequences
1. Cross-interface behavior is deterministic and auditable.
2. Client-provided identity can no longer override authenticated context.
3. MCP service-layer parity work can build on this without changing REST contracts again.

