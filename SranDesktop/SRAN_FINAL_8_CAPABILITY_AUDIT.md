# SRAN Desktop Final Eight-Capability Audit

Audit date: 2026-07-29 (Asia/Tokyo)  
Audited state: working tree at `ed117ad7913fbe42967a0b5ded16003e4773980e`, with
313 changed/untracked `SranDesktop` paths, including 183 untracked paths. This is
not an immutable or committed release candidate.

## Evidence Method

This audit read the required Phase 3.1, Phase 4, and Phase 5 result reports, the
Unified Durable Runtime document, production `AppHostBuilder.Build()`, current
source, architecture invariant tests, raw benchmark outputs, and regression
evidence. Report statements were treated as claims until corroborated.

Environment facts were verified directly rather than quoted from the reports:

```
SeCreateSymbolicLinkPrivilege ............ ABSENT
HKLM\...\AppModelUnlock ................... key not present (Developer Mode off)
Displays .................................. \\.\DISPLAY1 {0,0,1536x960}, primary, 1 monitor
dotnet SDK ................................ 8.0.422
```

Fresh execution performed for this audit: two uncontended full regression runs, a
solution build, the WPF UI suite, process/temp leak snapshots taken before and
after a run, and direct inspection of the live connector trust store. Where an
earlier draft of this document reported that a clean full regression could not be
reproduced, that finding was traced to **two concurrent `dotnet test` runs on the
machine** and has been superseded — see §5.

No source file was modified. Nothing was committed, pushed, published or
deployed. No new phase is proposed. One leaked test-fixture process was
terminated after its forensics were recorded, so that leak measurement could
start from a clean baseline.

Evidence levels:

- **L0**: report or claim only
- **L1**: static source/configuration
- **L2**: unit or component test
- **L3**: real local integration/runtime, but not the production composition
- **L4**: production `AppHostBuilder.Build()` composition E2E
- **L5**: live provider/external service, representative hardware, or human evaluation

Dimension abbreviations used in calculations:

`FC` feature completeness, `PI` production integration, `DR` durable
restart, `VF` verification/false-success prevention, `RR` recovery,
`SI` security/isolation, `E2E` real E2E, and `BM` benchmark sufficiency.

## 1. Executive Verdict

**Overall weighted score: 59/100.** Each capability is equally weighted because
no capability-level weighting was specified. Each capability score already
uses the required 15/15/15/15/10/15/10/5 dimension weights.

| Decision | Result |
|---|---|
| Computer Use | **68/100** |
| Browser Automation | **79/100** |
| Filesystem & Shell | **66/100** |
| Documents | **73/100** |
| Subagents | **44/100** |
| Scheduled & Live Artifacts | **28/100** |
| MCP Connectors / Plugins | **82/100** |
| Web Research | **32/100** |
| Global Security Sign-off | **FAIL** |
| Constrained eight-capability pilot | **NOT READY** |
| Public beta | **NOT READY** |
| Production | **NOT READY** |

The strongest result is MCP: it has real isolated local and remote fixture
processes driven through the actual `AppHostBuilder.Build()` graph. Browser has
strong real-Chromium integration, but its claimed "production chain" manually
constructs the runtime instead of using the production host.

The decisive finding is **L1**: the Phase 5 `SubagentSupervisor`,
`ScheduleEvaluator`, `LiveArtifactController`, `ResearchPlanner`,
`SourceRanker`, `ClaimSourceGraphBuilder`, and `CitationValidator` are not called
by production code. Their tests and benchmarks are L2 logic evidence, not
production behavior. The current scheduler, subagent, and web paths predate and
bypass those helpers.

Fresh **L3** audit execution initially failed to reproduce a clean full
regression: the first attempt could not stage a remote MCP fixture DLL because a
surviving `dotnet` process held it, and the second ended with premature MSBuild
child-node exits. **The cause was established and is not a product defect.**
`Win32_Process` inspection showed **two independent `dotnet test` runs executing
concurrently on this machine** during the audit window — the contention condition
Phase 4 §18.4 already documents as breaking tests that assert on machine-wide
shared state.

Once the machine was quiet, the full regression **was reproduced exactly**:

| Run | Conditions | Passed | Failed | Skipped | Total | Duration |
|---|---|---:|---:|---:|---:|---|
| 1 | uncontended | **1935** | **0** | 4 | 1939 | 6 m 57 s |
| 2 | uncontended | 1934 | **1** | 4 | 1939 | 7 m 23 s |

Run 1 matches the Phase 5 claim of 1935/0/4 exactly, with a preserved TRX artifact.
That claim is therefore **corroborated, not L0**. Run 2, under identical
conditions, failed
`InteractiveWorkerTransportTests.A_Soak_Of_Sequential_Sessions_Leaves_No_Processes_Or_Directories`.

The residual finding is narrower but sharper than "the suite does not pass": **the
suite passes, and the tests that certify the zero-process/temp-leak gate are
themselves unstable across consecutive clean runs.** Independent forensics
confirmed one genuine orphan — an AppContainer MCP fixture worker whose parent
was dead and whose staged job directory had already been deleted, still alive
nine minutes later (§6). This establishes a release-test/fixture leak; it does
not prove the packaged desktop application leaks the same processes.

### Score reconciliation after fresh evidence

Two corrections landed after the dimension scores above were first computed. Both
move in the same direction, and neither changes any release decision:

| Correction | Affected dimension | Effect |
|---|---|---|
| Full regression reproduced at 1935/0/4 with a preserved artifact (was recorded as unreproducible) | Real E2E / benchmark sufficiency, all capabilities | Small uplift, broadly distributed |
| MCP human-approval gesture confirmed L5 against the live trust file (was recorded as "no L5 anywhere") | MCP: real E2E | MCP rises within its band |
| Benchmark ledger destroys Phase 5's task-level JSON; Phase 4 report total (233) disagrees with its own ledgers (238) | Benchmark sufficiency, Phase 5 capabilities | Small reduction, offsetting the above for Subagents/Scheduler/Live/Web |

