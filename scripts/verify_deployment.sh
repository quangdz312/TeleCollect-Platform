#!/usr/bin/env bash
# Read-only smoke test for the production stack (docker-compose.prod.yml).
# Run from /srv/telecollect/app on the VPS after `up -d`, or locally against
# docker-compose.local.yml with BASE_URL=http://localhost:8080.
#
# Never runs `down`, `down -v`, `restart`, or anything that touches data.
# Exits non-zero on the first failed check so it's safe to use as a CI/manual
# gate before handing the URL to BTC.

set -euo pipefail

BASE_URL="${BASE_URL:-https://${PROJECT_DOMAIN:-}}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-/srv/telecollect/secrets/.env.production}"

if [ -z "${PROJECT_DOMAIN:-}" ] && [ "${BASE_URL}" = "https://" ]; then
  echo "Set BASE_URL (e.g. http://localhost:8080) or PROJECT_DOMAIN before running this script." >&2
  exit 2
fi

compose() {
  if [ -f "$ENV_FILE" ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

fail=0
check() {
  local desc="$1"; shift
  if "$@"; then
    echo "PASS  $desc"
  else
    echo "FAIL  $desc"
    fail=1
  fi
}

echo "== verify_deployment.sh against ${BASE_URL} (compose file: ${COMPOSE_FILE}) =="

check "containers are up" bash -c "compose ps --format '{{.State}}' 2>/dev/null | grep -qv exited"
check "frontend GET / returns 2xx" curl -fsS -o /dev/null "${BASE_URL}/"
check "backend GET /health returns 2xx" curl -fsS -o /dev/null "${BASE_URL}/health"
check "REST API reachable (GET /api/v1/tasks, expect 401 without token, not 404/502)" \
  bash -c "code=\$(curl -s -o /dev/null -w '%{http_code}' '${BASE_URL}/api/v1/tasks'); [ \"\$code\" = '401' ]"

echo
echo "Not automated here, verify manually:"
echo "  - HTTPS certificate is valid (browser padlock, or: curl -vI ${BASE_URL}/ 2>&1 | grep -i 'SSL certificate')"
echo "  - WSS handshake: open the app, log in, go to Collect, click Connect, confirm no console errors"
echo "  - docker compose -f ${COMPOSE_FILE} ps  ->  only caddy publishes 80/443"
echo "  - Persistence: create a user/episode, 'docker compose -f ${COMPOSE_FILE} restart backend', confirm it's still there"

exit $fail
