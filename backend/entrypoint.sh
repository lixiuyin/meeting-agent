#!/bin/sh
# Fix ownership of the data directory when using a bind mount.
# This allows appuser to write to host-mounted ./data regardless of host UID.
chown -R appuser:appuser /app/data 2>/dev/null || true
exec gosu appuser "$@"