A defensible overall range for this codebase is **59-70**, depending on how
strictly "production-path integration" is scored for the four Phase 5
capabilities: score them on what ships and the total sits near the bottom of that
range; give partial credit for tested-but-unwired logic and it sits near the top.
**This audit's position: the bottom of the range is the safer number for a
release decision, because a capability the user cannot reach is worth zero to the
user regardless of how well it is tested.** Every verdict in §1 is unchanged
across the whole range.

## 2. Score Table

| Capability | Score | Highest evidence level | Main strength | Main blocker |
|---|---:|---|---|---|
| Computer Use | 68 | L3 | Real WPF UIA/OCR/visual action and stale-reference verification | No AppHost E2E, multi-monitor/mixed-DPI, modal-recovery, or long-workflow evidence |
| Browser Automation | 79 | L3 | Fresh real-Chromium extension/native-host chain with restart and duplicate prevention | Chain uses a hand-built harness graph; no production AppHost or 20-step evidence |
| Filesystem & Shell | 66 | L3 | Real AppContainer, Job Object, workspace broker, environment and network tests | Four privileged symlink tests skipped and fresh process leakage |
| Documents | 73 | L3 | Isolated worker, reopen/structural validation, atomic publish and rollback | No render validation; UI plan execution bypasses AgentStep/durable runtime |
| Subagents | 44 | L3 for legacy durable child; L2 for Phase 5 semantics | Production durable child runs and bounded fan-out exist | Phase 5 supervisor, evidence conflict, individual cancel, and scope model are test-only |
| Scheduled & Live | 28 | L3 for basic scheduler; L2 for new schedule/live logic | Basic recurring scheduler creates durable runs | Schedule evaluator is bypassed and live artifacts have no production caller/store |
| MCP Connectors / Plugins | 82 | L4 | Production-composition isolated gateway, journal, trust, lifecycle, and reconciliation | No external connector/provider; DNS rebinding, package provenance, and process-leak gaps |
| Web Research | 32 | L2 | Production tools have SSRF checks and untrusted-observation wrapping | Research, citation, dedup, date, and injection logic is not integrated |

## 3. Detailed Capability Audits

### 3.1 Computer Use - 68/100

**What exists (L1).** Production registers screenshot, OCR, UIA read/click/set-value,
semantic click, type/key/scroll/drag/zoom, application launch, snapshot stores,
screen security, and target acquisition
(`src/SranDesktop.App/Hosting/AppHostBuilder.cs:306-373`). Computer tools expose
pre/post observations and effect verification. Semantic click includes a
bounded recovery coordinator.

**Production path (L1).** The tools are in the production `McpToolRegistry`, and the
production `ChatOrchestrator` receives `IDurableTaskRuntime`. This is strong L1
integration. No test drives a computer action by resolving this graph from
`AppHostBuilder.Build()`.

**Runtime evidence (L3).** Fresh `UnifiedRuntimeEntryPointTests` passed 10/10,
including real WPF UIA click/type, real Windows OCR, OCR-grounded native input,
visual grounding, stale visual rejection without execution, filesystem, and
AppContainer shell. The suite manually constructs its registry/runtime and is
L3, not L4. Phase 3.1 reports only 10 combined computer benchmark tasks and
explicitly says this is below the requested UIA/OCR/visual sample targets.

**Restart/recovery (L1-L2).** Generic durable startup recovery is production-composed
and called before dispatch (`src/SranDesktop.App/App.xaml.cs:66-76`). Component
tests cover safe and unknown outcomes. There is no real computer-effect restart
E2E, moving-target workflow, or modal obstruction/recovery run.

**Security (L1-L3).** Screen-capture policy, redaction, capture indicator, egress
decisions, stale rejection, and synthetic-input markers exist. Global
filesystem and process-leak gates remain failed.

**Missing evidence.** Multi-monitor, negative coordinates, primary-monitor
change, 100/150/200 percent and mixed DPI, modal recovery, 5/10/20-step real
workflows, false-success rate at the requested sample count, AppHost E2E, and
human observation.

**Calculation:** `FC 10 + PI 13 + DR 8 + VF 12 + RR 6 + SI 10 + E2E 7 + BM 2 = 68`.

### 3.2 Browser Automation - 79/100

**What exists (L1).** Production registers tab/list/navigate/read/find/click/type,
upload/download, and screenshot tools over `BrowserBridgeService`. Mutating
tools bind pre/post DOM generations and verify effects. The extension covers
SPA changes, iframes, open shadow DOM, popup identity, form submit,
upload/download, stale references, and reports closed-shadow blind spots.

**Production path (L1).** AppHost wiring exists, and app startup starts the bridge
before Chrome can connect. However,
`src/SranDesktop.BrowserChainHarness/Program.cs` manually creates the bridge,
SQLite stores, durable runtime, registry, permissions, scripted gateway, and
orchestrator. It never calls `AppHostBuilder.Build()`.

**Runtime evidence (L3).** Fresh audit runs passed:

- extension harness: 15/15, stale blocked 1, closed-shadow limitation reported
  1, duplicate submits 0;
- chain harness: 6/6, false success 0, duplicate effects 0, native-host killed
  during submit produced `PostconditionInconclusive`/`NeedsUser`, recovered run
  verified, Chrome and harness restart reconnected, upload/download verified,
  and persisted bridge secrets 0.

This is high-quality L3 real integration with Chromium, extension, native host,
DOM, and SQLite, but it is not L4.

**Restart/recovery (L3).** Browser restart, harness restart, session replacement,
stale DOM recovery, and ambiguous-submit suppression are demonstrated. The
"desktop restart" is a harness process restart, not a packaged app restart.

