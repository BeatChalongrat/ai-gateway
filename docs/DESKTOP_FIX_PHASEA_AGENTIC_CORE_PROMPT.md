# DESKTOP FIX — PHASE A: Agentic core — make it a real Claude-Desktop-class working agent

The most important desktop phase. A deep agentic-capability audit found that SranDesktop is currently a **competent tool-using chatbot, not a working agent**: it reliably does "read 3 files and summarize / screenshot-click-verify," but it **cannot** sustain "refactor this module → build → read errors → edit 5 files → rebuild" the way Claude Desktop/Code does. This phase closes that gap. Desktop-only (plus its local tools). Do not regress Phases 1–16. Do NOT `git push`.

**Root cause (everything else is downstream of this):** the agent has **no durable working state**. Every turn's tool history (what it read, edited, ran, and saw) is discarded at turn end — `ChatViewModel.BuildGatewayMessages` rebuilds the request from `Messages` using only `Role`+`Content` (audit: ~L1774-1780), while the full `tool_calls`/tool-result chain lives only in the `List<GatewayMessage>` local to one `RunAsync` (`ChatOrchestrator.cs` ~L200-205, 322). Hitting the 25-step / 10-min cap emits a terminal error and abandons the task with nothing saved (`ChatOrchestrator.cs:26-27`, terminal error ~L224-226). So the app **structurally cannot sustain a task longer than one bounded turn** — the exact thing Claude Desktop is defined by.

## Ground rules

- Read every file you touch IN FULL; do each PART's R0 first and answer it in the summary. Verify the current line numbers/schema (code has drifted; the audit refs are approximate).
- Build ON the existing loop — the streaming tool loop, context budgeter (Phase 2), file manifest (Phase 14), MCP client, sub-agent isolation, and error-feedback are GOOD; extend, don't rewrite.
- Security posture may only tighten. PathGuard app-state deny (Phase 10 D1), DLP, and consent gating stay intact. New tools go through the same `PermissionService` path.
- New user-facing strings localized EN/TH (`DictParityTests`). Finish: `dotnet build` 0/0, `dotnet test` green, new tests per PART.

## Priority within the phase
PARTs 1–3 (persistence, edit tool, shell) unlock real multi-step work and are the must-haves. PARTs 4–5 (consent, plan) make it flow and visible. PART 6 (iteration budget) ties it together. Do them in order.

---

## PART 1 — Durable tool history across turns + resume on cap (THE fix)

R0: read `ChatViewModel.BuildGatewayMessages` (how history is rebuilt each turn — confirm it copies only Role+Content), `ChatViewModel.PersistTurnAsync` (what it persists today — confirm only user/assistant text bubbles), `ChatOrchestrator.RunAsync` (the in-turn `messages` list, where `tool_calls`/tool/screenshot messages are appended, and the cap terminal error), and `SqliteConversationStore` (does the `messages` table already have `tool_calls`/`tool_call_id`/`name` columns, or is a migration needed? — the schema may already have them unused). State the exact current persistence state.

