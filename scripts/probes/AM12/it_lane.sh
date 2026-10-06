#!/usr/bin/env bash
# AM12 — the runner's integration lane on the real sandbox (AGENT_MARKET_IT=1): every chunk of the AM10
# lane (the five agents that read files — the engine's form code and the step runner changed under them),
# the AM11 test of forms with no marker, and the tests of a round on the web. In chunks of a few tests (a
# long single run is reaped on this machine when memory is short), against the test database server only
# (scripts/probes/AM7/go_test.sh: CHAT_EDGE_TEST_DSN from .env, never printed).
#
#   bash scripts/probes/AM12/it_lane.sh > results/it.txt          # everything
#   bash scripts/probes/AM12/it_lane.sh web > results/it_web.txt  # the AM11 + AM12 chunks only
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export AGENT_MARKET_IT=1
rc=0
if [ "${1:-all}" != "web" ]; then
  bash "$ROOT/scripts/probes/AM10/it_lane.sh" | sed 's/^lane_exit=/am10_lane_exit=/' | tee /dev/stderr | grep -q '^am10_lane_exit=0$' || rc=1
fi
CHUNKS=(
  '^(TestFormsWithNoMarkerAreReadMappedFilledAndOpenedAgain)$'
  '^(TestAProcurementRoundAsksSearchesOpensChecksAndDraftsFromTheChoice|TestAReadingThatWasTalkedIntoSomethingChangesNothing)$'
  '^(TestTheProcurementSelfTestRunsOnItsBundledPagesAndReachesNothing|TestARoundOnTheWebDoesNothingWhileTheAgentsSwitchIsOff|TestChoosingNoneDeliversTheComparisonAndNoDraft|TestARoundThatFindsNothingToCompareSaysSo)$'
  '^(TestAPurchaseRequestIsDraftedIntoTheOrganisationsOwnForm)$'
)
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 1500 bash "$ROOT/scripts/probes/AM7/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: [^t]' || true
  [ "$st" -eq 0 ] || rc=1
done
echo "lane_exit=$rc"
