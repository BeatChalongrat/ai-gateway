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
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "new" ]; then
  bash "$ROOT/scripts/gate/it/am13.sh" asked | sed 's/^lane_exit=/am13_lane_exit=/' | tee /dev/stderr | grep -q '^am13_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestARunWaitsForAModelThatCannotBeReachedAndGoesOnWhenItIsBack)$'
  '^(TestARunThatWaitsForItsModelAndIsCancelledIsChargedNothing|TestARunThatFindsItsModelAwayEveryTimeItGoesOnEndsSayingSo)$'
  '^(TestAScanWaitsForTheModelThatReadsIt|TestTheReportsCommentaryWaitsForAModelThatIsAwayAndIsNotWrittenByRules)$'
)
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 1500 bash "$ROOT/scripts/gate/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: [^t]' || true
  [ "$st" -eq 0 ] || rc=1
  # a chunk that ran nothing is not a chunk that passed
  printf '%s\n' "$out" | grep -q '^--- PASS' || { echo "FAIL chunk ran no test: $c"; rc=1; }
done
echo "lane_exit=$rc"
