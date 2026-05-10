# SQLite Backup Guide

## Option 1: SQLite `.backup` command (recommended for manual backups)

```bash
# Create a consistent backup while the app is running
sqlite3 data/meetings.db ".backup data/meetings.db.bak"

# Verify backup integrity
sqlite3 data/meetings.db.bak "PRAGMA integrity_check;"
```

The `.backup` command creates a transactionally-consistent copy without locking the database. It works concurrently with WAL-mode reads and writes.

## Option 2: Litestream (recommended for continuous backup)

[Litestream](https://litestream.io/) replicates SQLite WAL changes to S3/GCS/Azure in near-real-time.

```bash
# Install
go install github.com/benbjohnson/litestream@latest

# Configure (litestream.yml)
dbs:
  - path: data/meetings.db
    replicas:
      - url: s3://my-bucket/meeting-agent/db
        retention: 72h
        snapshot-interval: 1h

# Run alongside the app
litestream replicate
```

## Option 3: File copy (stop the app first)

```bash
# Only safe when the app is stopped
systemctl stop meeting-agent
cp data/meetings.db data/meetings.db.bak
cp data/meetings.db-wal data/meetings.db-wal.bak 2>/dev/null || true
cp data/meetings.db-shm data/meetings.db-shm.bak 2>/dev/null || true
systemctl start meeting-agent
```

## Scheduling

```bash
# Cron: backup every 6 hours
0 */6 * * * sqlite3 /app/data/meetings.db ".backup /backups/meetings-$(date +\%Y\%m\%d-\%H\%M).db"
```

## What to back up

| Path | Contents |
|------|----------|
| `data/meetings.db` | All metadata, chat history, memories, knowledge graph |
| `data/chroma/` | Vector embeddings (can be rebuilt from DB via `/api/v1/settings/rebuild-vectors`) |
| `data/uploads/` | Original uploaded files |
| `config/main.yaml` | Non-secret configuration |
| `.env` | Secrets (ensure this is encrypted at rest) |
