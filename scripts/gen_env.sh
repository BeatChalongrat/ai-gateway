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

if ! command -v openssl >/dev/null 2>&1; then
  echo "[gen-env] ERROR: openssl not found on PATH (needed to generate secrets)." >&2
  exit 1
fi

# hex (not base64): the value is folded into redis:// URLs by the rate-limiter
# and chat-edge (redis://:${REDIS_PASSWORD}@redis:6379). base64's '/', '+', '='
# would corrupt that URL — hex is URL-safe.
gen_secret() { openssl rand -hex 32; }

# Replace the FIRST line whose key matches (keys here contain no regex metachars).
set_var() {
  local key="$1" val="$2"
  awk -v k="$key" -v v="$val" '
    BEGIN{done=0}
    { if(!done && $0 ~ ("^" k "=")){ print k"="v; done=1 } else print }
  ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
}

# has_key: true if an uncommented KEY= line already exists.
has_key() { grep -qE "^$1=" "$ENV_FILE"; }

# The compose stack hard-requires these (via ${VAR:?} / used in redis URLs).
# A .env created before they were added (or a hand-copied one) that LACKS them
# makes `docker compose up` fail cryptically — the trap that left a stack weeks
# stale. gen_env ensures they exist, whether creating fresh or backfilling.
REQUIRED_KEYS=(REDIS_PASSWORD SESSION_SIGNING_SECRETS)

if [ ! -f "$ENV_FILE" ]; then
  # ---- Fresh create: copy example, replace shipped changeme-* placeholders --
  if [ ! -f "$EXAMPLE" ]; then
    echo "[gen-env] ERROR: $EXAMPLE not found." >&2
    exit 1
  fi
  cp "$EXAMPLE" "$ENV_FILE"
  declare -A GENERATED=()
  for key in "${REQUIRED_KEYS[@]}"; do
    val="$(gen_secret)"; GENERATED[$key]="$val"
    if has_key "$key"; then set_var "$key" "$val"; else printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"; fi
  done
  echo "[gen-env] Created $ENV_FILE with generated secrets:"
  for key in "${REQUIRED_KEYS[@]}"; do echo "    $key=${GENERATED[$key]}"; done
  echo ""
  echo "[gen-env] RECORD THESE NOW (printed once; they live only in $ENV_FILE, which is git-ignored)."
  echo "[gen-env] Every other var keeps its safe dev default (docker-compose.yml supplies them)."
  echo "[gen-env] Add real provider keys (OPENAI/ANTHROPIC/OPENROUTER) to $ENV_FILE when you need live models."
else
  # ---- Existing .env: BACKFILL only MISSING required vars (never overwrite) --
  backfilled=0
  for key in "${REQUIRED_KEYS[@]}"; do
    if has_key "$key"; then
      continue  # present already — leave the operator's value 100% untouched
    fi
    val="$(gen_secret)"
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    echo "[gen-env] Backfilled MISSING $key into $ENV_FILE (generated): $key=$val"
    backfilled=1
  done
  if [ "$backfilled" -eq 0 ]; then
    echo "[gen-env] $ENV_FILE already has all required vars — left untouched (idempotent)."
  else
    echo "[gen-env] RECORD any backfilled value(s) above — they live only in $ENV_FILE (git-ignored)."
  fi
fi
