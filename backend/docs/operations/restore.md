# Complete Restore Guide

Restore accepts the `.tar.gz` archive produced by `scripts/backup.sh`. It
rejects path traversal and archive links, verifies every SHA256 checksum,
runs SQLite integrity and foreign-key checks, and swaps the complete data
directory atomically. Restored files are created under a private process umask
so database and user material are not made group/world-readable.

```bash
docker compose stop backend frontend
./scripts/restore.sh backups/meeting-agent-20260903-120000.tar.gz data
docker compose start backend frontend
curl -fsS http://localhost:7008/api/v1/health/ready
```

If `data/` already exists, it is moved to a timestamped
`data.pre-restore.*` rollback directory. Do not delete that directory until
meetings, sessions, uploads, and RAG retrieval have been verified.

For non-interactive CI, the final argument may be `--force`; this bypasses
local process detection only and does not make an online restore safe.

After startup verify:

1. readiness returns HTTP 200;
2. expected meetings and sessions are present;
3. an uploaded file can be opened;
4. scoped retrieval returns citations;
5. `PRAGMA foreign_key_check` remains empty.
