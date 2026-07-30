# Unified Durable Agent Runtime

## Scope

This document records the Phase 2 runtime boundary shared by chat, scheduler,
subagent, and tool execution. It does not claim feature parity for every tool.
Capabilities that cannot satisfy the runtime and security contracts must fail
closed or report that they are unavailable.

## Before

```text
Chat ----------> ChatOrchestrator ----------> tools
Scheduler -----> IChatService --------------> model
Subagent ------> nested chat ---------------> tools
Startup -------> UI
```

Execution progress was primarily conversation state. Scheduler and subagent
invocations did not have a common durable run identity, central transition
validation, or restart reconciliation.

## After

```text
Chat / Scheduler / Subagent
              |
              v
       IDurableTaskRuntime
              |
     AgentRunStateMachine
              |
       IAgentTaskStore
       (SQLite + version)
              |
 Plan -> permission/security -> common tool contract
              |
 execute -> observe -> verify -> reconcile
              |
     durable event sequence
              |
       UI progress events
```

`ChatOrchestrator` creates or joins an `AgentRun`, records durable steps, and
uses the same tool lifecycle for validation, preparation, execution,
observation, and verification. Model text cannot mark a tool step successful.

The scheduler creates a scheduler-triggered run before invoking the
orchestrator. The subagent tool creates a durable child run that inherits the
parent workspace, security, permission, and model scopes. Child cancellation is
persisted and parent cancellation cascades to active children.

## Persistence

SQLite migration 9 extends the existing agent task tables with:

- run trigger, intent, goal, security, permission, workspace, model, parent,
  checkpoint, cancellation, failure, final-result, timestamp, and version data;
- step dependency, lifecycle, risk, idempotency, outcome, side-effect,
  verification, failure, attempt, and timestamp data;
- observation source, trust, sensitivity, provenance, use restriction,
  retention, and content-reference data;
- verification records and monotonic event sequence numbers.

Scheduler migration 2 adds a durable `last_run_id`. Scheduler prompt, result,
and name fields use protected storage, with legacy plaintext rows upgraded when
they are read.

Writes use transactions and optimistic version checks. Values pass through the
sensitive-data redactor before persistence. Raw clipboard images, screen images,
and unrestricted tool payloads are not durable runtime fields.

## State And Recovery

All status changes pass through `AgentRunStateMachine`. Terminal states cannot
transition back to active states. Unknown outcomes enter reconciliation and are
never automatically retried.

At application startup, incomplete runs are loaded in deterministic order.
Safe interrupted work transitions through resuming to ready. A step with a
possible side effect transitions to reconciling. Corrupt checkpoints fail the
run instead of starting duplicate work.

Automatic retry is limited to classified transient failures. Policy denials are
not retried. Timeouts or worker crashes after a possible side effect require
reconciliation.

## Security Invariants

1. Runtime tools reuse the workspace broker, sandbox runner, permission engine,
   data-egress policy, observation boundary, and redactor.
2. No shell or Python fallback runs in the privileged desktop process.
3. Sandbox initialization failure blocks execution.
4. External observations retain provenance and cannot grant permission.
5. Screen or clipboard transmission requires a distinct egress decision.
6. An unknown side effect is reconciled before retry.
7. UI progress is derived from persisted runtime events.
8. A stored permission cannot override a workspace, sandbox, egress, screen, or
   connector trust denial.

## Current Migration Coverage

The following execution paths use the durable runtime:

- normal chat;
- workspace file tool execution through chat;
- a read-only sandbox command through chat;
- computer-state observation through chat;
- scheduler-triggered chat;
- subagent child execution.

Browser, document, MCP, web, and live-artifact tools participate when invoked
through `ChatOrchestrator`, but dedicated external entry points that do not yet
exist or have not been migrated must remain unavailable rather than create a
parallel execution path.

## Known Security Gates

Production enablement remains blocked until the privileged Windows filesystem
suite runs without skips in a disposable environment. Windows PowerShell inside
the current AppContainer environment exits during startup; this is a
compatibility limitation and does not cause host execution fallback.

Local MCP stdio is no longer disabled. `AppHostBuilder` composes an isolated
local MCP session provider behind `McpDurableGateway`; the sentence that said
otherwise described a state the code left some time ago.

## Shell Execution Lanes

`run_command` has two lanes, selected by `shell_execution_mode` in user settings
before dispatch:

| Lane | Setting | Isolation | Host tooling |
|---|---|---|---|
| Sandbox | `"sandbox"` (default) | AppContainer, empty capability set, no network, PATH = System32, staged workspace copy, Job Object limits | Unavailable by construction |
| Host | `"host"` | Authorized working directory and the command blocklist only | Available |

The sandbox lane cannot host `docker`, `git`, `node` or `python`. This is not a
gap to be closed by extending PATH: those CLIs are clients of host services
reached over named pipes or sockets, and severing access to host services is the
sandbox's purpose. The two requirements are mutually exclusive, so the product
offers a choice rather than a compromise that satisfies neither.

**The host lane is opt-in and is never a fallback.** A sandbox failure returns an
error; it does not re-run the command on the host. Reaching the host lane by
recovering from a sandbox failure would convert an isolation defect into a
privilege escalation, so the ordering is enforced at source level by
`RuntimeBypassInvariantTests.Host_Lane_Is_Never_Reached_By_Fallback` rather than
left to review.

The earlier invariant "zero host execution" is therefore now stated precisely as
**zero silent host execution**. What changed is the wording's accuracy, not the
protection: no path reaches the host without a prior, explicit user setting.

### Accepted risk

With `approval_mode = "skip"`, host-lane commands run without a prompt. The
exposure is not a careless click but prompt injection — web pages or files the
model has read can dictate the command, and in this configuration nothing
intervenes. This combination is a deliberate user choice. Every host-lane
dispatch is written to the security audit log as
`shell.host_lane_dispatch` before execution, so a command that hangs or crashes
the process still leaves a record.
