# ChromaDB advisory disposition

Last reviewed: 2026-09-08

The locked ChromaDB 1.5.9 package is reported by `pip-audit` with
`PYSEC-2026-311` / `CVE-2026-45829`, `CVE-2026-45830`, `CVE-2026-45831`, and
`CVE-2026-45833`. No fix version is currently published in the audit data.

These findings affect Chroma's HTTP collection endpoints, server-side tenant
authorization, or model loading with `trust_remote_code`. Meeting Agent does
not run a Chroma HTTP server or expose Chroma credentials. It constructs only
an embedded `PersistentClient`; application API authorization and authoritative
scope filtering happen before vector access. Startup rejects remote Chroma
clients and `trust_remote_code`, confines production persistence beneath the
private data directory, and disables Chroma telemetry.

This is a constrained mitigation, not a claim that the third-party package is
vulnerability-free. `make security-audit` fails if:

- a new dependency advisory appears;
- any reviewed Chroma advisory gains a fix version, requiring an upgrade;
- application source introduces a Chroma HTTP/server client; or
- application source enables `trust_remote_code`.

The package must be upgraded and this disposition revisited as soon as an
upstream fixed release is available.
