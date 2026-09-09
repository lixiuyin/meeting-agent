# Security Policy

## Reporting a Vulnerability

We take security bugs seriously. Thank you for improving the project.

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them via one of these channels:

- **GitHub Security Advisories**: Use the [private vulnerability reporting](https://github.com/lixiuyin/meeting-agent/security/advisories/new) feature on this repository.
- **Email**: Send details to the maintainer listed in the repository's CODEOWNERS file.

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce or a proof of concept
- Affected versions, if known
- Any suggested mitigations

We will acknowledge your report within **48 hours** and aim to send a detailed response within **5 business days**.

## Triage Policy

| Severity | Definition | Target Response Time |
|----------|-----------|---------------------|
| CRITICAL | Remote code execution, data exfiltration, auth bypass | Patch within **24 hours** |
| HIGH | Privilege escalation, significant data exposure | Patch within **7 days** |
| MEDIUM | Limited impact, requires specific conditions | Patch in **next release** |
| LOW | Informational, minor misconfiguration | Best-effort fix |

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main` branch | Yes |
| Latest release | Yes |
| Previous release | Best-effort |
| Older releases | No |

## Security Update Policy

- Security patches are released as patch version bumps (e.g., v1.2.3 to v1.2.4).
- Critical and high-severity fixes are backported to the latest release branch.
- Security advisories are published on GitHub with affected version ranges and upgrade instructions.
- Dependencies are scanned weekly via CI (Trivy, pip-audit, npm audit). High and critical CVEs in direct dependencies are patched within the timelines above.

## Security Features

- API key authentication via `X-API-Key` header (timing-attack-resistant comparison)
- File upload sanitization (path traversal prevention, size limits)
- Signed file download URLs (HMAC-SHA256, 5-minute TTL)
- CORS hardening (fail-closed in production)
- Rate limiting on all mutating endpoints
- Error responses never expose internal details
- Pre-commit hooks include `gitleaks` and `detect-secrets` for credential scanning
- Chroma is embedded through a local `PersistentClient`; production startup
  rejects remote clients, `trust_remote_code`, and vector paths outside `DATA_DIR`.

## Temporary dependency mitigations

The dependency audit has narrowly scoped ignores for advisories that are not
reachable in this deployment while upstream compatibility catches up:

- Chroma `PYSEC-2026-311` and `CVE-2026-45830/45831/45833` affect the Chroma
  HTTP server, model-repository loading, or its multi-tenant RBAC provider.
  Meeting Agent only constructs an embedded `PersistentClient`; it does not
  start or expose the Chroma server.
- The default `production` dependency group now requires Starlette 1.6 or
  newer; its five prior advisory exceptions have been removed. This group is
  enabled by default in both development and production `uv sync`/`uv export`.
  The incompatible `multimodal` extra requires an explicitly separate
  development environment (`--no-group production --extra multimodal`) and
  remains prohibited outside development.

These ignores name exact advisory IDs, remain blocking for every other finding,
expire in CI on **2026-10-01**, and must be reviewed before that date by the
repository maintainers.  A review must either upgrade the dependency or renew
the deadline here and in `.github/workflows/security.yml` with updated evidence.
They must be removed as soon as compatible patched releases are available.

Last local review: **2026-09-08**. The installed Chroma 1.5.9 audit still reports
the four exact exceptions above without a listed fixed version. Runtime policy
tests continue to reject remote Chroma, remote-code loading, and production
storage outside DATA_DIR. The upstream [Python server authorization report](https://github.com/chroma-core/chroma/issues/7588)
remains open; this review does not authorize a server deployment or extend the
2026-10-01 deadline. Embedded-only mitigation is not a claim of zero dependency risk.

The optional `multimodal` extra currently inherits additional advisories from
RAGAnything's pinned MinerU/LightRAG stack. The application therefore refuses
to start with `RAGANYTHING_ENABLED=true` outside development. Native and hybrid
retrieval remain supported in production; remove this guard only after the
upstream dependency graph can resolve to patched Gradio, LightRAG and
Transformers versions.
