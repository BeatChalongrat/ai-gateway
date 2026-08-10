# DESKTOP FIX — PHASE B: Agentic depth — close the last gaps to Claude-Code-class

Phase A gave the desktop durable working state (tool history persists, resume-on-cap, edit_file, shell timeout/cwd, folder-scoped consent, plan tool, soft budget). The Phase A audit named the remaining gaps vs Claude Code: **no true incremental shell streaming (partial-on-timeout only), no parallel sub-agent fan-out (sequential, depth-1), no LSP/semantic edits, no worktree isolation, no cross-session memory.** This phase closes the two that are desktop-only and fully verifiable now — **streaming shell** and **parallel delegation** — plus a cross-session agent memory scratchpad. Desktop-only. Everything here is testable with `dotnet test` (no live infra needed — that's why it's next). Do not regress Phases 1–16 + A. Do NOT `git push`.

Independent PARTs — do/commit separately, each with R0 + tests. `dotnet build` 0/0, `dotnet test` green, existing suites unmodified.

---

## PART 1 — Streaming shell output (see build/test progress live, not partial-on-timeout)

**Phase A state:** `RunCommandTool` now has a configurable timeout (default 300s) and returns partial output + a resumable note on overrun, plus a retained per-conversation cwd (`ShellSession`). But output still only comes back **after the process exits (or times out)** — a 4-minute build shows nothing until it's done. Claude Code streams stdout/stderr as it runs so the agent (and user) see progress and the model can react to early errors.

R0: read the Phase-A `RunCommandTool.cs` fully (how it captures stdout/stderr today — `Process.StandardOutput.ReadToEnd`-style vs async), `ShellSession.cs`, and how tool results surface as events (`AgentToolResultEvent`, `AgentExecutionViewModel`/`ToolExecutionItemViewModel`, `MessageBubble.xaml` tool card).

1. **Stream process output incrementally:** switch to async line-buffered reads (`OutputDataReceived`/`ErrorDataReceived` or async stream reads) so stdout/stderr flow while the process runs. Surface incremental output to the UI tool card (append as it arrives) via a progress channel — reuse the Phase-16-A `ISupportsNestedProgress`/progress-event pattern if it fits, or a similar per-tool-call incremental event. Cap total buffered output (existing 50K-ish cap) and truncate the middle, keeping head+tail, if a command floods.
2. **The model still gets a coherent final result** (full captured output + exit code) as the tool result for its reasoning — streaming is additive UI/UX, the model's `tool` message is unchanged in shape. Long-running commands that exceed the timeout keep the Phase-A partial+resumable behavior.
3. **Live tool card:** the `run_command` card shows output accumulating in real time (like a mini terminal), not a spinner that resolves at the end. Consistent with existing card visuals; collapsible; monospace.
4. Tests: a mock/echo process that emits lines over time → incremental events fire before exit (assert ≥2 progress updates arrive before the terminal result); final result still carries full output + exit code; output cap truncates head+tail; timeout still returns partial+resumable. (Use a small cross-platform test process — a `cmd`/`pwsh` loop or a tiny script — or abstract the process spawn behind an interface so the test injects a fake streaming process; prefer the interface so the test is deterministic and CI-safe.)

## PART 2 — Parallel sub-agent fan-out (real delegation, not sequential helpers)

**Phase A state:** `SpawnAgentTool` runs a nested orchestrator, depth-1, isolated, consent-forwarded, budget raised — but each `run_subagent` call resolves **sequentially inside the parent loop**, so "do these 3 independent subtasks" runs one-after-another. Claude-class delegation fans out independent work concurrently.

