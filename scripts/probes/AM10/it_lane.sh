#!/usr/bin/env bash
# AM10 — the runner's integration lane on the real sandbox (AGENT_MARKET_IT=1), in chunks of a few tests
# (a long single run is reaped on this machine when memory is short), against the test database server
# only (scripts/probes/AM7/go_test.sh: CHAT_EDGE_TEST_DSN from .env, never printed).
#
#   bash scripts/probes/AM10/it_lane.sh > results/it.txt
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export AGENT_MARKET_IT=1
CHUNKS=(
  '^(TestCleanRunEndToEnd|TestParkRestartResumeWithoutReplay|TestKillMidStepResumesWithNoDuplicateArtifact|TestLateUploadResumesWithoutReparse)$'
  '^(TestBudgetParksTheRunAndItGoesOnWhenRaised|TestLyingModelFallsBackToRules|TestRepromptLimitThenDefault|TestSandboxHasNoNetworkAndNoOtherWorkspace)$'
  '^(TestCustomerWorkbookIsFilledAndRecalculated|TestAChangedPackageNeverOverwritesAReleasedVersion|TestPinnedInstallRunsItsOwnEngineAndUpgradesOnlyWhenItsSelfTestPasses)$'
  '^(TestReviewStampsTheFilesWithTheReviewersName|TestCatalogSamplesAreMadeOnceAtStart|TestInvoiceSelfTestIgnoresTheStrangersOwnTaxID|TestASkippedVATCardLeavesTheInvoiceOnTheListAndOneClickBringsItBack)$'
  '^(TestARoundThatFailsSaysSoInWordsAndCanBeTriedAgain|TestAReminderOnThePOAgentOpensAWaitingRoundThatRunsOnlyWhenTheFilesArrive|TestAReminderWhoseRoundGotNoFileClosesItChargesNothingAndFollowsUpOnce)$'
  '^(TestThePOMatchingSelfTestPassesThroughTheRunner|TestAReconciliationThatTiesSucceeds|TestAReconciliationThatDoesNotTieIsNotCalledASuccess|TestAPairAPersonConfirmedNamesThatPerson)$'
  '^(TestExpenseAuditLeavesEveryFailedRuleToAPerson|TestInvoiceRunRecordsThenCatchesTheDuplicate|TestScanIsReadAgainByTheSelfHostedVisionModelOnly)$'
  '^(TestAnInstallFromBeforeAM2RunsItsOwnAM1Engine|TestReportPDFIsTheConvertedDocxAndPassesItsGate|TestARestartedRunnerReapsWhatItsKilledProcessLeft|TestTheSweepNeverTouchesAStepInFlight)$'
  '^(TestTheBankPaperPrintsEachRowsDescriptionBesideItsAmounts|TestThePOPaperPrintsEachRowsDescriptionBesideItsAmounts|TestTheExpensePaperPrintsEachRowsDescriptionBesideItsAmounts)$'
  '^(TestAFormDroppedIntoARoundIsFilledForThatRoundOnly)$'
)
rc=0
for c in "${CHUNKS[@]}"; do
  echo "== chunk $c"
  out=$(timeout 900 bash "$ROOT/scripts/probes/AM7/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 870s 2>&1)
  st=$?
  printf '%s\n' "$out" | grep -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: [^t]' || true
  [ "$st" -eq 0 ] || rc=1
done
echo "lane_exit=$rc"
