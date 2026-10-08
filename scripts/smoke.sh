#!/usr/bin/env bash
# FIX134 F — the permanent smoke suite: every entry point, one line per check, exit 0 = green.
#
#   SMOKE_TARGET=live bash scripts/smoke.sh          # the live stack, read-mostly (default)
#   SMOKE_TARGET=test bash scripts/smoke.sh          # the test stack (compose project sran-test)
#   SMOKE_DESKTOP=1|0     the installed desktop app over CDP (default: on for live, off for test)
#   SMOKE_ONLY=tls,apps   a subset of the areas (tls apps api jobs due alert desktop)
#   SMOKE_OUT=<file>      a copy of the JSON result (the daily task points it at the backup folder)
#   SMOKE_ENV_FILE=<.env> the .env to read credentials from (default: the repo's; a worktree has none)
#
# Result: scripts/probes/FIX134/results/smoke/smoke_<target>_<utc>.json (gitignored) and the
# table on stdout. scripts/smoke/README.md says what each check needs. Node runs the checks;
# https calls trust the machine's local CA (infra/caddy/certs/rootCA.pem) through
# NODE_EXTRA_CA_CERTS, while the tls.chain check deliberately uses the OS store instead.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export SMOKE_TARGET="${SMOKE_TARGET:-live}"
case "$SMOKE_TARGET" in
  live|test) ;;
  *) echo "SMOKE_TARGET must be live or test (got '$SMOKE_TARGET')" >&2; exit 2 ;;
esac
if [ -z "${SMOKE_DESKTOP:-}" ]; then
  if [ "$SMOKE_TARGET" = live ]; then SMOKE_DESKTOP=1; else SMOKE_DESKTOP=0; fi
fi
export SMOKE_DESKTOP

command -v node >/dev/null 2>&1 || { echo "node is required (the checks are scripts/smoke/*.js)" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker is required (container logs, records, the test alert)" >&2; exit 2; }

# Git Bash on Windows: node wants Windows paths (cygpath -w); elsewhere the POSIX path is the path.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# Node trusts the OS store with NODE_USE_SYSTEM_CA (Node 24; older versions ignore it) and, as
# a fallback, the local CA file (SMOKE_CA, or the repo's copy, gitignored and per machine).
export NODE_USE_SYSTEM_CA=1
CA="${SMOKE_CA:-$ROOT/infra/caddy/certs/rootCA.pem}"
if [ -f "$CA" ] && [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  export NODE_EXTRA_CA_CERTS="$(native "$CA")"
fi

OUT_DIR="$ROOT/scripts/probes/FIX134/results/smoke"
mkdir -p "$OUT_DIR"
RESULT="$OUT_DIR/smoke_${SMOKE_TARGET}_$(date -u +%Y%m%dT%H%M%SZ).json"
export SMOKE_RESULT="$(native "$RESULT")"

node "$(native "$ROOT/scripts/smoke/run.js")"
rc=$?
SMOKE_RESULT="$RESULT"
if [ -n "${SMOKE_OUT:-}" ] && [ -f "$SMOKE_RESULT" ]; then
  mkdir -p "$(dirname "$SMOKE_OUT")" && cp "$SMOKE_RESULT" "$SMOKE_OUT"
fi
exit $rc
