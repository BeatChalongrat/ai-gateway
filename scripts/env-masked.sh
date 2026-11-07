#!/usr/bin/env bash
# scripts/env-masked.sh — check a container's (or an env file's) settings
# without printing their values.
#
# Usage:
#   scripts/env-masked.sh <container> [key-regex]
#   scripts/env-masked.sh --env-file .env [key-regex]
#   scripts/env-masked.sh ai-gateway-chat-edge-api 'REDIS|POSTGRES'
#
# `docker inspect <c>`, `docker exec <c> env` and `cat .env` put every secret
# into the terminal, the transcript and whatever captures it. Masking the
# output is not enough: FIX134's version of this script printed the first 3
# characters of anything it judged secret and everything else in full, and a
# judgement that misses (a webhook or ntfy topic URL under a plain key name,
# a connection URL with an empty user such as redis://:pass@host that an
# ad-hoc mask did not match — AM1c) prints the secret. So (AM2) no value is
# printed unless it is provably harmless:
#
#   KEY = value                       only when the KEY is on the allowlist
#                                     below AND the value is a plain word or
#                                     number (no ":", "/", "@", "=", "?", no
#                                     longer than 40 characters);
#   KEY   set  48 chars  sha256:1a2b3c4d
#                                     everything else: length and the first 8
#                                     hex characters of its SHA-256, enough to
#                                     compare the container with .env or with a
#                                     new value, never enough to use it;
#   KEY   (empty)
#
# Nothing is guessed from the key name the other way round: an unknown key is
# never shown. The optional key-regex is a case-insensitive extended regex on
# the key. Rule and reason: docs/ops/README-secrets.md.
set -euo pipefail

usage() { echo "usage: $0 <container> [key-regex] | $0 --env-file FILE [key-regex]" >&2; exit 2; }

source_kind=container
if [ "${1:-}" = "--env-file" ]; then
  [ $# -ge 2 ] && [ $# -le 3 ] || usage
  source_kind=file; src="$2"; pattern="${3:-}"
else
  [ $# -ge 1 ] && [ $# -le 2 ] || usage
  src="$1"; pattern="${2:-}"
fi

# Keys whose values are configuration, never credentials. A key that also
# names a credential (PASSWORD, SECRET, KEY, TOKEN, …) is never shown even if
# it matches here.
show_key() {
  case "$1" in
    *PASSWORD*|*PASSWD*|*SECRET*|*KEY*|*TOKEN*|*CREDENTIAL*|*DSN*|*AUTH*|*URL*|*URI*|*WEBHOOK*|*TOPIC*|*SALT*|*PRIVATE*|*COOKIE*|*SESSION*) return 1 ;;
  esac
  case "$1" in
    APP_ENV|ENVIRONMENT|ENV|NODE_ENV|GIN_MODE|TZ|LANG|LC_ALL|HOSTNAME|LOG_LEVEL|WEB_CONCURRENCY|PORT) return 0 ;;
    *_ENABLED|*_ENABLE|*_MODE|*_PORT|*_LEVEL|*_WORKERS|*_SECONDS|*_TIMEOUT|*_MS|*_MB|*_GB|*_COUNT|*_LIMIT|*_SIZE|*_MAX|*_MIN|*_VERSION|*_TAG|*_REV|*_HOST|*_PROVIDER|*_MODEL|*_REGION|*_BUCKET|*_DB|*_NAME|*_PROFILE) return 0 ;;
  esac
  return 1
}

plain_value() {
  [ "${#1}" -le 40 ] || return 1
  case "$1" in
    *[!A-Za-z0-9._,+-]*) return 1 ;;
  esac
  return 0
}

fingerprint() { printf '%s' "$1" | sha256sum | cut -c1-8; }

if [ "$source_kind" = container ]; then
  # One KEY=VALUE per line, as the container was created (compose env + image
  # ENV). The output of docker inspect never leaves this script.
  if ! env_lines=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$src" 2>/dev/null); then
    echo "env-masked: no such container: $src" >&2
    exit 1
  fi
else
  [ -f "$src" ] || { echo "env-masked: no such file: $src" >&2; exit 1; }
  # KEY=VALUE lines; comments and blanks skipped, an optional `export `,
  # surrounding quotes and a trailing CR removed (the same value compose sees).
  env_lines=$(sed -e 's/\r$//' -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' -e 's/^[[:space:]]*export[[:space:]]\{1,\}//' "$src" \
    | sed -E "s/^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$/\1=\2/; s/^([A-Za-z_][A-Za-z0-9_]*)='(.*)'$/\1=\2/")
fi

printf '%s\n' "$env_lines" | LC_ALL=C sort | while IFS= read -r line; do
  [ -n "$line" ] || continue
  case "$line" in
    *=*) key="${line%%=*}"; val="${line#*=}" ;;
    *)   continue ;;   # a continuation of a multi-line value: never shown
  esac
  if [ -n "$pattern" ] && ! printf '%s' "$key" | grep -Eiq -- "$pattern"; then
    continue
  fi
  if [ -z "$val" ]; then
    printf '%-40s (empty)\n' "$key"
  elif show_key "$key" && plain_value "$val"; then
    printf '%-40s = %s\n' "$key" "$val"
  else
    printf '%-40s set  %d chars  sha256:%s\n' "$key" "${#val}" "$(fingerprint "$val")"
  fi
done
