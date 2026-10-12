#!/usr/bin/env bash
# Agent Market — build the web-fetcher image (headless Chromium + Thai fonts).
#
#   bash scripts/agent-market/build-fetcher.sh [REV]        # from a commit (git archive), tag :<short sha>
#   bash scripts/agent-market/build-fetcher.sh --worktree   # from the working tree (development), tag :dev
#   options: --latest (also tag :latest)
#
# Like scripts/agent-market/build.sh: the build context holds only what the
# image needs (services/web-fetcher + the repo's two Sarabun fonts), the
# REVISION/CREATED labels say what was built, and nothing is pushed or
# restarted.
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
REV_IN=HEAD
FROM_TREE=0
LATEST=0
for a in "$@"; do
  case "$a" in
    --worktree) FROM_TREE=1 ;;
    --latest) LATEST=1 ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) REV_IN="$a" ;;
  esac
done
IMAGE=ai-gateway-web-fetcher
FONTS="services/chat-edge-api/scripts/fonts/Sarabun-Regular.ttf services/chat-edge-api/scripts/fonts/Sarabun-Bold.ttf"
# on battery nothing is built (scripts/ops/power.sh); RUN_ON_BATTERY=yes on the command runs it anyway
bash "$ROOT/scripts/ops/power.sh" need "the web fetcher's image build" || exit 3
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ "$FROM_TREE" = 1 ]; then
  REVISION="dev-$(git -C "$ROOT" rev-parse --short=7 HEAD)"
  TAG=dev
  mkdir -p "$TMP/services/chat-edge-api/scripts/fonts"
  cp -r "$ROOT/services/web-fetcher" "$TMP/services/"
  for f in $FONTS; do cp "$ROOT/$f" "$TMP/$f"; done
  rm -rf "$TMP/services/web-fetcher/node_modules"
else
  FULL=$(git -C "$ROOT" rev-parse --verify "${REV_IN}^{commit}")
  REVISION=$(git -C "$ROOT" rev-parse --short=7 "$FULL")
  TAG=$REVISION
  # shellcheck disable=SC2086
  git -C "$ROOT" archive --format=tar "$FULL" services/web-fetcher $FONTS | tar -x -C "$TMP"
fi
SRC=$(native "$TMP")
echo "building $IMAGE:$TAG (revision $REVISION)"
docker build -f "$SRC/services/web-fetcher/Dockerfile" \
  --build-arg REVISION="$REVISION" --build-arg CREATED="$CREATED" \
  -t "$IMAGE:$TAG" "$SRC"
if [ "$LATEST" = 1 ]; then docker tag "$IMAGE:$TAG" "$IMAGE:latest"; fi
got=$(docker image inspect "$IMAGE:$TAG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[ "$got" = "$REVISION" ] || { echo "revision label mismatch: $got != $REVISION" >&2; exit 1; }
echo "ok $IMAGE:$TAG revision=$got"
