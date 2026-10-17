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
. "$ROOT/scripts/gate/it/lib.sh"
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
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
