#!/bin/sh
set -eu
umask 077

if [ "$(id -u)" = "0" ]; then
  # Compose grants only CHOWN/SETUID/SETGID for this bootstrap step. Do not
  # hide failures: an unwritable mount would otherwise surface later as a
  # vague SQLite or Chroma error.
  ownership_sentinel=/app/data/.ownership-appuser-v1
  if [ ! -f "$ownership_sentinel" ]; then
    chown -R appuser:appuser /app/data
    # CHOWN does not grant root permission to write an appuser-owned 0755
    # directory after capabilities are dropped. Create the marker as its owner.
    gosu appuser touch "$ownership_sentinel"
  fi
  exec gosu appuser "$@"
fi

# Kubernetes normally supplies runAsUser/fsGroup, so no transition is needed
# when the image already starts as the application user.
exec "$@"