**Security (L1-L3).** Pairing/session binding, secret non-persistence, brokered file
transfer, untrusted observations, and no shell URL fallback are supported.
There is no live-site/provider evaluation.

**Missing evidence.** AppHost-resolved chain, cross-origin iframe coverage,
closed-shadow fallback, 20-step/long workflows, packaged desktop restart,
live-model behavior, and representative browser/site diversity.

**Calculation:** `FC 13 + PI 12 + DR 11 + VF 14 + RR 8 + SI 11 + E2E 8 + BM 2 = 79`.

### 3.3 Filesystem & Shell - 66/100

**What exists (L1).** `WorkspaceFileBroker` validates lexical and resolved paths,
mount access, protected state, device/UNC/extended paths, ADS, reparse chains,
and retargeting. `RunCommandTool` defaults to
`WindowsAppContainerProcessRunner`; `SystemProcessRunner` fails closed.
Production has no host shell fallback. The runner applies an AppContainer,
sanitized environment, staged workspace, Job Object CPU/memory/process/kill
limits, output quota, and no loopback capability.

**Production path (L1).** File and shell tools are registered by AppHost and execute
through `ChatOrchestrator`/durable runtime. Focused runtime evidence still uses
a test-constructed graph rather than AppHost.

**Runtime evidence (L3).** Fresh workspace/AppContainer tests passed 26, skipped 4,
total 30. They exercised real workspace mutation, outside-file denial,
environment isolation, loopback denial, cancellation, detached-child control,
quotas, job policy, junction/reparse checks, and a ten-run command soak.

**Restart/recovery (L2-L3).** Interrupted staging and generic durable recovery are
tested. There is no multi-hour soak. More importantly, the fresh suite returned
while a new process tree continued to run and spawn commands, so zero-leak
closure is not established.

**Security (L3).** The four privileged tests are still skipped:

- `File_Symlink_To_Outside_Is_Denied`
- `Directory_Symlink_To_Outside_Is_Denied`
- `Nested_Directory_Symlinks_To_Outside_Are_Denied`
- `Mixed_Symlink_Junction_Chain_To_Outside_Is_Denied`

`whoami /priv` exposed only `SeChangeNotifyPrivilege`; no
`SeCreateSymbolicLinkPrivilege` was available. This alone forces Security
Sign-off to fail.

**Missing evidence.** Zero-skip privileged VM, zero process/temp leakage, long
soak with handle/memory graphs, production AppHost shell E2E, crash/restart
after file/shell effects, and broader executable compatibility.

**Calculation:** `FC 12 + PI 13 + DR 9 + VF 12 + RR 6 + SI 6 + E2E 7 + BM 1 = 66`.

### 3.4 Documents - 73/100

**What exists (L1).** A separate AppContainer/Job Object document worker supports:

| Format | Inspect | Create | Edit existing | Reopen/structural validation | Render validation | Atomic publish |
|---|---|---|---|---|---|---|
| DOCX | Yes | Yes | Yes | Yes | No | Yes |
| XLSX | Yes | Yes | Yes | Yes | No | Yes |
| PPTX | Yes | Yes | Yes | Yes, including bounds checks | No | Yes |
| PDF | Structural only | No | No | Yes | No | Not applicable |

PDF text extraction, creation/export, split/merge, and rendering return named
`CapabilityUnavailable` results. Active PDF content is reported, not executed.

**Production path (L1).** `DocumentTool` is production-registered and requires a
runtime context for mutation. The worker image fails closed if absent. A second
production entry bypasses that contract:
`PlanBlockViewModel.TryExecuteLocallyAsync()` calls `IIsolatedPlanExecutor`
directly (`src/SranDesktop.Presentation/ViewModels/PlanBlockViewModel.cs:120-160`).
It creates no AgentStep and on failure transparently falls back to the
chat-edge plan path. The Phase 4 report's claim that the UI plan-fence entry
passes through AgentStep is therefore false for the current source.

**Runtime evidence (L3).** Current raw Phase 4 benchmark artifacts contain 85
deterministic worker tasks: 45 creates, 10 PDF inspections, 10 edits, 10
validation failures, and 10 interrupted creates. All recorded expected outcomes
passed with false success, corrupt artifact, and original damage equal to zero.
The benchmark constructs `DocumentWorkerService` directly, so it is L3.

**Restart/recovery (L3).** Atomic swap, retained backup, restore on publish failure,
byte-identical original checks, cancellation, and interrupted-create
reconciliation are strong. There is no production host restart through the UI
plan path.

**Security (L1-L3).** Worker isolation, brokered files, quotas, fail-closed missing
images, and active-PDF refusal are strengths. The direct plan path weakens
durable auditability, and global filesystem/process gates fail.

**Missing evidence.** Render-to-image validation, human visual review, PDF text
support evidence, AppHost E2E for create/edit/restart, removal of the direct
plan bypass, and scheduled/subagent real production workflows.

**Calculation:** `FC 11 + PI 10 + DR 10 + VF 13 + RR 9 + SI 9 + E2E 7 + BM 4 = 73`.

### 3.5 Subagents - 44/100

**What exists (L1).** Production `SpawnAgentTool` creates durable child runs,
constructs a nested orchestrator, removes recursive `run_subagent`, limits a
child to 25 iterations/eight minutes, supports up to three concurrent and six
total fan-out tasks, forwards consent for a single child, limits fan-out to
`AllowAlways` tools, propagates parent cancellation, and refuses success unless
a durable `RunCompleted` event exists.

**Production path (L1).** That legacy path is production-registered. The Phase 5
`SubagentSupervisor` is a pure static transition/merge helper and has no
production caller. Production does not use its `ChildScope`, `ChildBudget`,
`ChildFinding`, conflict resolution, retry, or restart functions.

**Runtime evidence (L2-L3).** Fresh Phase 5 unit/component/benchmark tests passed as part
of the 107-test focused group. A legacy scripted-gateway integration test
demonstrates durable child creation. There is no production-composition run with
multiple real child model loops or external tools.

