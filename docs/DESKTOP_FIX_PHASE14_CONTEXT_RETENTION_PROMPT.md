# DESKTOP FIX — PHASE 14: Context retention (files stop disappearing)

Builds on Phase 2's context management (`ContextBudgeter`, `ScreenshotPruner`, `TokenEstimator`). Desktop-only. Do not regress Phases 1–13.

## The real problem (verified in recon) + how Claude/ChatGPT solve it

Users report attached files and computer-use screenshots "vanish fast." The recon found it's **two distinct causes**:

1. **Cross-turn amnesia (the big one):** `ChatViewModel.BuildGatewayMessages` rebuilds the gateway history **fresh each turn from text bubbles only** (`ChatViewModel.cs:1594-1601` copies `Role`+`Content` string). Tool results (`read_file` output) and screenshot vision messages are **per-turn ephemera** — they never cross turns at all. A file read in turn 1 is simply gone by turn 3; only the attachment *name* baked into the user bubble text survives.
2. **Within-turn trimming:** in one long agent loop, `ContextBudgeter` hard-drops whole oldest groups (`ContextBudgeter.cs:57-60`, including `read_file` results), and `ScreenshotPruner` keeps only the latest 2 (`keepLatest: 2` hardcoded).

**How Claude/ChatGPT handle it:** they don't rely on the file content staying in the transcript. Files uploaded to a session remain **retrievable** — ChatGPT's code interpreter keeps them on a session filesystem the model can re-open; Claude Projects keep files in a knowledge store retrieved on demand. The model always *knows what files exist* and can *re-read them*, even after the raw content has scrolled out of the window. For computer use, both keep only the most recent few screenshots (our approach is right; it just needs tuning + easy re-capture).

**The fix mirrors that:** a persistent **file manifest** at conversation level (the dormant `files` table — `SqliteConversationStore.cs:46-56`, verified: no writers today) surfaced in the never-trimmed system prompt every turn, so the model always sees the file list + paths and can `read_file` them again (paths stay valid on disk). Plus tune screenshot retention, and replace hard-drops with cheap structural summaries.

## Ground rules

- Read every file you touch IN FULL; do R0 first and answer it in the summary.
- Build ON Phase 2; do not rewrite `ContextBudgeter`/`ScreenshotPruner` wholesale. Extend their tests, don't weaken them.
- Keep the manifest SIZE-BOUNDED (a chatty file list must not itself blow the context). No raw file content in the manifest — names + paths + tiny metadata only; the model re-reads on demand.
- New strings localized EN/TH (`DictParityTests`). Finish: `dotnet build` 0/0, `dotnet test` green.

## R0 — Reconnaissance (confirm the recon; answer in summary)

1. `SqliteConversationStore` `files` table + `UpsertFileAsync`/`GetFilesAsync` + `FileAttachment` entity — confirm they exist and have NO production callers today.
2. `ChatViewModel.SendAsync` (~:712-716) where `attachedPaths`/`attachedImagePaths` are known, and `RunTurnAsync` (~:905-948) where they become messages — the populate points.
3. `ChatOrchestrator.ExecuteToolCallAsync` (~:320-325) where `read_file`/`write_file`/screenshot results are appended, and `LogExecutionAsync` (~:400-423) writing `tool_executions` — the other populate point + the existing file-identity trace.
4. `BuildSystemExtrasAsync` (~:1161-1231) — the `## Project` file-listing loop (~:1201-1205) and `TryReadSmallTextFile` (~:1234-1256) as the precedent to mirror for a `## Files` section. Confirm it rides `gatewayMessages[0]` (the never-trimmed system prompt).
5. `ContextBudgeter.FitToBudget` trim loop (~:57-60) and the trim-note re-insertion (~:63-68) — the seam for summarize-instead-of-drop.
6. `ScreenshotPruner.Prune` call sites (`ChatOrchestrator.cs:97,156`, `ContextBudgeter.cs:77`) — where `keepLatest` is passed and where `window` is in scope.

---

## PART A — Conversation file manifest (THE fix for "files disappear")

Give every conversation a durable, model-visible list of its files so nothing is silently forgotten.