1. **Persist the full turn message chain**, not just the two text bubbles: the assistant message WITH `tool_calls`, every `role:"tool"` result, and screenshot vision messages, keyed to the conversation with correct ordering. Reuse the `messages` table (add a migration via the Phase 3 `SqliteMigrator` if the tool columns don't exist). Screenshots: persist a reference/placeholder, NOT the full base64 (they're pruned anyway — Phase 2 `ScreenshotPruner` keeps latest N); store enough to reconstruct the narrative, not the raw image history.
2. **Rebuild the FULL chain in `BuildGatewayMessages`** so a new turn continues with the tool context intact (then Phase 2's budgeter/pruner trims it as usual — it already handles tool/screenshot messages). This is the change that lets "you edited X, the build showed error Z" survive into the next turn.
3. **Resume on cap instead of abandon:** when `RunAsync` hits the iteration/time cap, do NOT just emit a terminal error and discard. Persist the accumulated state and surface a **"Continue" affordance** (a button / the model can be re-invoked) that re-enters the loop with the accumulated `messages`, resuming the task. The cap becomes a checkpoint, not a cliff.
4. **Survive interruption/restart:** because the chain is now persisted per turn, an interrupted/cancelled/crashed turn's completed tool steps are already saved; on reopen, the conversation shows them and the model can continue. Verify a cancelled mid-turn conversation reopens with its tool steps intact.
5. Tests: a turn with tool calls → the tool_calls + tool results persist and reload; `BuildGatewayMessages` on the next turn includes them; cap-hit persists state and the resume path re-enters with the accumulated messages; a cancelled turn's completed steps survive a store round-trip. Do NOT break Phase 2 budgeting (the rebuilt chain still fits the window).

## PART 2 — A real edit tool (string-replace / patch), not just overwrite

**Verified:** the only write primitive is `WriteFileTool` (overwrite whole file). To change one line in a 500-line file the model must re-emit the entire file — token-expensive, error-prone, clobbers concurrent changes. Claude Desktop/Code edit by targeted replacement.

R0: read `WriteFileTool.cs`, `ReadFileTool.cs`, the `IMcpTool` contract, and how tools register in `AppHostBuilder`. Confirm no edit/patch tool exists.

1. **New `EditFileTool` (`edit_file`)**: args `{path, old_string, new_string, replace_all?}`. Behavior mirrors a good coding-agent editor: `old_string` must match EXACTLY and be UNIQUE in the file (else error telling the model to add context) unless `replace_all:true`; on match, replace and write; return a small confirmation (line changed, not the whole file). Go through `PathGuard` (same allowed/granted roots + app-state deny) and the same consent path as `write_file`.
2. **Also add the small filesystem primitives a real agent needs** (each `PathGuard`-scoped + consent-gated as appropriate): `create_directory` (mkdir -p), and a `move_file`/`rename` (or document that these go through `run_command` — but a first-class tool is safer than shelling out). `delete` stays out or is consent-heavy (destructive).
3. Update the system prompt (`ChatViewModel` agent prompt) to tell the model: prefer `edit_file` for changes to existing files, `write_file` for new/whole-file writes — so it doesn't overwrite by default.
4. Tests: `edit_file` unique-match replace; non-unique match → error asking for more context; `replace_all`; miss → clear error; `PathGuard` denies app-state + out-of-root; consent still gates it; a multi-edit sequence on one file works.

## PART 3 — Shell that can actually build/test (timeout + working dir + optional session)

**Verified:** `RunCommandTool.cs` has a **30-second hard timeout** (~L13) then kills the process tree, and each call is a fresh `cmd.exe`/`powershell` (no `cd`/env/venv persistence). A .NET/npm build, test suite, `npm install`, `dotnet restore` routinely exceed 30s → killed mid-run. This alone disqualifies the build-fix-rebuild loop.

R0: read `RunCommandTool.cs` fully (timeout, working dir default, output capture, the destructive blocklist).

1. **Raise/configure the timeout** for long-running commands: a much larger default (e.g. 5–10 min, configurable), OR — better — **stream output and support long-running processes** so a build's progress returns incrementally and the model isn't blocked on a fixed wall-clock. At minimum, make the timeout a setting and default it high enough for real builds; return partial output + a "still running / timed out" status rather than a bare kill so the model can decide.
2. **Working directory continuity:** let the agent set and RETAIN a working directory across `run_command` calls within a conversation (a per-conversation cwd the model can `cd` into), so a multi-step build in a project folder doesn't reset to the user profile each call. Keep `PathGuard` validation on the cwd.
3. Keep stdout+stderr+exit-code feedback (already good — it's what lets the loop react) and the destructive blocklist (defense-in-depth; consent is the real control).
4. Tests: a command that runs >30s completes (or streams) instead of being killed at 30; cwd set in one call persists to the next; stderr+exit code still feed back; blocklist still trips; `PathGuard` still governs cwd.

## PART 4 — Folder-scoped write consent (kill the multi-file fatigue)

**Verified (`ChatOrchestrator.ExtractScope` ~L368-387):** for `FileSystem` tools the consent scope is the **exact raw path string** — so `write_file`/`edit_file` "Always Allow" persists keyed to that one file. Editing a 12-file project = **12 separate consent prompts** even clicking "Always allow" each time. Meanwhile `run_command` (Shell) scope is `"*"` — one "Always Allow" = blanket auto-approval of every future shell command. The balance is wrong in both directions.

R0: read `ExtractScope`, `PermissionService` (how scope keys persist), `FolderGrantValidator`/`PathGuard` (the granted-folder concept from Phase 6).

1. **File writes → folder-scoped consent:** change `ExtractScope` for FileSystem writes to resolve to the **containing granted-folder (or the nearest allowed root)**, not the exact file path. So "Always allow writes under `~/Desktop/myproject`" covers every file in that project — one prompt, then flow. Reads stay `AllowAlways` (no change). Persist at the folder scope.
2. **Shell → tighten from blanket `"*"`:** `run_command`'s `"*"` scope means one approval trusts all future commands. Scope it more meaningfully (e.g. per-working-directory, or at least keep it session-scoped rather than persisted-forever by default), so "Always allow" doesn't hand over the whole shell for all time. Balance against fatigue — a coding agent runs many commands, so per-cwd folder trust (matching the file grant) is the sweet spot.
3. This must not weaken the security model: app-state deny (Phase 10 D1) and PathGuard still apply; folder-scoped consent only reduces prompt COUNT within already-allowed/granted roots, never widens reach.
4. Tests: writing 3 files under one granted folder after "Always allow" → ONE consent, not three; a write outside the trusted folder still prompts; shell "Always allow" is scoped (per-cwd/session) not global-forever; app-state/PathGuard still deny.

## PART 5 — Model-maintained plan / todo (visible progress)

**Verified:** there is NO agentic plan/todo tool and no plan surface (the only "Plan" in the codebase is Office-document generation — unrelated). The UI shows "Working... step 3/25" (a spinner), not a plan the agent works through and checks off. Claude Desktop/Code keep a visible todo list.

R0: read how tool cards / agent status render (`AgentExecutionViewModel`, `MessageBubble.xaml`, the `AgentStatusEvent` path), and the system prompt.

1. **New `update_plan` tool**: the model writes/updates a structured task list `[{step, status: pending|in_progress|done}]`; `DefaultPermission = AllowAlways` (writing a plan is side-effect-free). Store the current plan per conversation.
2. **Render it** as a compact, live checklist in the message area (checked off as the agent progresses) — consistent with the existing tool-card visuals, collapsible. This is the user-visible "what the agent is doing and how far" surface.
3. **Prompt the model** (agent system prompt) to maintain a plan for multi-step tasks: lay out the steps, mark in-progress/done as it works. Keep it optional (don't force a plan for a one-shot answer).
4. Tests: `update_plan` persists + renders; status transitions reflect in the UI; a plan survives the turn (ties to PART 1 persistence); no plan for trivial turns; EN/TH strings.

## PART 6 — Iteration budget: soft cap with continue, and stronger delegation

**Verified:** `DefaultMaxIterations = 25` / `10 min` (`ChatOrchestrator.cs:26-27`) — too low for real work (Claude Code runs hundreds of steps). Sub-agents are 10 steps / 4 min, depth-1, sequential, can't see the conversation, return only final text (`SpawnAgentTool.cs`).

1. **Raise the ceiling and make it a soft budget:** a much higher default step/time budget, and on reaching it, the resume/continue affordance from PART 1 (prompt-to-continue) instead of a hard terminal error. The cap protects against runaway loops but must not guillotine legitimate long tasks.
2. **Strengthen sub-agent delegation:** raise the nested budget (10→higher), and consider parallel fan-out (multiple `run_subagent` calls resolved concurrently) for genuine "delegate these independent subtasks" work — keep depth-1 (no recursion) and the isolation/consent forwarding intact.
3. Tests: cap-hit yields a continue path (not a dead error); a task that needs >25 steps completes across a continue; sub-agent budget raised; depth-1 + consent forwarding still enforced.

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` green (new persistence/edit/shell/consent/plan tests + `DictParityTests`; Phase 2/6/10/14 suites unmodified and green).
2. **Capability walk (written, the headline):** trace a real task — "edit `Program.cs` to add a method, build, read the error, fix it, rebuild" — and confirm each step now works: `edit_file` makes the targeted change (PART 2), `run_command` runs the build past 30s and returns errors (PART 3), the error feeds back and the model edits again (existing loop), folder-scoped consent means it didn't prompt 5 times (PART 4), a plan tracked the steps (PART 5), and if it ran long the tool history persisted + it could continue (PART 1/6). State honestly what now works vs what still doesn't.
3. **Security:** confirm app-state deny + PathGuard + DLP + consent still hold; folder-scoped consent reduces prompt count only within already-allowed roots, never widens reach; new tools go through the same consent path.
4. Summarize changes file by file. Note what's still short of Claude Code (e.g. no LSP/semantic edits, no worktree isolation, no cross-session agent memory files) as tracked follow-ups. This phase's goal is: the app can now sustain a real multi-step working task on the user's machine — not a chatbot that forgets at turn's end.
