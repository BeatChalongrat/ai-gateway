#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Idempotent .env generator for `make bootstrap` (Phase 15 ops hardening).
#
# Copies .env.example → .env (once) and replaces the shipped "changeme-*"
# placeholders with strong CSPRNG values so `docker compose up` — which hard-
# requires ${REDIS_PASSWORD:?} — works on a clean checkout without hand-editing.
#
# IDEMPOTENT: if the target .env already exists it is left 100% untouched.
#
# Usage:
#   bash scripts/gen_env.sh            # writes ./.env
#   bash scripts/gen_env.sh /path/.env # writes an alternate file (isolation test)
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${1:-.env}"
EXAMPLE="${ENV_EXAMPLE:-.env.example}"

if [ -f "$ENV_FILE" ]; then
  echo "[gen-env] $ENV_FILE already exists — leaving it untouched (idempotent)."
  exit 0
fi
if [ ! -f "$EXAMPLE" ]; then
  echo "[gen-env] ERROR: $EXAMPLE not found." >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "[gen-env] ERROR: openssl not found on PATH (needed to generate secrets)." >&2
  exit 1
fi

cp "$EXAMPLE" "$ENV_FILE"

# hex (not base64): the value is folded into redis:// URLs by the rate-limiter
# and chat-edge (redis://:${REDIS_PASSWORD}@redis:6379). base64's '/', '+', '='
# would corrupt that URL — hex is URL-safe.
REDIS_PASSWORD_VAL="$(openssl rand -hex 32)"
SESSION_SIGNING_VAL="$(openssl rand -hex 32)"

# Replace the FIRST line whose key matches (keys here contain no regex metachars).
set_var() {
  local key="$1" val="$2"
  awk -v k="$key" -v v="$val" '
    BEGIN{done=0}
    { if(!done && $0 ~ ("^" k "=")){ print k"="v; done=1 } else print }
  ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
}

set_var REDIS_PASSWORD          "$REDIS_PASSWORD_VAL"
set_var SESSION_SIGNING_SECRETS "$SESSION_SIGNING_VAL"

echo "[gen-env] Created $ENV_FILE with generated secrets for:"
echo "    REDIS_PASSWORD           (MANDATORY — compose \${REDIS_PASSWORD:?} refuses to start without it)"
echo "    SESSION_SIGNING_SECRETS  (admin HttpOnly-session signing key)"
echo ""
echo "[gen-env] RECORD THESE NOW (printed once; they live only in $ENV_FILE, which is git-ignored):"
echo "    REDIS_PASSWORD=$REDIS_PASSWORD_VAL"
echo "    SESSION_SIGNING_SECRETS=$SESSION_SIGNING_VAL"
echo ""
echo "[gen-env] Every other var keeps its safe dev default (docker-compose.yml supplies them)."
echo "[gen-env] Add real provider keys (OPENAI/ANTHROPIC/OPENROUTER) to $ENV_FILE when you need live models."
