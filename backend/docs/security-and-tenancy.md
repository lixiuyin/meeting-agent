# Security, Authentication, and Data Isolation

This document explains the implemented authentication, principals, resource authorization, short-lived tokens, idempotency-payload protection, request-layer security, and development-mode boundaries. It differs from the vulnerability-reporting policy in [`SECURITY.md`](../../SECURITY.md): this is an implementation reference, not a security guarantee or compliance claim.

## 1. Trust boundary

Meeting Agent is normally composed of one process, one SQLite database, a filesystem upload directory, and persistent Chroma storage. The API layer performs authentication and ownership filtering; the SQLite database, vector store, uploaded files, and logs must be protected by the deployment account. Anyone who can read the host filesystem can bypass HTTP authorization, so API-level multi-tenancy cannot replace operating-system, container-volume, and backup access controls.

The core boundary is:

```text
client
  → frontend Basic Auth (production browser entry)
  → reverse proxy X-API-Key / short-lived token
  → verify_api_key → principal.user_id
  → router ownership filter
  → SQLite / vector / file access
```

## 2. API keys and principals

### 2.1 Production and staging

When `API_KEY` is configured, protected routes require `X-API-Key` and compare
it in constant time. The key is not stored directly in the database. By
default, `HMAC-SHA256(PRINCIPAL_PEPPER, api_key)` derives an `api_<24 hex
chars>` principal. Non-dev environments must set `PRINCIPAL_PEPPER`; keep it
separate from the API key and rotate it as a secret. The current configuration
accepts one shared proxy key, so this principal represents one trusted
deployment, not separate end-user accounts.

The same API key maps to the same principal while the pepper is unchanged.
Rotating the key or pepper changes the derived identity and can make existing
ownership data appear to belong to another principal. `PRINCIPAL_ID` can pin
the configured credential to a known existing owner so key rotation preserves
ownership. Copy that owner ID from the authoritative database; inventing a new
ID does not migrate data. The setting accepts 8–128 alphanumeric,
underscore, or hyphen characters and rejects `default` and `dev_*` production
identities. A pinned ID still authenticates only the one configured API key and
is not an identity provider.

### 2.2 Development mode

When `API_KEY` is empty, the service enters local development semantics: `verify_api_key()` returns the legacy `default` principal, and ownership filtering is normally skipped for dev users, making data effectively shared. This mode is intended for single-user development, is not tenant isolation, and must not be exposed publicly.

The production frontend requires `FRONTEND_AUTH_USER` and an Apache-compatible
`FRONTEND_AUTH_PASSWORD_HASH`. Nginx authenticates callers before injecting the
shared backend API key. Generate the hash with `openssl passwd -apr1`. For
larger or multi-user deployments, put an OIDC/authentication gateway in front
of the service; Basic Auth still represents one deployment identity.

Non-dev configuration also requires explicit `CORS_ORIGINS`; production rejects a CORS wildcard. See [`configuration.md`](./configuration.md) for the complete defaults.

## 3. Resource authorization

With API-key authentication enabled, meeting, file, session, memory, and knowledge-graph operations apply principal ownership conditions. A `user_id` in a request body cannot override the authenticated principal. File downloads also check meeting/file ownership before issuing a signed URL.

Because `default` is shared in development mode, do not use a dev database to validate production isolation. The current single-key configuration can validate authenticated-versus-unauthenticated behavior and ownership enforcement, but genuine cross-user isolation requires an identity provider or a key registry that can issue multiple principals. `PRINCIPAL_ID` preserves one deployment owner across credential rotation; it does not change this limitation.

## 4. Short-lived file and WebSocket tokens

### 4.1 File tokens

- `POST /api/v1/meetings/file-token` issues a five-minute global assets token.
- `POST /api/v1/meetings/{meeting_id}/files/{file_id}/signed-url` issues a per-file token bound to `user_id + meeting_id + file_id + expiry`.
- Tokens are HMAC-SHA256 signatures, not database sessions; expired tokens are rejected by the server.
- A global token cannot be used for a per-file endpoint; media elements should use a scoped signed URL.
- Programmatic clients can send `X-API-Key` directly instead of putting the key in a URL.

In dev mode, the signing key is random for the lifetime of the process, so old tokens expire when the process restarts. In production, the signing key is derived from `API_KEY`.

