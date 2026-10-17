#!/usr/bin/env bash
# AM15 — the integration lane of the card, and from now on the gate's own last lane: AM13's (the rounds
# that do what they were asked) and the fixture of a run and a model that cannot be reached — down, the
# run waits and says so; back, the same run finishes from the step that could not ask. The real engine
# in the real sandbox, a stand-in gateway that answers what the real one answered with the model away.
#
#   bash scripts/gate/it/am15.sh          AM13's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am15.sh new      these only
#
# A test of the runner that needs the sandbox is run by a lane only when a chunk names it: one that no
# chunk names is skipped by every lane and fails nowhere. So the runner's own unit lane holds the list:
# TestEveryIntegrationTestIsInALaneOfTheGate (am15_lane_test.go) fails for a test no chunk of the gate's
# lanes names, and for a name in a chunk that no test has (a renamed test matches nothing and passes).
#
# Prints each test's verdict as it ends and `lane_exit=<0|1>` last.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$ROOT/scripts/gate/it/lib.sh"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "new" ]; then
  bash "$ROOT/scripts/gate/it/am13.sh" asked | sed 's/^lane_exit=/am13_lane_exit=/' | tee -a /dev/stderr | grep -q '^am13_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestARunWaitsForAModelThatCannotBeReachedAndGoesOnWhenItIsBack)$'
  '^(TestARunThatWaitsForItsModelAndIsCancelledIsChargedNothing|TestARunThatFindsItsModelAwayEveryTimeItGoesOnEndsSayingSo)$'
  '^(TestAScanWaitsForTheModelThatReadsIt|TestTheReportsCommentaryWaitsForAModelThatIsAwayAndIsNotWrittenByRules)$'
)
for c in "${CHUNKS[@]}"; do
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
