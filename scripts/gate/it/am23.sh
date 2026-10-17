#!/usr/bin/env bash
# AM23 — the integration lane of the card, and from now on the gate's own last lane: AM22's (which runs AM18's,
# AM16's, AM15's and AM13's first), then the file a person asks for, from any agent: a round asked only for a
# kind of file its agent does not make stops before any work and says why; the one neutral line on a working paper
# follows the organisation's switch, and no file or message names a model.
#
#   bash scripts/gate/it/am23.sh          AM22's lane, then these (what scripts/gate.sh it runs last)
#   bash scripts/gate/it/am23.sh new      these only
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
  bash "$ROOT/scripts/gate/it/am22.sh" | sed 's/^lane_exit=/am22_lane_exit=/' | tee -a /dev/stderr | grep -q '^am22_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestARoundAskedOnlyForAFileItsAgentDoesNotMakeStopsBeforeAnyWorkAndSaysWhy)$'
  '^(TestTheNeutralLineOnAWorkingPaperFollowsTheOrganisationsSwitch)$'
  '^(TestABankRoundMakesTheReportTheDeckAndThePDFOfItsWorkbookWhenAsked)$'
  '^(TestADeckOfMoreItemsThanSlidesHoldIsAskedAboutBeforeAnyFileIsWritten)$'
  '^(TestWordsTypedWithTheFilesAskForTheFilesAndAnXLSIsAnsweredWithWhy)$'
  '^(TestADeckAskedOfAFinishedRoundIsAskedAboutOnTheRound)$'
)
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 1500 bash "$ROOT/scripts/gate/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: ' || true
  [ "$st" -eq 0 ] || rc=1
  # a chunk that ran nothing is not a chunk that passed
  printf '%s\n' "$out" | grep -q '^--- PASS' || { echo "FAIL chunk ran no test: $c"; rc=1; }
done
echo "lane_exit=$rc"
