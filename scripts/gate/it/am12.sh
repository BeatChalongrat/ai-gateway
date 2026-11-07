#!/usr/bin/env bash
# AM12 — the runner's integration lane on the real sandbox (AGENT_MARKET_IT=1): every chunk of the AM10
# lane (the five agents that read files — the engine's form code and the step runner changed under them),
# the AM11 test of forms with no marker, and the tests of a round on the web. In chunks of a few tests (a
# long single run is reaped on this machine when memory is short), against the test database server only
# (scripts/gate/go_test.sh: CHAT_EDGE_TEST_DSN from .env, never printed).
#
#   bash scripts/gate/it/am12.sh > results/it.txt          # everything
#   bash scripts/gate/it/am12.sh web > results/it_web.txt  # the AM11 + AM12 chunks only
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$ROOT/scripts/gate/it/lib.sh"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "web" ]; then
  bash "$ROOT/scripts/gate/it/am10.sh" | sed 's/^lane_exit=/am10_lane_exit=/' | tee -a /dev/stderr | grep -q '^am10_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestFormsWithNoMarkerAreReadMappedFilledAndOpenedAgain)$'
  '^(TestAProcurementRoundAsksSearchesOpensChecksAndDraftsFromTheChoice|TestAReadingThatWasTalkedIntoSomethingChangesNothing)$'
  '^(TestTheProcurementSelfTestRunsOnItsBundledPagesAndReachesNothing|TestARoundOnTheWebDoesNothingWhileTheAgentsSwitchIsOff|TestChoosingNoneDeliversTheComparisonAndNoDraft|TestARoundThatFindsNothingToCompareSaysSo)$'
  '^(TestAPurchaseRequestIsDraftedIntoTheOrganisationsOwnForm)$'
)
for c in "${CHUNKS[@]}"; do
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
