#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Preflight for `make up` / `make bootstrap` (FIX 5, Bug B).
#
# Verifies .env exists and holds the HARD-REQUIRED vars (the ones compose
# refuses to start without via ${VAR:?}) with real, non-placeholder values —
# BEFORE `docker compose up`. Turns the cryptic
#
#     "error while interpolating services.chat-edge-api.environment.REDIS_URL:
#      required variable REDIS_PASSWORD is missing a value"
#
# — the failure that once left a stack weeks-stale at an old schema — into an
# immediate, actionable message pointing at `make bootstrap`.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${1:-.env}"

# Vars compose hard-requires (${VAR:?}) or folds into a redis:// URL. Keep in
# sync with docker-compose*.yml. (SERVICE_JWT_SECRET / other :? prod vars have
# safe dev defaults in the base file, so only the truly required-everywhere
# ones are enforced here.)
REQUIRED_KEYS=(REDIS_PASSWORD)
PLACEHOLDER_RE='^changeme'

if [ ! -f "$ENV_FILE" ]; then
  cat >&2 <<EOF
[preflight] ✗ $ENV_FILE not found.
    The stack needs a .env with required secrets. Generate it:
        make bootstrap        # creates .env with strong secrets, then brings the stack up
    (or: bash scripts/gen_env.sh)
EOF
  exit 1
fi

problems=0
read_val() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'\'' ' || true; }

for key in "${REQUIRED_KEYS[@]}"; do
  val="$(read_val "$key")"
  if [ -z "$val" ]; then
    echo "[preflight] ✗ $key is missing or empty in $ENV_FILE" >&2
    problems=1
  elif echo "$val" | grep -qE "$PLACEHOLDER_RE"; then
    echo "[preflight] ✗ $key is still the shipped placeholder — set a real value" >&2
    problems=1
  fi
done

if [ "$problems" -ne 0 ]; then
  cat >&2 <<EOF

[preflight] Required env var(s) missing/placeholder — 'docker compose up' would fail cryptically.
    Fix automatically:
        make bootstrap        # backfills missing required vars into an existing .env
    or set by hand, e.g.:
        echo "REDIS_PASSWORD=\$(openssl rand -hex 32)" >> $ENV_FILE
EOF
  exit 1
fi

echo "[preflight] .env OK — required vars present (${REQUIRED_KEYS[*]})."
