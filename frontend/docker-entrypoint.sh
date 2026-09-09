#!/bin/sh
# This file is sourced by nginx's parent entrypoint. Keep errexit but do not
# enable nounset globally for later upstream hooks.
set -e

# Keep API_KEY in ENVIRON even when development intentionally uses an empty
# value.  nginx's envsubst hook only replaces variables present in ENVIRON;
# leaving it unset would preserve a literal ${API_KEY} in the rendered config.
API_KEY=${API_KEY:-}
export API_KEY

if [ -z "${API_KEY:-}" ]; then
  case "${ENVIRONMENT:-dev}" in
    dev|development|test|testing|"")
      echo "WARN: API_KEY is empty; frontend proxy authentication is disabled for development." >&2
      ;;
    *)
      echo "ERROR: API_KEY is required for non-development frontend deployments." >&2
      exit 1
      ;;
  esac
fi

case "${ENVIRONMENT:-dev}" in
  dev|development|test|testing|"")
    export FRONTEND_AUTH_MODE=off
    printf 'disabled:!\n' > /tmp/meeting-agent.htpasswd
    ;;
  *)
    if [ -z "${FRONTEND_AUTH_USER:-}" ] || [ -z "${FRONTEND_AUTH_PASSWORD_HASH:-}" ]; then
      echo "ERROR: FRONTEND_AUTH_USER and FRONTEND_AUTH_PASSWORD_HASH are required in production." >&2
      exit 1
    fi
    if printf '%s' "$FRONTEND_AUTH_USER" | grep -q ':'; then
      echo "ERROR: FRONTEND_AUTH_USER must not contain a colon." >&2
      exit 1
    fi
    export FRONTEND_AUTH_MODE='"Meeting Agent"'
    printf '%s:%s\n' "$FRONTEND_AUTH_USER" "$FRONTEND_AUTH_PASSWORD_HASH" \
      > /tmp/meeting-agent.htpasswd
    chmod 600 /tmp/meeting-agent.htpasswd
    ;;
esac
