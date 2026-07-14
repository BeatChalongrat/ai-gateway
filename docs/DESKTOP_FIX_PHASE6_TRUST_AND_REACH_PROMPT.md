# DESKTOP FIX — PHASE 6: Agent trust & reach (folder grants, model-driven skills, WebView2 hardening, screenshot hygiene)

You are working on **SranDesktop** (Windows WPF, .NET 8, at `SranDesktop/`). Desktop-only — no gateway/chat-edge changes. Phases 1–5 are merged; do not regress them (agent loop, context budgeter, sync, progressive rendering, backend wiring — their test suites must stay green and unmodified).

## Why this phase (verified in code)

1. **The agent can't reach real work folders.** `PathGuard` (`SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/ToolHelpers.cs:41-87`) hard-codes exactly two allowed roots: the user profile and `%TEMP%`. A project on `D:\` or a shared drive is unreachable, full stop. There is no way to grant more.
2. **Skills only trigger on exact substrings.** `SkillManager.FindMatchingSkillsAsync` (`Infrastructure/Services/SkillManager.cs:74-81`) does `message.Contains(trigger)` — a Thai user saying "ทำไฟล์สเปรดชีตยอดขายให้หน่อย" never matches the `xlsx` skill's English triggers. The model never even learns the skills exist.
3. **Artifacts run untrusted JS with full freedom.** `ArtifactControl.xaml.cs:54-80` disables only status bar/DevTools/context menus; there is **no `NavigationStarting` restriction, no CSP**, and `ArtifactViewModel` loads React/Babel/Mermaid from public CDNs (unpkg/jsdelivr) — model-generated code can navigate anywhere, fetch anything, and artifacts break offline.
4. **Screenshot temp leak** (open since the first audit): `ComputerScreenshotTool` writes `%TEMP%\sran-screenshot-*.png` on every capture, never deletes them, and does a write-then-read roundtrip just to build the base64 data URI.

## Ground rules

- Read every file you touch IN FULL first; complete R0 and answer it in the summary.
- New user-facing strings → BOTH `En`/`Th` in `Dict.cs` (`DictParityTests`). Natural Thai.
- Security posture may only tighten, never loosen: nothing in this phase may weaken the consent flow, DLP handling, or `run_command` guards.
- Pure logic in testable classes; tests required for every behavioral claim.
- Finish state: `dotnet build` 0/0; `dotnet test` all green.

## R0 — Mandatory reconnaissance