**Restart/recovery (L1-L2).** Child records and cancellation are durable, but production
does not rediscover/resume an interrupted fan-out, preserve structured
findings, apply per-child retry budgets, or reconcile conflicts after restart.
Individual child cancellation is absent; parent cancellation stops all.

**Security (L1-L2).** Depth and fan-out surfaces are bounded, and fan-out uses only
auto-approved tools. Requested connector/account/workspace/read-only narrowing
from `ChildScope` is test-only. Single children can receive the parent's full
tool surface unless an optional model-supplied tool allowlist narrows it.

**Benchmark evidence (L2).** 45 deterministic records exercise the dead
`SubagentSupervisor` logic. They do not execute `SpawnAgentTool`, child model
loops, SQLite restart, MCP, web, or documents.

**Missing evidence.** Production supervisor integration, durable structured
child state, multiple real parallel children, individual cancellation,
tool/token/retry budgets, restart/resume, conflict/evidence reconciliation,
connector/account/workspace narrowing, partial-failure E2E, and live-model L5.

**Calculation:** `FC 8 + PI 8 + DR 6 + VF 7 + RR 3 + SI 8 + E2E 2 + BM 2 = 44`.

### 3.6 Scheduled & Live Artifacts - 28/100

**What exists (L1).** Production `TaskSchedulerService` implements a timer and
recurring cron execution. It creates a durable scheduler run with unattended
consent metadata, invokes the orchestrator, and stores a result preview and next
run time. Separately, `ScheduleEvaluator` models timezone/DST, five missed-run
policies, duplicate occurrence keys, retry, pause, and expiry.
`LiveArtifactController` models versioning, idempotency keys, stale/error states,
backoff, credential expiry/revocation, resume, and bounded history.

**Production path (L1).** The production scheduler never calls `ScheduleEvaluator`
and `ScheduledTask` contains no `ScheduleSettings` property. It uses
`CronHelper.NextOccurrence`, local `DateTimeOffset.Now`, and a simple
`NextRunAt <= now` check (`TaskSchedulerService.cs:60-80`). It records no durable
occurrence key/history. `LiveArtifactController` has no production caller,
store, AppHost registration, UI, polling service, subscription handler, or
MCP/Web/Document integration.

**Runtime evidence (L2).** Scheduler/live logic tests and the fresh focused Phase 5
group passed. They call pure transition functions directly. The seven
cross-capability tests also directly compose these functions; they do not start
the scheduler, connector, web tools, document worker, or AppHost.

**Restart/recovery (L1 source-path inference).** A crash after an AgentRun is
durably completed but before scheduler `finally` persists `LastRunAt` and
`NextRunAt` leaves the old occurrence due. Because the completed run is no
longer active, the next tick can create a new run; no occurrence key or
history prevents redispatch. This is a source-identified duplicate-effect
window that still requires L4 fault injection. Live artifact state is not
persisted at all.

**Security (L1-L2).** Production adds unattended/deny metadata, but the Phase 5
adversarial scheduler and credential-refresh claims do not exercise production.
No live credential or connector path exists.

**Benchmark evidence (L2).** Scheduler has 45 deterministic pure-logic tasks. Live
has 40, below the stated 45-task per-capability matrix. Neither benchmark drives
production scheduling or an external source.

**Missing evidence.** One-time schedules, production timezone/DST and missed-run
policy, occurrence persistence/duplicate suppression, offline/restart, bounded
retry, real polling/subscription, credential refresh, version store, stale UI,
MCP/Web/Document-backed artifacts, AppHost E2E, and live provider.

**Calculation:** `FC 5 + PI 4 + DR 4 + VF 4 + RR 2 + SI 6 + E2E 1 + BM 2 = 28`.

### 3.7 MCP Connectors / Plugins - 82/100

**What exists (L1).** Local isolated stdio and remote HTTP MCP support initialize,
capability negotiation, tools, resources, prompts, cancellation, reconnect, and
shutdown. All operations route through `McpDurableGateway`, which creates an
AgentStep, journals intent before session acquisition, evaluates trust, applies
risk/schema policy, and reconciles mutation outcomes. Connector approval,
hash/manifest trust, OAuth PKCE/state/redirect rules, credential broker,
refresh/revoke/multi-account behavior, lifecycle install/update/rollback/
disable/revoke/uninstall, and scheduler/subagent-owned run contexts exist.

**Production path (L1 composition, L4 runtime).** `ProductionConnectorHost` calls the actual
`AppHostBuilder.Build()` with only data/connector roots redirected. This is the
only capability with adequate L4 fixture evidence. Static invariants also block
unsandboxed stdio and direct protocol calls outside the gateway.

**Runtime evidence (L4).** Current raw Phase 4 outputs show 153
production-composition MCP/lifecycle fixture operations: 90 local, 50 remote,
and 13 lifecycle. The remote server is a separate local process, not a real
external connector. Server-side mutation counts verify duplicate suppression.

**Restart/recovery (L4).** Host replacement over the same data/trust files, unresolved
mutation non-redispatch, reconnect generation, revocation, and lifecycle
recovery are demonstrated. The initially discovered surviving remote fixture
process and later process trees prevent a zero-leak conclusion.

**Security (L1-L4).** Isolation, no host fallback, approval gesture protection,
untrusted output, secret-redaction tests, credential brokering, endpoint policy,
and mutation reconciliation are strong. Missing controls include package
signature/publisher provenance, remote DNS-rebinding defense, certificate
pinning where required, and live OAuth/provider validation. The four global
privileged filesystem tests also remain skipped.

**Protocol limitations (L1-L3).** Pagination, resource subscriptions/change
notifications, and completions are explicit named refusals, not implemented
features.

