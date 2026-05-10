# Restore Guide

## From SQLite backup

```bash
# 1. Stop the application
systemctl stop meeting-agent

# 2. Verify backup integrity
sqlite3 /backups/meetings-20260414-1200.db "PRAGMA integrity_check;"

# 3. Replace the current database
cp data/meetings.db data/meetings.db.pre-restore  # keep current as safety
cp /backups/meetings-20260414-1200.db data/meetings.db

# 4. Remove WAL/SHM files (they will be recreated)
rm -f data/meetings.db-wal data/meetings.db-shm

# 5. Start the application
systemctl start meeting-agent

# 6. Verify
curl http://localhost:8000/api/v1/health/ready
```

## From Litestream

```bash
# Restore to a specific point in time
litestream restore -o data/meetings.db -timestamp 2026-04-14T12:00:00Z s3://my-bucket/meeting-agent/db

# Or restore the latest snapshot
litestream restore -o data/meetings.db s3://my-bucket/meeting-agent/db
```

## Vector store recovery

Vectors are stored in `data/chroma/`. If lost, they can be rebuilt:

```bash
# Trigger vector rebuild via API
curl -X POST http://localhost:8000/api/v1/settings/rebuild-vectors \
  -H "X-API-Key: your-key"
```

This re-indexes all meeting transcripts from the database into Chroma.

## Verification checklist

After restoring, verify:

1. `GET /api/v1/health/ready` returns `status: ok`
2. `GET /api/v1/meetings` lists expected meetings
3. `GET /api/v1/sessions` shows chat history
4. `GET /api/v1/memory` returns stored memories
5. Upload a test file and ask a question to confirm RAG works
