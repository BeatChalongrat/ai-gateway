#!/usr/bin/env bash
# FIX126 O6 — build service images from a CLEAN tree of one commit, stamped
# with that commit.
#
#   scripts/build-images.sh [options] <service>...
#
#   scripts/build-images.sh web-chat chat-edge-api          # HEAD, :<sha> tag
#   scripts/build-images.sh --latest web-chat               # also :latest
#   scripts/build-images.sh --rev 141d082 gateway backend
#
# What it does, per service:
#   1. `git archive <rev>` into a temp dir — uncommitted edits, untracked
#      files and a stale node_modules/.next in the checkout never reach the
#      image (they cannot: the build context is the archive).
#   2. Reads the service's build block (context, dockerfile, build args) from
#      the compose file in that archive via `docker compose config`, so the
#      NEXT_PUBLIC_* and other build args are EXACTLY what `docker compose
#      build` would pass. web-chat and chat-edge-api come from
#      docker-compose.webchat.yml (the shape that runs them); every other
#      service from docker-compose.yml. Values are interpolated from the
#      checkout's .env (or --env-file), like compose does.
#   3. `docker build` with REVISION=<short sha> and CREATED=<UTC now>, which
#      every Dockerfile turns into the org.opencontainers.image.revision /
#      .created labels, then checks the label on the result.
#
# Tags: <image>:<short sha> always; <image>:latest only with --latest (that
# is the tag the compose files run — building it does NOT restart anything;
# `docker compose ... up -d <service>` does). Nothing is pushed or started.
#
# Options:
#   --rev <rev>        commit to build (default HEAD)
#   --env-file <path>  env file for compose interpolation (default: <repo>/.env
#                      when it exists)
#   --latest           also tag <image>:latest
#   --image <name>     image repository name (one service only), e.g. for a
#                      throwaway check build: --image fix126-check-web-chat
#   --no-cache         pass --no-cache to docker build
#
# Services: web-chat chat-edge-api gateway backend worker mcp-gateway
#   ner-service dlp-sidecar rate-limiter log-worker mock-provider admin-ui
#   portal sandbox
#
# Needs: git, tar, docker (with compose v2), python3 (or python).
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

REV_IN=HEAD
ENV_FILE=""
LATEST=0
IMAGE_OVERRIDE=""
NO_CACHE=()
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV_IN="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --latest) LATEST=1; shift ;;
    --image) IMAGE_OVERRIDE="$2"; shift 2 ;;
    --no-cache) NO_CACHE=(--no-cache); shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) SERVICES+=("$1"); shift ;;
  esac
done
[ ${#SERVICES[@]} -gt 0 ] || { usage >&2; exit 2; }
if [ -n "$IMAGE_OVERRIDE" ] && [ ${#SERVICES[@]} -ne 1 ]; then
  echo "--image names ONE service's image; got ${#SERVICES[@]} services" >&2; exit 2
fi

# First interpreter that actually runs (on Windows `python3` can be the
# Microsoft Store stub, which exists on PATH but only prints a hint).
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import json' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "python3 (or python) is required to read the compose build block" >&2; exit 1; }

ROOT=$(git rev-parse --show-toplevel)
FULL=$(git -C "$ROOT" rev-parse --verify "${REV_IN}^{commit}")
REVISION=$(git -C "$ROOT" rev-parse --short=7 "$FULL")
CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
export REVISION CREATED

if [ -z "$ENV_FILE" ] && [ -f "$ROOT/.env" ]; then ENV_FILE="$ROOT/.env"; fi
ENV_ARGS=()
[ -n "$ENV_FILE" ] && ENV_ARGS=(--env-file "$ENV_FILE")

if [ "$REV_IN" = "HEAD" ] && [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  echo "note: the checkout has uncommitted changes — they are NOT in this build (it builds $REVISION as committed)."
fi

# Native path for the Windows docker CLI under Git Bash; plain path elsewhere.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git -C "$ROOT" archive --format=tar "$FULL" | tar -x -C "$TMP"
SRC=$(native "$TMP")
echo "building $REVISION ($FULL) from a clean archive; created=$CREATED"

# Prints context, dockerfile, image, then KEY=VALUE per build arg, each
# NUL-terminated. Only the build block is read — environment values (which
# can hold secrets) are never printed.
read_build='
import json, sys
svc = sys.argv[1]
d = json.load(sys.stdin)["services"][svc]
b = d.get("build") or {}
if not b:
    sys.exit("service %s has no build block" % svc)
out = [b.get("context", "."), b.get("dockerfile", "Dockerfile"), (d.get("image") or "").rsplit(":", 1)[0]]
for k, v in sorted((b.get("args") or {}).items()):
    out.append("%s=%s" % (k, "" if v is None else v))
sys.stdout.write("\0".join(out) + "\0")
'

for svc in "${SERVICES[@]}"; do
  args=()
  if [ "$svc" = "sandbox" ]; then
    # Not a compose service: built by `make sandbox-image` from this context.
    context="$SRC/services/chat-edge-api"; dockerfile="sandbox/Dockerfile.sandbox"; image="ai-gateway-sandbox"
    args=(--build-arg "REVISION=$REVISION" --build-arg "CREATED=$CREATED")
  else
    case "$svc" in
      web-chat|chat-edge-api) file=docker-compose.webchat.yml ;;
      *) file=docker-compose.yml ;;
    esac
    fields=()
    while IFS= read -r -d '' f; do fields+=("$f"); done < <(
      docker compose -p ai-gateway --project-directory "$SRC" -f "$SRC/$file" "${ENV_ARGS[@]}" \
        --profile desktop --profile scaling config --format json "$svc" \
      | "$PY" -c "$read_build" "$svc")
    [ ${#fields[@]} -ge 3 ] || { echo "could not read the build block of $svc from $file" >&2; exit 1; }
    context="${fields[0]}"; dockerfile="${fields[1]}"; image="${fields[2]:-ai-gateway-$svc}"
    for kv in "${fields[@]:3}"; do args+=(--build-arg "$kv"); done
    case "$dockerfile" in /*|[A-Za-z]:*) ;; *) dockerfile="$context/$dockerfile" ;; esac
  fi
  [ -n "$IMAGE_OVERRIDE" ] && image="$IMAGE_OVERRIDE"
  tags=(-t "$image:$REVISION")
  [ "$LATEST" = 1 ] && tags+=(-t "$image:latest")

  echo "== $svc -> $image:$REVISION$([ "$LATEST" = 1 ] && echo ' (+latest)')"
  echo "   build args: $(printf '%s ' "${args[@]}" | grep -oE -- '--build-arg [A-Z_0-9]+' | awk '{printf "%s ", $2}')"
  docker build "${NO_CACHE[@]}" "${tags[@]}" -f "$dockerfile" "${args[@]}" "$context"

  got=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image:$REVISION")
  if [ "$got" != "$REVISION" ]; then
    echo "ERROR: $image:$REVISION carries revision label '$got', expected '$REVISION'" >&2; exit 1
  fi
  echo "   ok: org.opencontainers.image.revision=$got"
done
