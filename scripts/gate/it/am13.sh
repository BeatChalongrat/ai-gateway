#!/usr/bin/env bash
# AM13 — the integration lane of the card: the rounds that do what they were asked. AM11b's three (the
# report, a round nobody asked anything of, the expense audit) and AM13's two — the bank reconciliation
# asked for two orders and then another, and procurement asked to order by price and to say which is
# best. The real engine in the real sandbox, a stand-in for the model that reads a person's words.
#
#   bash scripts/gate/it/am13.sh asked     these five (what scripts/gate.sh it runs last, as GATE_IT)
#   bash scripts/gate/it/am13.sh new       AM13's two only
#
# The standing chunks (AM10's, then the rounds on the web) are run before it by the gate:
#   GATE_IT="scripts/gate/it/am13.sh asked" bash scripts/gate.sh it
#
# Prints each test's verdict as it ends and `lane_exit=<0|1>` last.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export AGENT_MARKET_IT=1
rc=0
CHUNKS=(
  '^(TestTheBankReconciliationTakesOneOrderAndALaterOneTakesItsPlace)$'
  '^(TestProcurementOrdersByPriceWhenAskedAndIsNeverTalkedIntoAVerdict)$'
  '^(TestWordsThatCameBackUnreadAreReadOnceMoreBeforeAPersonIsTold)$'
  '^(TestTheTwoSwitchesAreWhatTheGatewayIsToldAndTheCardSaysWhatWasDone)$'
)
if [ "${1:-asked}" != "new" ]; then
  CHUNKS+=(
    '^(TestARoundDoesWhatItWasAskedAndSaysWhatItCouldNot|TestARoundNobodyAskedAnythingOfIsAsItWas)$'
    '^(TestTheExpenseAuditOrdersItsClaimsAsAskedAndNoModelSeesAReceipt)$'
  )
fi
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 1500 bash "$ROOT/scripts/gate/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: [^t]' || true
  [ "$st" -eq 0 ] || rc=1
done
echo "lane_exit=$rc"