1. **Populate the dormant `files` table** (it's the ready-made home):
   - On attach (`ChatViewModel.SendAsync`, where `attachedPaths`/`attachedImagePaths` are known): `UpsertFileAsync` a row per file — `conversation_id`, `name`, `local_path`, `mime_type`, `size_bytes`, `created_at`. Dedupe by (conversation, local_path) so re-sending the same file doesn't duplicate.
   - Optionally on successful `read_file`/`write_file` in `ChatOrchestrator` (a file the agent touched that wasn't user-attached): record it too, so agent-discovered files also persist. R0: decide if this is in scope now or a follow-up; user-attached is the must-have.
   - Best-effort + logged; a manifest write must never fail a turn.
2. **Surface it in the system prompt** (never-trimmed):
   - Add a `## Files in this conversation` section in `BuildSystemExtrasAsync`, mirroring the `## Project` file loop. For each file: name, local path, and tiny metadata (size, "attached" vs "created"). Tell the model plainly: *these files are available; use `read_file` on the path to view current contents even if you don't see them above.*
   - **Size cap:** list at most the N most-recent files (e.g. 20), newest first; if more, note "(+K more)". Never inline content. Keep the whole block well under the safety-margin budget.
   - Because it's in `gatewayMessages[0]`, it survives every trim automatically — no per-iteration re-injection needed.
3. **Re-readability:** the model can already call `read_file` on the listed path; `PathGuard` still applies (paths outside allowed/granted roots are denied — verify the manifest doesn't imply access it doesn't have). If a listed file was moved/deleted, `read_file` fails gracefully — the manifest is a hint, not a guarantee. Note this.
4. Tests: attach two files → both rows in `files`; the system extras contains a `## Files` block listing them; re-sending a file doesn't duplicate; the block is size-capped at N; a conversation with no files has no block; `DictParityTests` green.

## PART B — Adaptive screenshot retention

Keeping only 2 screenshots is too aggressive for large-window models; too many for tiny ones.

1. Add `ScreenshotPruner.KeepLatestFor(int contextWindow)` (pure, tested): map window → keepLatest (e.g. <50K→2, <200K→3, ≥200K→4; clamp 1–5 — pick sensible thresholds, justify). Each screenshot ≈1100 tokens (per `TokenEstimator`), so the count must stay a small fraction of budget.
2. Use it at `ChatOrchestrator.cs:97` where `window` is in scope: `ScreenshotPruner.Prune(messages, ScreenshotPruner.KeepLatestFor(window))`. Leave the recovery/monster-fallback call sites at their aggressive `keepLatest: 1` (those fire under context pressure — correct to be aggressive there).
3. The pruned-placeholder text already tells the model to "take a new screenshot if you need to see the current state" — keep that (it IS the re-capture affordance). Tests: `KeepLatestFor` thresholds; the main loop uses the adaptive value; recovery still uses 1.

## PART C — Structural summary instead of hard-drop (cheap, no extra LLM call)

When `ContextBudgeter` trims an oldest group, replace it with a one-line structural summary instead of deleting outright — so the model retains the *gist* of what happened, not a blank.

1. At the trim seam (`ContextBudgeter.cs:57-60`): before `RemoveRange`, derive a **deterministic** summary from the group's messages (NO LLM call — this runs inside the sync budgeter): e.g. `"[Summary of earlier turn: user asked \"{first ~80 chars of the user message}\"; assistant ran tools: {distinct tool names from tool_calls}; files touched: {read_file/write_file paths}.]"`. Replace the removed range with one `user`-role message carrying that text (mirror the existing trim-note insertion machinery at :63-68).
2. Keep the cumulative trim-note behavior (count of omitted messages) — the per-group summaries are additional, not a replacement for the "N earlier messages omitted" note. Ensure the summaries themselves are counted in the budget (they're tiny) and that repeated trims don't stack unbounded summaries — cap the number of retained summaries (e.g. keep the most recent few structural summaries; older ones collapse into the plain cumulative note). Justify the cap.
3. This is deterministic and cheap — no async, no extra gateway round-trip, fits the sync static method. (An LLM-generated summary is a possible future enhancement; do NOT add an LLM round-trip in the budgeter here — note it as deferred.)
4. Tests: extend `ContextBudgeterTests` — a trimmed tool-call group is replaced by a summary mentioning the user question + `read_file` path (adjust `Tool_Call_Group_Is_Removed_Atomically` / `Over_Budget...` which currently assert hard-drop + exact `OmittedMessages`); the summary is far smaller than the group; the summary cap holds under repeated trims; final group still never trimmed.

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` green (extended ContextBudgeter/ScreenshotPruner tests + new manifest/store tests + `DictParityTests`).
2. **Behavior walk (written):** trace a 3-turn conversation where turn 1 attaches `data.csv` and the agent reads it, then turns 2–3 don't mention it — confirm the model STILL sees `data.csv` in the `## Files` manifest in turn 3 and could `read_file` it again (the exact fix for the reported "files disappear"). And a long computer-use turn: confirm adaptive screenshots + structural summaries keep the gist without blowing budget.
3. **Budget safety:** confirm the manifest + summaries are size-bounded and can't themselves cause context overflow (they cost tokens too); the Phase 2 budgeter still fits everything under `(window - reserve) * 0.9`.
4. Confirm DLP/masking untouched (manifest lists paths/names, not masked content; file content still only enters via `read_file` which flows through the normal masked pipeline).
5. Summarize changes file by file. Note deferred: LLM-generated trim summaries; agent-discovered-file manifest population if you scoped it out; RAG-style retrieval of large files (a bigger future direction — list it).
