# Data Retention Policy

## Overview

The Meeting Agent automatically purges old data to prevent unbounded database growth. Retention is configured via environment variables or `config/main.yaml`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `CHAT_MESSAGE_RETENTION_DAYS` | 180 | Chat messages from inactive sessions older than this are deleted |
| `DECAY_STATE_RETENTION_DAYS` | 365 | Fully-decayed memory states older than this are removed |
| `MEMORY_TTL_DAYS` | 90 | Memories past their TTL are expired (existing feature) |

## What gets purged

### Chat messages (daily)

- Messages belonging to sessions with no activity in the last `CHAT_MESSAGE_RETENTION_DAYS` days
- Empty sessions left after message deletion are also removed
- FTS5 index entries are cascade-deleted

### Decay state (daily)

- Memory decay entries where `current_score < 0.1` (effectively dead)
- Only if `last_accessed` is older than `DECAY_STATE_RETENTION_DAYS`

### Expired memories (hourly, existing)

- Memories past their `MEMORY_TTL_DAYS` are already cleaned up by the existing purge loop
- Both SQLite rows and Chroma vectors are removed

## What is NOT purged

- **Meetings** — meeting records and transcripts are kept indefinitely
- **Uploads** — original files in `data/uploads/` are not deleted
- **Active memories** — memories with decay score >= 0.1 are retained
- **Knowledge graph entities** — entities and relations are not purged

## Disabling retention

Set the retention days to `0` to disable:

```bash
CHAT_MESSAGE_RETENTION_DAYS=0
DECAY_STATE_RETENTION_DAYS=0
```

## Manual purge

To trigger an immediate purge outside the scheduled window:

```python
from src.services.retention import purge_old_chat_messages, purge_old_decay_state

purge_old_chat_messages()
purge_old_decay_state()
```