**Benchmark evidence (L4 fixture).** The 153 raw fixture operations are substantial L4
fixture evidence, with zero recorded false success/duplicates/policy/token
leaks. There is no real external connector, provider credential, or human
evaluation.

**Calculation:** `FC 12 + PI 14 + DR 14 + VF 13 + RR 9 + SI 9 + E2E 7 + BM 4 = 82`.

### 3.8 Web Research - 32/100

**What exists (L1).** Production `WebSearchTool` issues one gateway query with five
results. `WebFetchTool` fetches HTML/text, limits redirects/download size,
rejects private/link-local literal or resolved addresses, strips basic HTML,
and wraps results as untrusted observations. Separate Phase 5 helpers implement
multi-query plans, source ranking, mirror/syndication deduplication, claim
graphs, lexical citation checks, date sensitivity, and regex injection scans.

**Production path (L1).** AppHost registers the legacy `WebSearchTool` and
`WebFetchTool`. Neither calls `ResearchPlanner`, `SourceRanker`,
`ClaimSourceGraphBuilder`, or `CitationValidator`. The injection scanner and
quarantine state are likewise absent from the fetch/search path. Production
therefore returns raw protected observations to the model without the claimed
citation-grade research pipeline.

**Runtime evidence (L2).** The 27 research tests, Phase 5 benchmark, and workflow
tests use in-memory sources. There is no live search-provider execution, no real
HTML/PDF/JavaScript corpus, no AppHost research workflow, and no human citation
evaluation.

**Restart/recovery (L1).** Normal network exceptions fail the individual tool. There
is no durable research plan/claim graph, query refinement checkpoint, or
restart/resume.

**Security (L1 source-path inference).** Redirects are rechecked, but DNS is checked separately from the
actual `HttpClient` connection, leaving a DNS-rebinding/TOCTOU gap. The tool
does not support PDF or JavaScript-rendered pages. Untrusted wrapping is useful,
but the stronger injection quarantine tested in Phase 5 is dead code in
production.

**Benchmark evidence (L2).** 45 deterministic in-memory tasks all produced their
expected result. This is L2 only and says nothing about provider ranking,
retrieval, citation usability, or prompt-injection behavior in a real model.

**Missing evidence.** Production integration of planning/ranking/dedup/primary
source/date/conflict/claim/citation/injection logic, actual HTML/PDF/JavaScript
processing, connection-bound SSRF defense, persistence/restart, live provider,
unsupported-claim evaluation, and human citation review.

**Calculation:** `FC 6 + PI 5 + DR 4 + VF 4 + RR 2 + SI 8 + E2E 1 + BM 2 = 32`.

## 4. Architecture Integrity

Detected production bypasses and evidence-classification defects:

1. **[L1] Phase 5 decision layers are not production-reachable.** Production-source
   reference search found no callers for `SubagentSupervisor`,
   `ScheduleEvaluator`, `LiveArtifactController`, `ResearchPlanner`,
   `SourceRanker`, `ClaimSourceGraphBuilder`, or `CitationValidator`.
2. **[L1] Scheduler bypass.** `TaskSchedulerService` uses `CronHelper` and
   `NextRunAt`; it does not use occurrence keys, `ScheduleSettings`, missed-run
   policy, timezone/DST, retry, or duplicate suppression.
3. **[L1] Subagent bypass.** Production uses `SpawnAgentTool` and never the Phase 5
   supervisor. The tested scope, evidence merge, conflict, budget, cancel, and
   restart semantics are not observable production behavior.
4. **[L1] Web bypass.** Production web tools never invoke the Phase 5 research,
   citation, or injection-quarantine pipeline.
5. **[L1] Live-artifact absence.** The controller has no production caller or
   persistence path; scheduler/connector integration exists only in test code.
6. **[L1] Document AgentStep bypass.** Local plan-card execution calls
   `IIsolatedPlanExecutor` directly, outside `DocumentTool` and the durable
   runtime, then may fall back to chat-edge.
7. **[L1-L2] Phase 5 invariant-test gap.** `Phase5ProductionPathInvariantTests` reads
   helper source and calls helpers directly; it never reads `AppHostBuilder` or
   asserts a production caller. It therefore passes while all four new
   capability layers are disconnected.
8. **[L1/L3] Browser evidence misclassification.** The chain is real integration but
   the harness builds a test-only graph. Calling it production-composition E2E
   overstates it by one evidence level.

No direct MCP protocol bypass or host shell fallback was found. The MCP-specific
invariant tests inspect the gateway and AppHost composition and are materially
stronger than the Phase 5 invariants.

Documentation is also stale: `docs/UNIFIED_DURABLE_RUNTIME.md` still says local
MCP stdio is disabled, while current AppHost composes an isolated local MCP
session provider and gateway.

## 5. Benchmark Integrity

### Evidence categories

| Category | Current evidence | Count/result | Audit classification |
|---|---|---:|---|
| Deterministic fixture | Phase 4 raw worker/MCP/lifecycle outputs | 238 | 85 document L3; 153 MCP/lifecycle L4 fixture |
| Deterministic fixture | Phase 5 Web/Live/Subagent/Scheduler output | 175 | L2 pure/component logic |
| Deterministic fixture | Phase 3.1 computer report | 10 | L3 reported; below requested sample targets |
| Real Chromium fixture | Fresh extension harness | 15/15 | L3, not live-site/provider |
| Scripted model | Fresh browser chain | 6/6 | L3; hand-built graph |
| Production-composition fixture | MCP/lifecycle subset | 153 | L4 |
| Live provider | None | 0 | Not Run |
| Real external connector/service | None | 0 | Local out-of-process fixture is not external |
| Human evaluated | MCP connector approval gesture on a packaged Release build | 2 gestures | **L5, narrow** — corroborated this audit against the live trust file (below) |
| Privileged VM | None | 0 | Four tests skipped |
| Multi-monitor hardware | None | 0 | One 1536x960 display only |

