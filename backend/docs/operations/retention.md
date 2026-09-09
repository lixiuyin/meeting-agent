# Data Retention Policy

## Overview

The Meeting Agent expires chat/decay data and archives inactive semantic recall. Fact history is preserved by recall maintenance and can grow; explicit deletion/account erasure is a separate operation. Retention is configured via environment variables or `config/main.yaml`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `CHAT_MESSAGE_RETENTION_DAYS` | 180 | Chat messages from inactive sessions older than this are deleted |
| `DECAY_STATE_RETENTION_DAYS` | 365 | Fully-decayed memory states older than this are removed |
| `MEMORY_TTL_DAYS` | 90 | General auto-extracted recall TTL; domain facts use business validity instead |

## What gets purged

### Chat messages (daily)

- Messages belonging to sessions with no activity in the last `CHAT_MESSAGE_RETENTION_DAYS` days
- Empty sessions left after message deletion are also removed
- FTS5 index entries are cascade-deleted
- Session-summary vector IDs are written to the durable deletion outbox in the
  same transaction before their SQLite rows are cascade-deleted

### Decay state (daily)

- Memory decay entries where `current_score < 0.1` (effectively dead)
- Only if `last_accessed` is older than `DECAY_STATE_RETENTION_DAYS`

### Expired memories (hourly, existing)

- Expired recall is marked with `archived_at` and reason `expired`; Chroma cleanup is queued.
- SQLite fact rows and immutable versions are preserved. Explicit expiration still excludes current active recall; business validity/history remains a separate contract.

### Stale low-importance memories (daily)

- Non-profile memories below `0.5` importance and untouched for at least 90 days
- Vector deletion jobs and recall archival are committed atomically; an outbox failure rolls archival back.
- The per-user active recall cap also archives excess rows; it never deletes fact history.
- Generated profiles are invalidated when source revisions, eligibility, or recall lifecycle change. Legacy profiles without provenance are excluded.

## Account erasure status

`DELETE /api/v1/settings/account` returns `202 Accepted` with an opaque
`deletion_batch_id`. The primary database erasure is already committed, while
external files and indexes may still be processing. Poll
`GET /api/v1/settings/account/deletions/{batch_id}` until `completed`. If it
reports `failed`, repair the external store and call
`POST /api/v1/settings/account/deletions/{batch_id}/retry`.

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
