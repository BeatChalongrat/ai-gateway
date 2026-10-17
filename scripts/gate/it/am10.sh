#!/usr/bin/env bash
# AM10 — the runner's integration lane on the real sandbox (AGENT_MARKET_IT=1), in chunks of a few tests
# (a long single run is reaped on this machine when memory is short), against the test database server
# only (scripts/gate/go_test.sh: CHAT_EDGE_TEST_DSN from .env, never printed).
#
#   bash scripts/gate/it/am10.sh > results/it.txt
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$ROOT/scripts/gate/it/lib.sh"
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
  '^(TestAFormDroppedIntoARoundIsFilledForThatRoundOnly|TestAnInstallThatNeverSaidWhichFilesIsAskedOnceAndRemembered|TestAFormMappedWhileTheRoundRenderedIsFilledWhenItFinishes|TestAnAnswerGivenWhileTheFilesWereMadeIsMadeWhenTheRoundFinishes)$'
)
rc=0
for c in "${CHUNKS[@]}"; do
  run_chunk "$c" || rc=1 # (lib.sh: red when it ran no test, or when a name it lists matched none)
done
echo "lane_exit=$rc"
