# Complete Backup Guide

The supported backup unit is the complete application data generation:

- `meetings.db` — metadata, chats, memories, and knowledge graph;
- `uploads/` — original user files;
- `vectordb/` — derived Chroma indexes;
- `SHA256SUMS` and `BACKUP_INFO` — integrity and format metadata.

Stop or quiesce the backend before taking a full backup. SQLite `.backup` is
safe during writes, but SQLite, uploads, and Chroma cannot otherwise be
guaranteed to represent the same point in time.

```bash
docker compose stop backend
./scripts/backup.sh data/meetings.db backups 30 data
docker compose start backend
```

The result is `backups/meeting-agent-YYYYmmdd-HHMMSS.tar.gz`. The script checks
SQLite integrity before packaging and retains archives for 30 days by default.
Archives are created with mode `600`, and the default root `backups/` directory
is ignored by Git because it contains user data and may be very large. Supplying
a custom backup directory makes the operator responsible for equivalent access
control and source-control exclusion.
The archive contains uploads, vector stores, and persistent custom Skills from
`data/skills/`; built-in Skills remain part of the application image.
It refuses a detected local Uvicorn process. `MEETING_AGENT_ALLOW_LIVE_BACKUP=1`
is an explicit escape hatch that guarantees only database consistency, not a
full application point-in-time snapshot.

Keep `config/main.yaml` separately. Keep `.env` and other secrets in an
encrypted secret manager rather than inside the application-data archive.

For continuous off-host SQLite replication, Litestream can complement these
full snapshots, but it does not replace backups of uploads.