R0: read `SpawnAgentTool.cs` (how a nested run is constructed + how the parent orchestrator executes tool calls — is the parent's tool-call loop sequential per call?), `ChatOrchestrator.ExecuteToolCallAsync` (are multiple tool calls in one assistant turn executed one at a time?), the consent-forwarding path, and the depth-1 enforcement.

1. **Allow concurrent sub-agent runs when the model emits multiple `run_subagent` tool calls in one turn:** run them with bounded parallelism (e.g. `Task.WhenAll` with a max-concurrency cap of 2–3 — don't spawn unbounded). Each keeps its own isolated orchestrator, budget, and linked cancellation. Depth-1 stays hard-enforced (a sub-agent still can't spawn sub-agents).
2. **Consent ordering safety (the reason Phase A kept it sequential):** parallel nested runs must NOT interleave consent prompts confusingly. Design: either (a) sub-agents get a restricted, auto-approvable tool set (no consent-requiring tools inside a fan-out — read/search/plan only, which is the common research-delegation case), or (b) serialize ONLY the consent prompts (a lock around `ConsentRequested`) while the rest runs parallel. Pick one, justify; (a) is simpler and safe for the main use case (parallel research/read). State the constraint clearly in the tool description so the model knows what a fan-out sub-agent may do.
3. **Results:** collect all nested final texts, return them to the parent as a combined structured result (labeled per subtask). Live progress: the existing nested-progress child cards (Phase 16 A) should show multiple sub-agents progressing at once under the parent card.
4. **Bounds:** max concurrent (2–3), depth-1, per-sub-agent budget unchanged, parent cancellation cancels all children (linked CTS). Total fan-out can't exceed a sane cap.
5. Tests: two `run_subagent` calls in one turn run concurrently (assert overlap via timing on mocked nested orchestrators, or that both started before either finished); combined labeled result returned; depth-1 still refused; parent cancel aborts all; consent constraint from step 2 enforced (a fan-out sub-agent can't invoke a consent-requiring tool, OR consent is serialized — per your choice).

## PART 3 — Cross-session agent memory scratchpad (survive app restarts, not just turns)

**Phase A state:** tool history now persists per conversation (survives restart within a conversation). But there's no durable **agent scratchpad / learned-facts** store that carries useful context the way Claude's project memory / CLAUDE.md-style notes do — e.g. "this project builds with `dotnet build src/X.sln`", "the user prefers tabs", discovered file locations.

R0: read the Phase-14 file manifest (`ChatViewModel` manifest section — the `## Files` block), how `BuildSystemExtrasAsync` injects system-prompt sections, and `SqliteConversationStore` (is there a per-conversation or per-app key/value store, or add one).

1. **A `remember` / `note` tool** (or extend `update_plan` philosophy): the model can persist short durable notes scoped to the conversation (and optionally a per-workspace-folder scope), stored in SQLite. Small, capped (e.g. 2KB / N notes), size-bounded like the manifest.
2. **Surface the notes** in `BuildSystemExtrasAsync` as a `## Notes` / `## What I've learned` block in the never-trimmed system prologue — so across restarts and long tasks the agent recalls key facts without re-discovering them. Same size-cap discipline as the Phase-14 manifest (never bloat context).
3. Keep it honest: notes are model-authored hints, not guarantees; they're plain text, no PII beyond what the user already shared. This is NOT cross-conversation leakage — scope to the conversation (and optionally the granted folder), not global.
4. Tests: `remember` persists + reloads; the `## Notes` block appears in system extras, size-capped; notes survive a store round-trip (restart simulation); scope isolation (conversation A's notes don't appear in B). EN/TH strings.

---

## Final verification checklist
1. `dotnet build` 0/0; `dotnet test` green (new streaming-shell / fan-out / memory tests + `DictParityTests`; Phase A + earlier suites unmodified).
2. **Capability walk (written):** trace "run a long build and watch it stream, then delegate 3 independent file-analyses in parallel, and remember the build command for next session" — confirm each works: shell output streams live (PART 1), the 3 sub-agents run concurrently with per-subtask results (PART 2), and the build command persists to the `## Notes` block surviving restart (PART 3). State honestly what's now Claude-Code-class vs still short.
3. **Security:** streaming doesn't bypass the output cap or DLP (tool output still flows through the normal masked pipeline); fan-out consent constraint holds (no unapproved side-effecting tools in parallel sub-agents, or serialized consent); memory notes are conversation-scoped, not global, and size-bounded.
4. Summarize changes file by file. Note remaining Claude-Code gaps NOT closed here (LSP/semantic edits, git-worktree isolation for parallel edits, true multi-agent shared workspace) as tracked follow-ups — these need bigger architecture and can be a future phase. This phase's goal: the desktop agent now shows live progress on long commands and can genuinely delegate parallel work — the last desktop-only pieces of Claude-Code-class behavior.
