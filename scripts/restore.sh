#!/usr/bin/env bash
# SQLite database restore from a backup file.
#
# Companion to scripts/backup.sh. Verifies the backup, copies it to the target
# location, and validates the restored database passes integrity checks.
#
# Usage:
#   ./scripts/restore.sh <BACKUP_FILE> [TARGET_DB_PATH]
#
# Arguments:
#   BACKUP_FILE     Path to the backup .db file (required)
#   TARGET_DB_PATH  Where to restore the database (default: backend/data/meetings.db)
#
# Safety:
#   - Aborts if the backup file does not exist or is not a valid SQLite database.
#   - Creates a .bak copy of the current database before overwriting.
#   - Verifies the restored file passes PRAGMA integrity_check.

set -euo pipefail

BACKUP_FILE="${1:-}"
TARGET_DB="${2:-backend/data/meetings.db}"

# ── Validate arguments ──────────────────────────────────────────────────────

if [[ -z "$BACKUP_FILE" ]]; then
  echo "ERROR: Missing required argument BACKUP_FILE" >&2
  echo "Usage: $0 <BACKUP_FILE> [TARGET_DB_PATH]" >&2
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

# ── Verify backup is valid SQLite ───────────────────────────────────────────

if ! sqlite3 "$BACKUP_FILE" "SELECT 1;" &>/dev/null; then
  echo "ERROR: Backup file is not a valid SQLite database: $BACKUP_FILE" >&2
  exit 1
fi

BACKUP_CHECK=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" 2>&1)
if [[ "$BACKUP_CHECK" != "ok" ]]; then
  echo "ERROR: Backup file failed integrity check: $BACKUP_CHECK" >&2
  exit 1
fi

echo "Backup verified: $BACKUP_FILE"

# ── Warn about running processes ────────────────────────────────────────────
# Attempt to locate running backend processes that may hold the DB open.

PIDS=$(pgrep -f "uvicorn src.main:app" 2>/dev/null || true)
if [[ -n "$PIDS" ]]; then
  echo "WARNING: Running backend processes detected (PIDs: $PIDS)"
  echo "  Consider stopping them before restoring: kill $PIDS"
  read -rp "Continue anyway? [y/N] " CONFIRM
  if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# ── Preserve current database ───────────────────────────────────────────────

if [[ -f "$TARGET_DB" ]]; then
  BAK_FILE="${TARGET_DB}.bak.$(date +%Y%m%d-%H%M%S)"
  echo "Saving current database to $BAK_FILE"
  cp "$TARGET_DB" "$BAK_FILE"
fi

# ── Restore ─────────────────────────────────────────────────────────────────

TARGET_DIR=$(dirname "$TARGET_DB")
mkdir -p "$TARGET_DIR"

echo "Restoring $BACKUP_FILE → $TARGET_DB"
cp "$BACKUP_FILE" "$TARGET_DB"

# ── Verify restored database ────────────────────────────────────────────────

RESTORE_CHECK=$(sqlite3 "$TARGET_DB" "PRAGMA integrity_check;" 2>&1)
if [[ "$RESTORE_CHECK" != "ok" ]]; then
  echo "ERROR: Restored database failed integrity check: $RESTORE_CHECK" >&2
  # Attempt rollback
  if [[ -f "${BAK_FILE:-}" ]]; then
    echo "Rolling back to previous database..."
    cp "$BAK_FILE" "$TARGET_DB"
  fi
  exit 1
fi

SIZE=$(du -h "$TARGET_DB" | cut -f1)
echo "Restore complete: $TARGET_DB ($SIZE)"
echo "Verify application connectivity before deleting the .bak file."
