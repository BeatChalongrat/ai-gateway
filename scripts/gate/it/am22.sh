#!/usr/bin/env bash
# AM22 — the integration lane of the card, and from now on the gate's own last lane: AM18's (which runs AM16's,
# AM15's and AM13's first), then the commentary of an agent whose words a model now writes: with its model away the
# working paper is delivered with the rules' words and says so on its face; with its model there, the model's words
# are on the sheet with the neutral line (AM23) and the rules' sentence is nowhere in the file.
#
#   bash scripts/gate/it/am22.sh          AM18's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am22.sh new      these only
#
# TestEveryIntegrationTestIsInALaneOfTheGate (am15_lane_test.go) holds the list: a test no chunk names
# fails there, and so does a name in a chunk that no test has.
#
# Prints each test's verdict as it ends and `lane_exit=<0|1>` last.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "new" ]; then
  bash "$ROOT/scripts/gate/it/am18.sh" | sed 's/^lane_exit=/am18_lane_exit=/' | tee -a /dev/stderr | grep -q '^am18_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestAReconciliationWhoseModelIsAwayIsDeliveredWithTheRulesWordsSayingSo)$'
  '^(TestAReconciliationAModelWroteCarriesTheNeutralLineAndNeverTheRulesSentence)$'
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