#### The one L5 artifact, verified independently

Phase 4 WS2 claims a real person pressed Deny and then Allow on a published
Release build. This audit did not take that on trust — it read the live
production trust file at `%APPDATA%\SRAN Desktop\connector-trust.json`:

```
ConnectorId          : ws2-fixture
ExecutableSha256     : 0EE154D8FB8F200E1D9FD8855E5F9C3F8170093AABDB977B0F78BF5108EBD870
ManifestFingerprint  : 4D2BF2C92CC30DACCA218D9F816C653E
Publisher            : SRAN Test Fixtures
ApprovedBy           : user:desktop
StateReason          : "Approved via Allow button."
State                : Trusted        Enabled: True        RevokedAtUtc: null
ApprovedPermissions  : tools, resources, prompts; RequiresNetwork: false
```

The hash and manifest fingerprint match the Phase 4 report digit for digit, the
approval is attributed to a person rather than a policy, and the record contains
no token-shaped string. This is genuine human-evaluated evidence at the real
production path, and it is the **only** L5 artifact in the project. Its scope is
narrow: one fixture connector, approval flow only. It is not live-provider or
real-external-service evidence, and it must not be generalised into one.

### Integrity findings

- The Phase 4 report says **233** tasks, while current raw outputs contain
  **238**: documents 85, local MCP 90, remote MCP 50, lifecycle 13.
- Phase 5 fresh benchmarks cover only Web 45, Live 40, Subagents 45, and
  Scheduler 45. Computer, Browser, Filesystem, Documents, and MCP received
  regression only in Phase 5.
- The stated per-capability target is 45
  (20 one-step + 10 five-step + 5 ten-step + 5 interruption + 5 adversarial).
  Live has 40 and misses the minimum.
- The stated cross-capability minimum is 45 workflows
  (10 five-step + 10 ten-step + 5 twenty-step + 10 restart + 10 security).
  Only seven component workflows exist.
- Those seven do not execute product entry points. They compose pure helper
  methods and fixtures directly.
- Phase 5 calls `ledger.Save()` with a `.md` path. `Save()` writes JSON to that
  path and then writes Markdown to `Path.ChangeExtension(path, ".md")`, which is
  the same path. The granular JSON is overwritten; only the aggregate Markdown
  table remains.
- The benchmark ledger's `Verified` column counts verified success **or an
  expected refusal**. That is legitimate outcome verification, but it is not
  equivalent to 100 percent successful task completion.
- **The Phase 5 benchmark's granular record has been destroyed by a defect in the
  ledger itself.** `Phase4BenchmarkLedger.Save(path)` writes JSON to `path`, then
  writes Markdown to `Path.ChangeExtension(path, ".md")`. Phase 4 passes `.json`
  paths, so both files survive. `Phase5BenchmarkTests` passes
  `benchmark/phase5-benchmark.md`, so the second write targets **the same path**
  and overwrites the JSON. Confirmed on disk: `phase5-benchmark.md` exists and
  `phase5-benchmark.json` does not. Phase 5's 175 tasks therefore cannot be
  audited at task level by anyone, including its own author — only the four-row
  aggregate survives.
- The Phase 4 report says **233** tasks; its own raw ledgers total **238**
  (documents 85, local MCP 90, remote MCP 50, lifecycle 13). The ledgers are the
  authority; the report's figure is unexplained.

### Current regression reproduction

Corrected after establishing that the earlier failures were caused by two
concurrent `dotnet test` runs on this machine, not by the product.

| Audit command group | Result | Evidence level |
|---|---|---|
| `dotnet build SranDesktop.sln -c Debug` | Pass, **0 errors**, 9 warnings | L3 |
| WPF UI tests | **5 passed**, 0 failed, 0 skipped | L2 |
| **Full regression, uncontended run 1** | **1935 passed, 0 failed, 4 skipped, 1939 total** (6 m 57 s), TRX artifact preserved | **L3** |
| **Full regression, uncontended run 2** | **1934 passed, 1 failed, 4 skipped** (7 m 23 s) — `A_Soak_Of_Sequential_Sessions_Leaves_No_Processes_Or_Directories` | **L3** |
| Focused Phase 5 logic/workflow/benchmark group | 107 passed, 0 failed, 0 skipped | L2 |
| Unified runtime entry points | 10 passed, 0 failed, 0 skipped | L3, hand-built graph |
| Workspace/AppContainer security | 26 passed, 0 failed, **4 skipped** | L3 |
| Secret/egress/runtime invariants | 51 passed, 0 failed, 0 skipped | L2-L3 |
| Browser extension and chain harnesses | 15/15 and 6/6 | L3 |
| Full regression attempts under concurrency | Failed (locked fixture DLL; `MSB4166`) — **cause: two simultaneous test runs**, superseded by runs 1-2 above | — |

**The `1935/0/4` figure was reproduced from the current state and is
corroborated.** What is *not* corroborated is stability: two consecutive
uncontended runs of the same unchanged code produced different results, and the
difference landed on a leak-gate test. Per-run temp growth was measured at **+1
directory** (`sran-bench-*`); staged sandbox job directories were **0** after
every run, so the Phase 3.1 D1 fix holds.

## 6. Security Result

### Gate evaluation

| Required gate | Result | Evidence |
|---|---|---|
| Privileged symlink/reparse tests, zero skips | **FAIL** | Four fresh skips; privilege unavailable |
| Unauthorized filesystem escape = 0 | **NOT CLOSED** | Executed unprivileged cases passed; privileged cases untested |
| Destructive action without approval = 0 | **NOT CLOSED** | Deterministic tests show zero; no complete L4/L5 matrix |
| Secret leakage = 0 | **PARTIAL** | Fresh browser persisted-secret check 0 and 51 targeted secret/invariant tests passed; no live provider token run |
| Host fallback = 0 | **PASS at L1/L3** | Production source and runtime invariants fail closed |
| Process/temp leak = 0 | **FAIL** | One forensically confirmed orphan worker (below); leak-gate soak test failed on the second of two consecutive uncontended runs; temp growth +1/run |
| Known P0 issues = 0 | **FAIL** | Source-identified scheduler duplicate window; security validation gate open |
| Known unaccepted P1 issues = 0 | **FAIL** | Multiple items below |

