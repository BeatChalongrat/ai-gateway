#!/usr/bin/env bash
# AM18 — the integration lane of the card, and from now on the gate's own last lane: AM16's (which runs
# AM15's and AM13's first), then the documents in both languages: each working paper printed from an
# English run through the real converter, every row beside its amounts on one page, no sheet named in Thai.
#
#   bash scripts/gate/it/am18.sh          AM16's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am18.sh new      these only
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
  bash "$ROOT/scripts/gate/it/am16.sh" | sed 's/^lane_exit=/am16_lane_exit=/' | tee -a /dev/stderr | grep -q '^am16_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestTheBankPaperPrintsEachRowsDescriptionBesideItsAmountsInEnglish)$'
  '^(TestTheLanguageAndEveryTimeAreHeardKeptAndCleared)$'
  '^(TestARequestThatCouldMeanTwoThingsIsAskedBeforeWorkAndAnotherLanguageIsRefused)$'
  '^(TestThePagesOfADeliveredFileAreMadeFromTheFileItselfAndKeptBesideIt)$'
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
