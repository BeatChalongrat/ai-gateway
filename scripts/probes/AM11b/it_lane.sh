#!/usr/bin/env bash
# AM11b — the integration lane: everything AM12's lane runs (AM10's chunks, then the rounds on the web),
# then the rounds that do what they were asked (runner/am11b_asked_it_test.go) — the real engine in the
# real sandbox, a stand-in for the model that reads a person's words.
#
#   bash scripts/probes/AM11b/it_lane.sh          the whole lane (about 50 minutes)
#   bash scripts/probes/AM11b/it_lane.sh asked    the AM11b rounds only
#
# Prints each test's verdict as it ends and `lane_exit=<0|1>` last.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "asked" ]; then
  bash "$ROOT/scripts/probes/AM12/it_lane.sh" | sed 's/^lane_exit=/am12_lane_exit=/' | tee /dev/stderr | grep -q '^am12_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestARoundDoesWhatItWasAskedAndSaysWhatItCouldNot|TestARoundNobodyAskedAnythingOfIsAsItWas)$'
  '^(TestTheExpenseAuditOrdersItsClaimsAsAskedAndNoModelSeesAReceipt)$'
)
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 1500 bash "$ROOT/scripts/probes/AM7/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: [^t]' || true
  [ "$st" -eq 0 ] || rc=1
done
echo "lane_exit=$rc"