### 4.2 WebSocket tokens

`POST /api/v1/ws/token` is designed to issue a five-minute token for
`/api/v1/ws?client_id=...&token=...`. Connections also have a one-hour absolute lifetime, idle ping/pong handling, and disconnection after two unanswered pings. The legacy `api_key` query parameter is still accepted but deprecated.

The HTTP token endpoint and token-only WebSocket handshake derive the same principal as `X-API-Key` authentication. Keep an end-to-end token handshake in deployment acceptance tests. Do not spread API-key query parameters to new clients.

## 5. Idempotency and sensitive payloads

Supported mutating requests may send `Idempotency-Key`. The lookup key includes the key, HTTP method, path, principal, and SHA-256 hash of the request body; successful responses are cached for 24 hours. A settings/model epoch change turns an old cache entry into a miss, preventing stale replay after a model or embedding change.

Idempotency response bodies are AES-GCM encrypted in the database, with the key derived from `API_KEY` through HKDF. `IDEMPOTENCY_OLD_KEYS` is used only to decrypt historical payloads during key rotation. In dev without an API key, a process-stable random fallback key is used; old idempotency payloads are not guaranteed to survive a restart. Expired records are deleted by the background cleanup loop.

Lifecycle state is stored separately from the encrypted response. Completed
responses and abandoned reservations can therefore expire even after a key
rotation. A transaction whose side effects committed without a replay response
remains fail-closed for a seven-day recovery window; after that bounded window
its tombstone can be purged instead of blocking the key forever.

## 6. HTTP security controls

- Request IDs are sanitized and length-limited before entering logs.
- `TRUSTED_HOSTS` enables Starlette TrustedHostMiddleware to block untrusted Host headers.
- `TRUSTED_PROXIES` controls when `X-Forwarded-For` is trusted; without it, arbitrary forwarded headers are not treated as the client IP.
- slowapi applies a default `60/minute` limit. The key is the API key when authentication is enabled and the client IP otherwise; explicit routes can override it. Production cannot disable rate limiting through `DISABLE_RATE_LIMIT`.
- `SECURITY_HEADERS_ENABLED` enables HSTS, nosniff, X-Frame-Options, Referrer-Policy, CSP, and Permissions-Policy headers by default.
- All application log handlers redact Authorization values plus `token` and
  `api_key` query parameters, including Uvicorn WebSocket handshake records.
  Reverse-proxy access logs must apply equivalent redaction, and clients should
  still avoid API keys in URLs.
- RAG documents, web results, memories, entity context, prior-session summaries, and memory-maintenance inputs are treated as untrusted prompt data. Structural markup is escaped before insertion into tagged prompt sections, and system prompts instruct models not to execute embedded instructions. This reduces delimiter-breaking and indirect prompt-injection risk; it is not a claim that model-level prompt injection is fully solved.

## 7. Deployment checklist

Before going live, confirm:

- `ENVIRONMENT` is not dev, and `API_KEY`, `PRINCIPAL_PEPPER`, and explicit `CORS_ORIGINS` are set; if `PRINCIPAL_ID` is used, verify it matches the existing database owner before deployment;
- `FRONTEND_AUTH_USER` and `FRONTEND_AUTH_PASSWORD_HASH` are set, or a stronger external identity gateway terminates access before the frontend;
- `TRUSTED_HOSTS` and `TRUSTED_PROXIES` match the real reverse-proxy topology;
- `data/meetings.db`, `data/uploads/`, `data/vectordb/`, `.env`, logs, and backups are not exposed by the web server;
- `/metrics` uses a separate `PROMETHEUS_API_KEY` when independent rotation is needed, and its network source is restricted;
- TLS terminates at the reverse proxy and HSTS is enabled only after the HTTPS path is verified;
- API keys, peppers, and provider keys do not enter git, image layers, shell history, logs, or issues;
- cross-principal authorization, expired file tokens, idempotency replay, rate limiting, Host/CORS, and WebSocket handshake tests are run.

Known boundary: the current principal is either an irreversible API-key-derived
value or an explicitly pinned deployment identity, not an independent
account/role system. There is no organization, role, administrator, or
fine-grained ACL model. These capabilities require a separate identity and
authorization layer rather than only more documentation or another API key.
