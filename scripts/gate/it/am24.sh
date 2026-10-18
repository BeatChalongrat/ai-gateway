#!/usr/bin/env bash
# AM24 — the integration lane of the card, and from now on the gate's own last lane: AM23's (which runs AM22's,
# AM18's, AM16's, AM15's and AM13's first), then the defect made on purpose (CARD-RULES §6d): the in-house model
# writes a Thai mark twice, and what a person opens is the mended word, with the run's record saying so; a slip with
# no one reading leaves the rules' words.
#
#   bash scripts/gate/it/am24.sh          AM23's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am24.sh new      these only
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
  bash "$ROOT/scripts/gate/it/am23.sh" | sed 's/^lane_exit=/am23_lane_exit=/' | tee -a /dev/stderr | grep -q '^am23_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestAThaiMarkTheModelWritesTwiceIsMendedInTheFileAndRecorded|TestAThaiSlipWithNoOneReadingLeavesTheRulesWords)$'
)
for c in "${CHUNKS[@]}"; do
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
