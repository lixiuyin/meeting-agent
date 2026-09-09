#!/usr/bin/env bash
# Complete application-data backup with integrity verification.
#
# Usage:
#   ./scripts/backup.sh [DB_PATH] [BACKUP_DIR] [RETENTION_DAYS]
#
# Defaults:
#   DB_PATH        → data/meetings.db
#   BACKUP_DIR     → backups/
#   RETENTION_DAYS → 30
#   DATA_DIR       → directory containing meetings.db, uploads/, vectordb/

set -euo pipefail
umask 077

DB_PATH="${1:-data/meetings.db}"
BACKUP_DIR="${2:-backups}"
RETENTION_DAYS="${3:-30}"
DATA_DIR="${4:-$(dirname "$DB_PATH")}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: Database not found at $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

TS=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/meeting-agent-${TS}.tar.gz"
STAGING_DIR=$(mktemp -d "${BACKUP_DIR}/.meeting-agent-backup.XXXXXX")
MANIFEST_TMP=$(mktemp)
trap 'rm -rf "$STAGING_DIR"; rm -f "$MANIFEST_TMP"' EXIT

echo "Backing up $DATA_DIR → $BACKUP_FILE"

# The database backup is transactionally consistent, but uploads and vector
# indexes are separate stores. Refuse a known live local backend so this full
# archive represents one application-data generation.
if [[ "${MEETING_AGENT_ALLOW_LIVE_BACKUP:-0}" != "1" ]]; then
  PIDS=$(pgrep -f "uvicorn .*src.main:app" 2>/dev/null || true)
  if [[ -n "$PIDS" ]]; then
    echo "ERROR: Backend process is running (PIDs: $PIDS). Stop it before backup." >&2
    echo "MEETING_AGENT_ALLOW_LIVE_BACKUP=1 permits a DB-consistent but not full point-in-time snapshot." >&2
    exit 1
  fi
fi

# Checkpoint WAL before backup to ensure all committed data is in the main file.
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(FULL);" >/dev/null 2>&1 || true

# SQLite online backup (safe for concurrent reads)
sqlite3 "$DB_PATH" ".backup '${STAGING_DIR}/meetings.db'"

# Verify backup integrity
RESULT=$(sqlite3 "${STAGING_DIR}/meetings.db" "PRAGMA integrity_check;" 2>&1)
if [[ "$RESULT" != "ok" ]]; then
  echo "ERROR: Backup integrity check failed: $RESULT" >&2
  exit 1
fi

# Smoke-test: verify the backup contains data by counting core tables.
TABLE_COUNT=$(sqlite3 "${STAGING_DIR}/meetings.db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>&1)
if [[ "$TABLE_COUNT" -lt 1 ]]; then
  echo "ERROR: Backup appears empty (0 tables found)" >&2
  exit 1
fi

# Uploads are primary data and vectors are expensive derived state. Include
# both when present, plus a checksum manifest for restore-time verification.
for name in uploads vectordb skills; do
  if [[ -d "${DATA_DIR}/${name}" ]]; then
    cp -a "${DATA_DIR}/${name}" "$STAGING_DIR/"
  fi
done

(
  cd "$STAGING_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    find . -type f -exec sha256sum {} + > "$MANIFEST_TMP"
  else
    find . -type f -exec shasum -a 256 {} + > "$MANIFEST_TMP"
  fi
)
mv "$MANIFEST_TMP" "${STAGING_DIR}/SHA256SUMS"

cat > "${STAGING_DIR}/BACKUP_INFO" <<EOF
format=meeting-agent-full-v1
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
database=meetings.db
EOF
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$STAGING_DIR" && sha256sum BACKUP_INFO >> SHA256SUMS)
else
  (cd "$STAGING_DIR" && shasum -a 256 BACKUP_INFO >> SHA256SUMS)
fi

tar -C "$STAGING_DIR" -czf "$BACKUP_FILE" .
chmod 600 "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

# Remove backups older than RETENTION_DAYS
DELETED=$(find "$BACKUP_DIR" -name "meeting-agent-*.tar.gz" -mtime +"${RETENTION_DAYS}" -delete -print | wc -l)
if [[ "$DELETED" -gt 0 ]]; then
  echo "Cleaned up ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi
