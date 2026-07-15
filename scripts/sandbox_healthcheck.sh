#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Post-`make sandbox-up` health check (Phase 15).
#
# Confirms chat-edge is up AND that run_python is ACTIVE (not the disabled
# 503 path). The /code/run endpoint is auth-gated + rate-limited, so an
# unauthenticated probe can't reach the 503-vs-200 branch; the authoritative
# signal is chat-edge's startup log line, which reflects SANDBOX_ENABLED
# directly:
#     "run_python code interpreter active"   → enabled  (good)
#     "SANDBOX_ENABLED=0 ... DISABLED"       → still off (overlay not applied)
#
# Usage: bash scripts/sandbox_healthcheck.sh [compose-project] [chat-edge-service]
# ─────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${1:-ai-gateway}"
SVC="${2:-chat-edge-api}"
TIMEOUT="${HEALTH_TIMEOUT:-90}"

DC="docker compose -p $PROJECT -f docker-compose.webchat.yml -f docker-compose.sandbox.yml"

# 1. chat-edge serves GET /health at the router root (host :4000).
deadline=$(( $(date +%s) + TIMEOUT ))
printf '[sandbox] waiting for chat-edge (:4000/health)'
until curl -fsS http://localhost:4000/health >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo " TIMEOUT (${TIMEOUT}s)"
    echo "[sandbox] FAILED: chat-edge did not become healthy. Check: $DC logs $SVC" >&2
    exit 1
  fi
  printf '.'; sleep 2
done
echo " OK"

# 2. Confirm run_python is ACTIVE via the startup log line.
LOGS="$($DC logs --tail 200 "$SVC" 2>/dev/null || true)"
if echo "$LOGS" | grep -q "run_python code interpreter active"; then
  echo "[sandbox] run_python code interpreter is ACTIVE (SANDBOX_ENABLED=1). ✔"
elif echo "$LOGS" | grep -qi "SANDBOX_ENABLED=0"; then
  echo "[sandbox] WARNING: chat-edge reports SANDBOX_ENABLED=0 — the overlay did NOT take effect." >&2
  echo "[sandbox]          Confirm the container was recreated with docker-compose.sandbox.yml." >&2
  exit 1
else
  echo "[sandbox] NOTE: could not find the sandbox status line in recent logs."
  echo "[sandbox]       Verify manually: $DC logs $SVC | grep -i run_python"
fi

echo ""
echo "[sandbox] Endpoint contract: POST /api/v1/code/run (auth + rate-limited)."
echo "[sandbox] A disabled server answers 503 'code interpreter is disabled';"
echo "[sandbox] with the overlay active it executes model code in the isolated sandbox."