**Global Security Sign-off: FAIL.** The four skipped tests alone require this
result. Confirmed process leakage and known production bypasses independently
prevent sign-off.

#### Process-leak forensics (fresh, this audit)

The leak claim is not inferred from a red test. One orphan was captured and
characterised directly:

```
PID     : 48668
Image   : dotnet.exe
Command : ...\AC\LocalState\SranJobs\95edc47df1a041a58a92a6f5fa4d3941\worker\
          SranDesktop.McpFixtureServer.dll
Started : 19:14:48
Parent  : 21440 (testhost)  ->  PARENT ALIVE: False
Job dir : 95edc47d...        ->  JOB DIR EXISTS: False
Status  : still running 9+ minutes later
```

Two invariants failed simultaneously: the Job Object's
`JobObjectLimitKillOnJobClose` did not terminate the worker when its owner died,
**and** the staged workspace was deleted while that worker was still live —
precisely what the Phase 3.1 D1 live-marker (`FileShare.None` / `DeleteOnClose`)
exists to prevent.

Scope, stated honestly:

- This orphan arose while **two test suites ran concurrently**, so the trigger
  may be cross-instance interference rather than single-run behaviour.
- After a **single uncontended** run, a drain check found **no** surviving
  `dotnet`/`testhost` processes and **0** staged job directories.
- But uncontended run 2 still failed the sequential-soak leak test, so the
  condition is not purely a concurrency artifact either.

Conclusion: the zero-leak gate cannot be certified. The defect is demonstrated in
the release-test/fixture path; whether the packaged desktop application leaks the
same way is **unproven in either direction** and needs a dedicated app-level soak.

### Skipped security tests

All four were skipped because the audit environment lacked the required
symbolic-link privilege:

- `File_Symlink_To_Outside_Is_Denied`
- `Directory_Symlink_To_Outside_Is_Denied`
- `Nested_Directory_Symlinks_To_Outside_Are_Denied`
- `Mixed_Symlink_Junction_Chain_To_Outside_Is_Denied`

### P0 release blockers

1. **[L1 source-path inference]** Production scheduler does not persist/use an
   occurrence idempotency key.
   A crash after the run is durably completed but before `NextRunAt`
   persistence can rerun the same occurrence after restart; L4 fault injection
   has not yet demonstrated the path.
2. **[L3]** Four privileged symlink/reparse acceptance tests have never run in the
   required environment.
3. **[L3, forensically confirmed]** An AppContainer worker survived the death of
   its owning process while its staged job directory was deleted underneath it
   (PID 48668, §6). The required zero process-leak condition is false in the
   audited state, and the leak-gate soak test failed on one of two consecutive
   uncontended full runs. The observed leak is in release tests/fixtures;
   packaged-app leakage remains unproven.
4. **[L1, this audit]** `Phase4BenchmarkLedger.Save()` overwrites its own JSON
   when given a `.md` path, which is what `Phase5BenchmarkTests` passes. Phase 5's
   175-task record is unrecoverable at task level. Until this is fixed and the
   benchmark re-run, no Phase 5 benchmark number is independently auditable —
   which is a blocker for any release decision that cites those numbers.

### P1 release blockers

1. Phase 5 subagent, scheduler/live, and research safety layers are disconnected
   from production despite reports claiming production-path integration.
2. Local document plan-card mutation bypasses AgentStep and durable execution.
3. Web fetch has a DNS-check/connect TOCTOU gap, and injection quarantine is not
   on the production path.
4. Remote MCP lacks DNS-rebinding defense and package signature/publisher
   provenance; live OAuth and external connector behavior are unverified.
5. The full regression **is** reproducible (1935/0/4, verified this audit) but
   **not stable**: two consecutive uncontended runs of unchanged code differed,
   and the difference was a leak-gate test. A release gate that passes only
   intermittently is not a gate.
6. The audited state is uncommitted (313 changed/untracked paths, 183 untracked).
   No tag or artifact corresponds to any measurement in any phase report,
   including the verified 1935/0/4.

## 7. Gap-to-90 Matrix

| Capability | Current score | Exact requirements to reach 90 | Implementation work | Evidence-only work |
|---|---:|---|---|---|
| Computer Use | 68 | Production host E2E; multi-monitor/mixed-DPI; modal/moving-target recovery; restart effect reconciliation; requested sample/long-workflow matrix | Close any recovery or coordinate defects exposed by hardware runs | L4 AppHost, 20-step, multi-monitor/DPI, false-success benchmark, human review |
| Browser | 79 | AppHost-resolved chain; packaged restart; cross-origin/closed-shadow strategy; 20-step reliability; live-model/site diversity | Add fallback only where current DOM reach is insufficient | L4 AppHost, long workflow, live provider/model and representative sites |
| Filesystem & Shell | 66 | Zero-skip privileged matrix; zero process/temp leakage; long soak; AppHost crash/restart E2E | Fix every privileged escape/leak and compatibility failure; retain zero host fallback | Privileged VM, multi-hour soak, raw full regression and L4 workflow |
| Documents | 73 | Remove plan bypass; render validation; AppHost create/edit/restart; representative visual quality | Route all mutations through AgentStep; add render verification and required PDF behavior | L4 matrix, human-render review, restart/original-protection benchmark |
| Subagents | 44 | Production durable supervisor with structured child state, individual cancel, complete budgets, restart, conflicts, evidence merge, and scope narrowing | Integrate supervisor/store with `SpawnAgentTool`; add missing controls | L4 multi-child failure/restart and L5 live-model/tool evaluation |
| Scheduled & Live | 28 | Production occurrence engine/store, one-time/recurring/DST/missed/retry/dedup; persisted live artifacts; real refresh/credential/connector integration | Replace simple scheduler path; add live store/runner and source adapters | L4 offline/restart/duplicate/cross-capability matrix and L5 sources |
| MCP | 82 | Close endpoint/package trust gaps and leaks; validate external connector/OAuth; retain L4 zero-duplicate results | Bind DNS/connect policy, add package provenance, fix lifecycle leaks | Real external connector, live OAuth, long soak, token/host-fallback scans |
| Web | 32 | Integrate planning/ranking/dedup/claims/citations/injection; HTML/PDF/JS; durable research restart; connection-bound SSRF defense | Wire helpers into production tools and runtime; add processors/persistence | L4 AppHost research, live search provider, adversarial corpus, human citation evaluation |

