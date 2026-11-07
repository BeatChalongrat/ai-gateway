#!/usr/bin/env bash
# AM16 — the integration lane of the card, and from now on the gate's own last lane: AM15's (which runs
# AM13's first), then the fixtures of what a run says about where its documents went. The real engine
# in the real sandbox; a stand-in gateway that answers for every model name, so a model the runner does
# not hold as in-house is a model outside.
#
#   bash scripts/gate/it/am16.sh          AM15's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am16.sh new      these only
#
# TestEveryIntegrationTestIsInALaneOfTheGate (am15_lane_test.go) holds the list: a test no chunk names
# fails there, and so does a name in a chunk that no test has.
#
# Prints each test's verdict as it ends and `lane_exit=<0|1>` last.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$ROOT/scripts/gate/it/lib.sh"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "new" ]; then
  bash "$ROOT/scripts/gate/it/am15.sh" | sed 's/^lane_exit=/am15_lane_exit=/' | tee -a /dev/stderr | grep -q '^am15_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestAScanReadByAModelOutsideIsSaidOnTheFaceOfTheRunsCard)$'
  '^(TestAPageSentToAModelThatDidNotAnswerStillLeft)$'
)
for c in "${CHUNKS[@]}"; do
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
