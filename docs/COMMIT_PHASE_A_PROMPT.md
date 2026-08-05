# COMMIT — Phase A (agentic core), safely

Phase A (durable tool history, edit_file, shell timeout/cwd, folder-scoped consent, plan tool, soft iteration budget) is built and tested (638 pass, 0/0 build) but is **entirely uncommitted** — HEAD is still Phase 16 (`26f2e6d`/`423df99`), and the new files (`EditFileTool.cs`, `AgentChainCodec.cs`, `ShellSession.cs`, `UpdatePlanTool.cs`, `FolderScopeResolver.cs`, etc.) are UNTRACKED (`??`). Untracked work is one `git clean` from gone and violates the project's "everything in git" rule. Commit it now — no push.

## Rules
- Do NOT `git push`. Commit only.
- Explicit pathspecs — do NOT `git add -A`/`.`. The tree carries unrelated dirty files and known-tracked items to keep OUT.
- Do NOT commit any secret or build artifact. Re-run the sweep below.
- No history rewrite, no amend.

## Step 0 — Survey
1. `git status --porcelain` — list what's modified vs untracked. Identify exactly the Phase A files: the new Application services (`AgentChainCodec.cs`, `ShellSession.cs`, `AgentPlan.cs`, `IToolScopeResolver.cs`), Infrastructure tools (`EditFileTool.cs`, `CreateDirectoryTool.cs`, `MoveFileTool.cs`, `UpdatePlanTool.cs`, `FolderScopeResolver.cs`, rewritten `RunCommandTool.cs`, `ToolHelpers.cs`), Presentation (`ChatViewModel.cs`, `MessageViewModel.cs`, `PlanStepViewModel.cs`, `AgentExecutionViewModel.cs`, `MessageBubble.xaml`, `Dict.cs`), App (`AppHostBuilder.cs`), the modified `ChatOrchestrator.cs`/`AgentStreamEvents.cs`/`UserSettings.cs`/`SpawnAgentTool.cs`, and the 5 new test files + updated tests.
2. **Known item to EXCLUDE:** `admin-ui/.tmp_bugs/` (contains a real committed JWT — a separate security task will purge it; do NOT stage it here, do NOT let it ride along). Confirm it's not in your pathspec.
3. Note any EOL-only "modified" files (`git diff --ignore-all-space --stat` empty for them) — the `.gitattributes` normalizes on commit; fine.

## Step 1 — Secret + artifact sweep on what you'll stage
- `git ls-files --others --exclude-standard SranDesktop/ | grep -iE '\.env|bin/|obj/|\.db$|credentials'` → must be empty (SranDesktop `.gitignore` should already exclude bin/obj).
- Grep the staged Phase A diff for hardcoded secrets (`gw_`/`sk-`/PEM/JWT `eyJ`) — test fixtures with fake values are fine; a real one stops.

## Step 2 — Commit (one clean commit, explicit paths)
Stage only Phase A paths (the SranDesktop source + tests changed/added for Phase A, and `docs/DESKTOP_FIX_PHASEA_AGENTIC_CORE_PROMPT.md`). Verify with `git diff --cached --stat` that: (a) every entry is a Phase A file, (b) NO `.tmp_bugs`, NO `bin/obj/`, NO `.env`, NO `.db`. Then:

`git commit -m "feat(desktop): agentic core — durable tool history + resume, edit_file, shell timeout/cwd, folder-scoped consent, plan tool, soft iteration budget (Phase A)"`

## Step 3 — Verify
- `git status` — Phase A files no longer untracked/modified; `.tmp_bugs` still untracked-or-tracked but NOT in your new commit.
- `git log --oneline -1` + `git show --stat HEAD` — confirm the commit contains exactly Phase A, nothing stray.
- Report the commit sha, the file count, and confirm no secret/artifact/`.tmp_bugs` was included and nothing was pushed.
