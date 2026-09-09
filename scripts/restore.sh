#!/usr/bin/env bash
# Restore a complete meeting-agent backup archive produced by backup.sh.
#
# Usage: ./scripts/restore.sh <BACKUP.tar.gz> [TARGET_DATA_DIR] [--force]
# The backend must be stopped. --force only bypasses process detection for
# controlled automation; it does not make an online restore safe.

set -euo pipefail
umask 077

BACKUP_FILE="${1:-}"
TARGET_DATA_DIR="${2:-data}"
FORCE="${3:-}"

if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup archive not found: ${BACKUP_FILE:-<missing>}" >&2
  echo "Usage: $0 <BACKUP.tar.gz> [TARGET_DATA_DIR] [--force]" >&2
  exit 1
fi
if [[ "$FORCE" != "" && "$FORCE" != "--force" ]]; then
  echo "ERROR: Unknown option: $FORCE" >&2
  exit 1
fi
if ! tar -tzf "$BACKUP_FILE" >/dev/null 2>&1; then
  echo "ERROR: Backup is not a readable tar.gz archive: $BACKUP_FILE" >&2
  exit 1
fi

# Reject absolute paths, traversal, and links before extracting untrusted input.
if tar -tzf "$BACKUP_FILE" | awk '
  {
    name=$0; sub(/^\.\//, "", name)
    if (name ~ /^\// || name ~ /(^|\/)\.\.($|\/)/) bad=1
  }
  END { exit bad ? 0 : 1 }
'; then
  echo "ERROR: Backup contains an unsafe path" >&2
  exit 1
fi
if tar -tvzf "$BACKUP_FILE" | awk 'substr($1,1,1) ~ /[lh]/ { found=1 } END { exit found ? 0 : 1 }'; then
  echo "ERROR: Backup contains symbolic or hard links" >&2
  exit 1
fi

if [[ "$FORCE" != "--force" ]]; then
  PIDS=$(pgrep -f "uvicorn .*src.main:app" 2>/dev/null || true)
  if [[ -n "$PIDS" ]]; then
    echo "ERROR: Backend process is running (PIDs: $PIDS). Stop it before restore." >&2
    exit 1
  fi
fi

TARGET_PARENT=$(dirname "$TARGET_DATA_DIR")
TARGET_NAME=$(basename "$TARGET_DATA_DIR")
mkdir -p "$TARGET_PARENT"
STAGING_DIR=$(mktemp -d "${TARGET_PARENT}/.${TARGET_NAME}.restore.XXXXXX")
ROLLBACK_DIR="${TARGET_DATA_DIR}.pre-restore.$(date +%Y%m%d-%H%M%S)"
trap 'if [[ -d "${STAGING_DIR:-}" ]]; then rm -rf "$STAGING_DIR"; fi' EXIT

tar -xzf "$BACKUP_FILE" -C "$STAGING_DIR" --no-same-owner --no-same-permissions

if [[ ! -f "$STAGING_DIR/meetings.db" || ! -f "$STAGING_DIR/SHA256SUMS" || ! -f "$STAGING_DIR/BACKUP_INFO" ]]; then
  echo "ERROR: Backup is missing meetings.db, SHA256SUMS, or BACKUP_INFO" >&2
  exit 1
fi
if ! grep -qx 'format=meeting-agent-full-v1' "$STAGING_DIR/BACKUP_INFO"; then
  echo "ERROR: Unsupported backup format" >&2
  exit 1
fi

(
  cd "$STAGING_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS
  else
    while IFS= read -r line; do
      expected=${line%% *}
      file=${line#*  }
      actual=$(shasum -a 256 "$file" | awk '{print $1}')
      [[ "$actual" == "$expected" ]] || {
        echo "Checksum mismatch: $file" >&2
        exit 1
      }
    done < SHA256SUMS
  fi
)

INTEGRITY=$(sqlite3 "$STAGING_DIR/meetings.db" "PRAGMA integrity_check;" 2>&1)
if [[ "$INTEGRITY" != "ok" ]]; then
  echo "ERROR: Restored database failed integrity_check: $INTEGRITY" >&2
  exit 1
fi
FK_ERRORS=$(sqlite3 "$STAGING_DIR/meetings.db" "PRAGMA foreign_key_check;" 2>&1)
if [[ -n "$FK_ERRORS" ]]; then
  echo "ERROR: Restored database failed foreign_key_check: $FK_ERRORS" >&2
  exit 1
fi
rm -f "$STAGING_DIR/meetings.db-wal" "$STAGING_DIR/meetings.db-shm"

if [[ -e "$TARGET_DATA_DIR" ]]; then
  mv "$TARGET_DATA_DIR" "$ROLLBACK_DIR"
  echo "Previous data preserved at $ROLLBACK_DIR"
fi
if ! mv "$STAGING_DIR" "$TARGET_DATA_DIR"; then
  if [[ -d "$ROLLBACK_DIR" && ! -e "$TARGET_DATA_DIR" ]]; then
    mv "$ROLLBACK_DIR" "$TARGET_DATA_DIR"
  fi
  echo "ERROR: Atomic data-directory swap failed; rollback attempted" >&2
  exit 1
fi

echo "Restore complete: $TARGET_DATA_DIR"
echo "Start the backend and verify http://localhost:7008/api/v1/health/ready"
