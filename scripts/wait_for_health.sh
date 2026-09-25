#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Health-gate the dev stack (Phase 15). Replaces `make fresh`'s bare
# `sleep 10` with an ACTIVE readiness poll of every critical service, so
# bootstrap only proceeds (migrate/seed) once the DB + cache + app are up.
#
# Read-only + safe to run against an already-running stack — use it to prove
# the poll logic on a live box:
#   bash scripts/wait_for_health.sh
#
# Tunables: HEALTH_TIMEOUT (seconds per service, default 120).
# ─────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TIMEOUT="${HEALTH_TIMEOUT:-120}"

poll() {
  local name="$1"; shift
  local deadline=$(( $(date +%s) + TIMEOUT ))
  printf '[health] %-34s' "$name"
  until "$@" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "TIMEOUT (${TIMEOUT}s)"
      echo "[health] FAILED: '$name' never became ready. Inspect: docker compose ps / logs." >&2
      return 1
    fi
    printf '.'
    sleep 2
  done
  echo " OK"
}

# Postgres: reached over the compose network (no host port) → exec pg_isready.
chk_postgres() { docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" -q; }
# Redis: AUTH is on (--requirepass). Reference $REDIS_PASSWORD INSIDE the
# container (single-quoted) so the secret never lands on the host cmdline.
chk_redis()    { docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping' 2>/dev/null | grep -q PONG; }
# Backend (Python) is published on host :8081; it serves /health.
chk_backend()  { curl -fsS http://localhost:8081/health; }
# Go gateway is the public entrypoint on :8080; chi serves GET /__gw/health.
chk_gateway()  { curl -fsS http://127.0.0.1:28080/__gw/health; }

rc=0
poll "postgres (pg_isready)"          chk_postgres || rc=1
poll "redis (redis-cli ping)"         chk_redis    || rc=1
poll "backend (:8081/health)"         chk_backend  || rc=1
poll "gateway (:8080/__gw/health)"    chk_gateway  || rc=1

if [ "$rc" -ne 0 ]; then
  echo "[health] One or more critical services are unhealthy — aborting." >&2
  exit 1
fi
echo "[health] All critical services ready."
