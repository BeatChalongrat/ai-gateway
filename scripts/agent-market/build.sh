#!/usr/bin/env bash
# Agent Market — build the agent-runner image.
#
#   bash scripts/agent-market/build.sh [REV]        # from a commit (git archive), tag :<short sha>
#   bash scripts/agent-market/build.sh --worktree   # from the working tree (development), tag :dev
#   options: --latest (also tag :latest)
#
# Like scripts/build-images.sh: the build context holds only what the
# image needs (services/chat-edge-api + backend/agent_market), the
# REVISION/CREATED labels say what was built, and nothing is pushed or
# restarted. The core build script is untouched (its service list does not
# know agent-runner, on purpose).
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
REV_IN=HEAD
FROM_TREE=0
LATEST=0
for a in "$@"; do
  case "$a" in
    --worktree) FROM_TREE=1 ;;
    --latest) LATEST=1 ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) REV_IN="$a" ;;
  esac
done
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ "$FROM_TREE" = 1 ]; then
  REVISION="dev-$(git -C "$ROOT" rev-parse --short=7 HEAD)"
  TAG=dev
  mkdir -p "$TMP/services" "$TMP/backend"
  cp -r "$ROOT/services/chat-edge-api" "$TMP/services/"
  cp -r "$ROOT/backend/agent_market" "$TMP/backend/"
  find "$TMP" -name __pycache__ -type d -prune -exec rm -rf {} +
else
  FULL=$(git -C "$ROOT" rev-parse --verify "${REV_IN}^{commit}")
  REVISION=$(git -C "$ROOT" rev-parse --short=7 "$FULL")
  TAG=$REVISION
  git -C "$ROOT" archive --format=tar "$FULL" services/chat-edge-api backend/agent_market | tar -x -C "$TMP"
fi
SRC=$(native "$TMP")
echo "building ai-gateway-agent-runner:$TAG (revision $REVISION)"
docker build -f "$SRC/services/chat-edge-api/cmd/agent-runner/Dockerfile" \
  --build-arg REVISION="$REVISION" --build-arg CREATED="$CREATED" \
  -t "ai-gateway-agent-runner:$TAG" "$SRC"
if [ "$LATEST" = 1 ]; then docker tag "ai-gateway-agent-runner:$TAG" ai-gateway-agent-runner:latest; fi
got=$(docker image inspect "ai-gateway-agent-runner:$TAG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[ "$got" = "$REVISION" ] || { echo "revision label mismatch: $got != $REVISION" >&2; exit 1; }
echo "ok ai-gateway-agent-runner:$TAG revision=$got"