1. Every caller of `PathGuard.Resolve` (all file tools + `RunCommandTool`'s working-dir guard — list them) and how the "Environment" block in the agent system prompt (`ChatViewModel.BuildGatewayMessages`) currently describes the accessible directories.
2. How `UserSettings` fields are added/persisted (`Application/Models/UserSettings.cs` + `JsonSettingsService`) and how `SettingsView.xaml`/`SettingsViewModel` sections are structured (you'll add a folder-grants section).
3. Tool DI registration in `AppHostBuilder` (the `McpToolRegistry` block, ~lines 101–127) — where the new skill tool registers.
4. `ArtifactViewModel`'s full HTML-building path: how each type (react/html/svg/mermaid/code) is wrapped, where CDN URLs appear, and the verbatim passthrough for sources containing `<html` (~line 124). Also `ArtifactControl` fullscreen + "open external" flows (the temp `.html` shell-open) — decide explicitly what CSP means for the open-external copy.
5. `ComputerScreenshotTool` + `Win32Input` capture path: confirm the write-then-read roundtrip and the scaling step.
6. `ISkillManager` interface + `Skill`/`SkillManifest` models; where `BuildSystemExtrasAsync` injects matched-skill SKILL.md today.

---

## TASK 1 — Folder grants: let the user extend the agent's reach safely

1. **Settings model:** `UserSettings.GrantedFolders` (`List<string>`, default empty), persisted as usual (atomic writes already exist from Phase 3).
2. **PathGuard:** keep the two built-in roots; add a thread-safe configurable set — `PathGuard.ConfigureGrantedRoots(IReadOnlyList<string>)` called at startup (after settings load) and on every settings change. Rules, all tested:
   - A granted path must be an existing, fully-qualified directory.
   - **Refuse** granting (silently skip + log warning at configure time; block with a localized message in the UI at add time): filesystem roots (`C:\`, `D:\`), `%WINDIR%`, `%ProgramFiles%`/`(x86)`, and `%APPDATA%\SRAN Desktop` (the app's own state — the agent must not edit its own permissions/credentials).
   - Canonicalize before comparison (same `..\` handling as today); a granted root allows the root itself + descendants.
   - The denial error message now enumerates "your user profile, the temp folder, and folders you've granted in Settings" — update the string, and mirror it in the agent system prompt's Environment block so the model knows what it can reach AND that the user can grant more via Settings.
3. **Settings UI:** a "Folder access" group in `SettingsView` — list of granted folders with per-row remove, an Add button using the WPF folder picker (`Microsoft.Win32.OpenFolderDialog` on .NET 8 — no WinForms reference). Dict keys (suggest): `settings.folderAccess`, `settings.folderAccessHint` (one-line explanation that the agent can read/write inside granted folders after per-action consent), `settings.addFolder`, `settings.removeFolder`, `settings.folderRejected` — EN + natural TH for all.
4. **Interaction with consent (design decision — implement, don't punt):** granting a folder widens WHERE tools may operate; it does NOT bypass consent. `write_file`/`run_command` still Ask per the existing `PermissionService` scopes. State this in the hint text and a code comment.
5. Tests: resolve inside a granted root (other drive letters — use a temp dir and case-insensitive compare, don't literally require `D:\`); `..\` escape from a granted root → denied; removing a grant revokes immediately (re-configure); forbidden grants filtered; built-in roots unaffected when the list is empty.

## TASK 2 — Model-driven skill selection (fix Thai + paraphrase triggering)

Keep the existing trigger fast-path (its R9 tests stay green); ADD the model-driven path:

1. **New tool `use_skill`** (`Infrastructure/Mcp/Tools/UseSkillTool.cs`): args `{"name": "<skill name>"}`; returns the skill's SKILL.md instructions for an ENABLED skill; error result for unknown/disabled names. `DefaultPermission = AllowAlways` (reading bundled instructions is side-effect-free — justify in a doc comment), a suitable `ToolCategory`, registered in the `AppHostBuilder` block (inject `ISkillManager`).
2. **Skill catalog in the system prompt:** in `BuildSystemExtrasAsync`, always append a compact catalog of enabled skills — one line each: `- {name}: {description}` — introduced by a short instruction: when a task matches a skill, call `use_skill` FIRST and follow the returned instructions; skills already inlined this turn (trigger fast-path) are marked as loaded so the model doesn't fetch them again. Build the catalog string in a pure, tested helper (input: skill list + already-matched names; output: string or null when no skills).
   - `SkillManifest` — R0.6: if there is no description field, add one (optional, backward-compatible JSON) and give the five built-ins real descriptions when `EnsureBuiltins` writes them; missing description falls back to the trigger list joined.
3. **Token discipline:** the catalog must stay small (cap each description at ~120 chars in the builder). The full SKILL.md only enters context when matched or fetched.
4. Tests: `UseSkillTool` returns instructions for enabled, errors for disabled/unknown (reuse the SkillManager temp-dir test harness from R9); catalog builder — empty list → null, matched skills marked, caps applied; existing trigger tests untouched.

## TASK 3 — WebView2 hardening + offline artifact libs

1. **Bundle the libs.** Add minified `react`, `react-dom`, `babel-standalone`, and `mermaid` (the same major versions currently referenced from CDNs — R0.4) under `Presentation/Assets/WebLibs/` as build-output content. Serve them via `CoreWebView2.SetVirtualHostNameToFolderMapping("appassets.sran", <that folder>, Deny)` — mapped with least privilege (`CoreWebView2HostResourceAccessKind.Allow` only for that host). `ArtifactViewModel` templates reference `https://appassets.sran/...` instead of unpkg/jsdelivr. Artifacts now work offline; note the installer size impact in the summary.
2. **CSP.** Inject a `<meta http-equiv="Content-Security-Policy">` into every generated document: `default-src 'none'; script-src 'unsafe-inline' https://appassets.sran; style-src 'unsafe-inline' https://appassets.sran; img-src data: https:; font-src https://appassets.sran; connect-src 'none'`. For the verbatim `<html` passthrough case, inject the meta right after `<head>` (or synthesize a head) — a pure, tested string function. Yes, `connect-src 'none'` means artifacts cannot call external APIs — that is the intended posture (matches Claude's artifact sandbox); mention it in the summary.
3. **Navigation lock.** On `CoreWebView2`: handle `NavigationStarting` — allow only the initial `NavigateToString` content (about:blank/data:) and `https://appassets.sran`; cancel everything else, and for cancelled `http(s)` targets open them in the system browser (`Process.Start UseShellExecute` — same pattern as `MarkdownRenderer.WireLink`). Handle `NewWindowRequested` → `Handled = true` + system browser. Also disable `IsPasswordAutosaveEnabled`/`IsGeneralAutofillEnabled`.
4. **Open-external copy** (R0.4): the temp `.html` written for "Open in browser" gets the same CSP meta; it will reference `appassets.sran` which doesn't resolve in a real browser — either inline the libs into that exported copy or fall back to the CDN URLs THERE ONLY (an explicit user action in their own browser; document the choice).
5. Tests: CSP-injection function (fragment, full-html-with-head, full-html-without-head); template output references `appassets.sran` and contains the CSP meta for every artifact type (extend the existing R9 artifact-HTML tests). Navigation-lock logic: extract the allow/deny decision into a pure predicate and test it (`https://appassets.sran/x` allow, `https://evil.example` deny+external, `data:`/`about:blank` allow).

## TASK 4 — Screenshot hygiene (small)

1. `ComputerScreenshotTool`: build the base64 data URI directly from a `MemoryStream` — no temp file, no write-then-read roundtrip. Keep the existing max-1920px scaling.
2. Startup cleanup (fire-and-forget, logged): delete `%TEMP%\sran-screenshot-*.png` older than 24 h — one-time migration for the files already leaked on users' machines. Put it where other startup housekeeping lives (R0: find where `AppHostBuilder`/`App.OnStartup` does init work).
3. Existing `ComputerToolTests` stay green; add a test only if the tool has a testable seam without native capture (if not, say so).

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` all green (Phases 1–5 suites unmodified) incl. new PathGuard/catalog/CSP/nav-predicate tests and `DictParityTests`.
2. Security review paragraph in the summary: for each of Tasks 1 and 3, state what an attacker-controlled artifact/model output could do BEFORE vs AFTER (folder grants: consent still gates writes; artifacts: no navigation, no network, no CDN supply-chain exposure).
3. Answer all R0 questions.
4. Summarize changes file by file + list follow-up risks for Phase 7 (OAuth connector broker: server-side OAuth redirect service, desktop custom-URI callback, token storage in DPAPI, MCP catalog hosting) WITHOUT implementing them.
