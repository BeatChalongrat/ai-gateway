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
. "$ROOT/scripts/gate/it/lib.sh"
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
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