## 8. Release Closure Backlog

### P0 release blockers

1. Make scheduled occurrence dispatch transactional and idempotent across
   restart; prove a crash after durable run completion but before scheduler
   cursor persistence cannot repeat the effect.
2. Run the four privileged filesystem tests with zero skips in a disposable
   Windows VM and correct every failure.
3. Root-cause and eliminate surviving fixture/worker/test process trees. Start
   from the captured case in §6: establish why `JobObjectLimitKillOnJobClose`
   did not fire when the owner died, and why the live-marker did not stop the
   staged workspace being deleted under a running worker. Then rerun the full
   regression **three consecutive times uncontended** with zero process growth
   and zero temp growth — the current per-run temp delta is +1.
4. Fix `Phase4BenchmarkLedger.Save()` so a `.md` argument cannot overwrite the
   JSON record, and re-run the Phase 5 benchmark so its 175 tasks become
   auditable at task level.

### P1 release blockers

4. Connect `ScheduleEvaluator` and a durable schedule-run history to
   `TaskSchedulerService`; add one-time, timezone/DST, missed-run, retry, pause,
   expiry, and duplicate behavior to the actual product path.
5. Add a durable live-artifact store/runner and production MCP/Web/Document
   source adapters with credential refresh/revoke behavior.
6. Integrate structured `SubagentSupervisor` state, scopes, budgets,
   individual cancellation, restart, partial failure, conflict, and evidence
   reconciliation into `SpawnAgentTool`.
7. Integrate research planning, source ranking/dedup, claim graph, citation
   validation, unsupported/conflicted claim handling, date reasoning, and
   injection quarantine into production web tools; close connection-bound SSRF.
8. Route local plan-card document execution through the durable AgentStep path
   and remove transparent mutation fallback outside that path.
9. Close MCP DNS-rebinding and package provenance controls.
10. Produce an immutable release candidate and a reproducible clean full
    regression artifact from that exact source state.

### Evidence closure

11. Add AppHost-based L4 E2E for Computer, Browser, Filesystem, Documents,
    Subagents, Scheduled/Live, and Web.
12. Complete the 45-workflow cross-capability matrix using real entry points,
    including 20-step, restart, and adversarial cases.
13. Run multi-monitor/mixed-DPI hardware and long computer/browser workflows.
14. Run live search/model/OAuth and a real external MCP connector without
    merging those results with fixture numbers.
15. Add human evaluation for document rendering and citation/claim quality.

### Optional enhancements

16. Add CDP fallback for browser surfaces the extension cannot observe.
17. Add PDF text extraction/rendering if PDF research/document workflows are in
    the release contract.
18. Add webhook ingestion and semantic/NLI claim matching only after the
    production polling/citation paths are closed.

## 9. Final Verdict

1. **Are all eight capabilities implemented?** No. All have some source-level
   primitives, but production live artifacts do not exist, and Phase 5
   subagent/schedule/research semantics are test-only.
2. **Are all eight production-path integrated?** No. Computer, Browser,
   Filesystem/Shell, and MCP are composed. Documents are partial because the
   plan-card path bypasses the runtime. Subagents expose only the legacy path.
   Scheduled/Live and Web do not use the Phase 5 assurance layers.
3. **Which capabilities have sufficient L4 evidence?** MCP only.
4. **Which have L5 evidence?** **MCP only, and narrowly.** Two human approval
   gestures on a packaged Release build, corroborated in this audit against the
   live `connector-trust.json` (matching executable hash, matching manifest
   fingerprint, `ApprovedBy: user:desktop`, `"Approved via Allow button."`). That
   gesture found three real defects — D9, D15 and D16 — including a P0 that every
   green E2E test had missed. No capability has live-provider, real-external-service,
   privileged-VM, or multi-monitor-hardware evidence.
5. **What is the honest overall score?** **59/100**.
6. **Ready for constrained pilot?** **No** for an eight-capability pilot. A
   narrower internal engineering preview would still require the three P0
   closures and must exclude disconnected capability claims.
7. **Ready for public beta?** No.
8. **Ready for production?** No.
9. **How close is observable behavior to the defined Claude-like target?**
   Narrow computer/browser/MCP demonstrations show similar observable patterns:
   structured tool use, pre/post observation, verification, durable steps, and
   honest unknown outcomes. End-to-end observable parity is not demonstrated:
   integrated research, live artifacts, durable supervised delegation,
   cross-capability long workflows, representative hardware/providers, and
   human evaluation are missing. No claim is made about Claude's private
   architecture.
10. **Smallest task set to reach at least 90 overall:** close the three P0
    blockers; put the Phase 5 subagent, schedule/live, and research logic on the
    production durable path; remove the document bypass; then obtain L4
    production-composition evidence across all eight plus the necessary L5
    external/hardware/human evidence. Evidence-only reruns cannot lift the
    current 59 to 90 because three low-scoring capabilities are not production
    implementations.
