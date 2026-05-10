#!/bin/sh
set -e

if [ -z "$BACKEND_API_KEY" ]; then
  echo "ERROR: BACKEND_API_KEY is required but not set." >&2
  echo "Set it via: docker run -e BACKEND_API_KEY=... or in your deployment config." >&2
  exit 1
fi

exec "$@"
