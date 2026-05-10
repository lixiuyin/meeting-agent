#!/usr/bin/env bash
# SQLite database backup with integrity verification.
#
# Usage:
#   ./scripts/backup.sh [DB_PATH] [BACKUP_DIR] [RETENTION_DAYS]
#
# Defaults:
#   DB_PATH        → backend/data/meetings.db
#   BACKUP_DIR     → backups/
#   RETENTION_DAYS → 30

set -euo pipefail

DB_PATH="${1:-backend/data/meetings.db}"
BACKUP_DIR="${2:-backups}"
RETENTION_DAYS="${3:-30}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: Database not found at $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

TS=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/meetings-${TS}.db"

echo "Backing up $DB_PATH → $BACKUP_FILE"

# Checkpoint WAL before backup to ensure all committed data is in the main file.
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(FULL);" 2>/dev/null || true

# SQLite online backup (safe for concurrent reads)
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"

# Verify backup integrity
RESULT=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" 2>&1)
if [[ "$RESULT" != "ok" ]]; then
  echo "ERROR: Backup integrity check failed: $RESULT" >&2
  rm -f "$BACKUP_FILE"
  exit 1
fi

# Smoke-test: verify the backup contains data by counting core tables.
TABLE_COUNT=$(sqlite3 "$BACKUP_FILE" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>&1)
if [[ "$TABLE_COUNT" -lt 1 ]]; then
  echo "ERROR: Backup appears empty (0 tables found)" >&2
  rm -f "$BACKUP_FILE"
  exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

# Remove backups older than RETENTION_DAYS
DELETED=$(find "$BACKUP_DIR" -name "meetings-*.db" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
if [[ "$DELETED" -gt 0 ]]; then
  echo "Cleaned up ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi
