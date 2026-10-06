#!/usr/bin/env bash
# AM2 removal drill (code): revert every Agent Market commit in a scratch
# worktree and compare it with the tree the core would have had without
# Agent Market — the commit AM1 was built on plus every non-AM commit since,
# cherry-picked in order. The diff must be 0 lines.
#
#   bash scripts/probes/AM2/removal_drill.sh [keep]    # "keep" leaves both worktrees for the next drill steps
#
# Worktrees go under $TMP (outside the checkout); no node_modules junctions
# are made in them, so `git worktree remove` is safe.
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
BASE=${AM_DRILL_BASE:-6b99cd10} # AM1 was built on this commit
TIP=$(git rev-parse HEAD)
WT=${AM_DRILL_DIR:-$(cygpath -m "${TMP:-/tmp}" 2>/dev/null || echo /tmp)/am2-drill}
A="$WT/reverted"
B="$WT/core"
rm -rf "$WT"
mkdir -p "$WT"
git worktree prune

# Agent Market commits are the ones whose subject starts with AM<digit>.
mapfile -t AM < <(git log --format='%h %s' "$BASE..$TIP" | awk '$2 ~ /^AM[0-9]/ {print $1}')
mapfile -t CORE < <(git log --reverse --format='%h %s' "$BASE..$TIP" | awk '$2 !~ /^AM[0-9]/ {print $1}')
echo "base $BASE  tip $(git rev-parse --short "$TIP")"
echo "Agent Market commits: ${#AM[@]} (reverted newest first)"
echo "other commits since base: ${#CORE[@]} (${CORE[*]:-none})"

git worktree add --detach "$A" "$TIP" >/dev/null 2>&1
git -C "$A" -c user.name="drill" -c user.email="drill@localhost" revert --no-edit "${AM[@]}" >/dev/null
echo "reverted tree: $(git -C "$A" rev-parse --short HEAD)"

git worktree add --detach "$B" "$BASE" >/dev/null 2>&1
if [ "${#CORE[@]}" -gt 0 ]; then
  git -C "$B" -c user.name="drill" -c user.email="drill@localhost" cherry-pick "${CORE[@]}" >/dev/null
fi
echo "core-only tree: $(git -C "$B" rev-parse --short HEAD)"

DIFF_LINES=$(git diff "$(git -C "$A" rev-parse HEAD)" "$(git -C "$B" rev-parse HEAD)" | wc -l | tr -d ' ')
echo "diff reverted..core-only: $DIFF_LINES lines"
git diff --stat "$(git -C "$A" rev-parse HEAD)" "$(git -C "$B" rev-parse HEAD)" | tail -5
HITS=$( (git -C "$A" grep -n -i -I "agent.market\|agentmarket\|agent-runner\|doc-converter\|web-fetcher" -- ':!*.md' ':!CHANGELOG*' || true) | wc -l | tr -d ' ')
echo "git grep hits in the reverted tree (code, not docs): $HITS"

if [ "${1:-}" != "keep" ]; then
  git worktree remove --force "$A"
  git worktree remove --force "$B"
  echo "worktrees removed"
fi
[ "$DIFF_LINES" = 0 ]
