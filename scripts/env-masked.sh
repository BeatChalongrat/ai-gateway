#!/usr/bin/env bash
# scripts/env-masked.sh — a container's environment with every secret masked.
#
# Usage:
#   scripts/env-masked.sh <container> [grep-pattern]
#   scripts/env-masked.sh ai-gateway-backend
#   scripts/env-masked.sh ai-gateway-chat-edge-api 'REDIS|POSTGRES'
#
# `docker inspect <c>` and `docker exec <c> env` put every secret the
# container holds into the terminal, the transcript and whatever captures it.
# This prints the KEYS in full and, for anything that can hold a secret, only
# the first 3 characters, an ellipsis and the length — enough to tell an empty
# or truncated value from a set one, never enough to use it. Rule and reason:
# docs/ops/README-secrets.md.
#
# What is masked (the key decides; the value can also trip it):
#   * keys containing PASSWORD, PASSWD, SECRET, KEY, TOKEN, CREDENTIAL, DSN,
#     or ending in _AUTH (REDISCLI_AUTH);
#   * any value that carries credentials: scheme://user:pass@host, or a
#     token=, password=, secret=, api_key= / apikey= query parameter (so a
#     DATABASE_URL / REDIS_URL with a password is masked, a plain service
#     URL is not).
# How: 8 characters or longer → first 3 + "…" + "(N chars)"; shorter → "…";
# empty → "(empty)". Everything else (APP_ENV, PORT, HOSTNAME, *_ENABLED,
# *_MODE, service URLs without credentials, …) prints in full.
# The optional second argument is a case-insensitive extended regex on the key.
# Works in Git Bash; needs docker only (the env comes from `docker inspect`,
# but it never leaves this script unmasked).
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: $0 <container> [grep-pattern]" >&2
  exit 2
fi
container="$1"
pattern="${2:-}"

mask() {
  local v="$1" n=${#1}
  if [ "$n" -eq 0 ]; then
    printf '(empty)'
  elif [ "$n" -lt 8 ]; then
    printf '…'
  else
    printf '%s…(%d chars)' "${v:0:3}" "$n"
  fi
}

sensitive() {
  local key="$1" val="$2"
  case "$key" in
    *PASSWORD*|*PASSWD*|*SECRET*|*KEY*|*TOKEN*|*CREDENTIAL*|*DSN*|*_AUTH) return 0 ;;
  esac
  case "$val" in
    *://*:*@*|*token=*|*TOKEN=*|*password=*|*PASSWORD=*|*secret=*|*SECRET=*|*api_key=*|*apikey=*) return 0 ;;
  esac
  return 1
}

# One KEY=VALUE per line, as the container was created (compose env + image ENV).
if ! env_lines=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null); then
  echo "env-masked: no such container: $container" >&2
  exit 1
fi

printf '%s\n' "$env_lines" | LC_ALL=C sort | while IFS= read -r line; do
  [ -n "$line" ] || continue
  case "$line" in
    *=*) key="${line%%=*}"; val="${line#*=}" ;;
    *)   key="$line";        val="" ;;   # a continuation of a multi-line value: never shown
  esac
  if [ -n "$pattern" ] && ! printf '%s' "$key" | grep -Eiq -- "$pattern"; then
    continue
  fi
  case "$line" in
    *=*) if sensitive "$key" "$val"; then printf '%s=%s\n' "$key" "$(mask "$val")"; else printf '%s=%s\n' "$key" "$val"; fi ;;
    *)   printf '%s\n' "$(mask "$line")" ;;
  esac
done
