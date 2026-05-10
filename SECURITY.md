# Security Policy

## Reporting a Vulnerability

We take security bugs seriously. Thank you for improving the project.

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them via one of these channels:

- **GitHub Security Advisories**: Use the [private vulnerability reporting](../../security/advisories/new) feature on this repository.
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
