# ECOSYSTEM FIX — PHASE 16: Close the deferred-features backlog (build what's worth it, formally close the rest)

The final planned phase. Four items were deferred across Phases 11–13. The recon shows they are NOT equal: two are worth shipping now, one is worth doing ONLY if done fully (a half-version breaks the desktop in the field), and two are genuinely low-ROI right now. This phase is honest closure — **build A + B, do C correctly-or-defer, and formally close pdf + Pyodide with rationale and revisit-conditions.** Padding the phase with low-value work would be the wrong call. Do not regress Phases 1–15. Do NOT `git push`.

Independent PARTs — do/commit separately. Each PART: R0 first, then behavior + tests. State what you ran, green.

---

## PART A — Sub-agent progress UI (desktop) — BUILD (effort M, clean seam)

**Verified:** `run_subagent` (`SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/SpawnAgentTool.cs`) already runs a nested `ChatOrchestrator` and **already consumes the nested `AgentStreamEvent` stream** in its switch (~L167-193: `AgentStatusEvent`/`AgentContentDeltaEvent`/`AgentToolResultEvent`→trace/`AgentStreamDoneEvent`/`AgentErrorEvent`) — it just collapses everything into `finalText` + a `trace` string. The nested run is opaque in the UI. The top-level `run_subagent` call already renders its own `ToolExecutionItemViewModel` card (`ChatViewModel.cs` ~L982). Goal: show the nested tool activity *inside* that card, live.

R0: read `SpawnAgentTool.cs` fully, `AgentExecutionViewModel.cs` (`ToolExecutionItemViewModel` — `State`, `Complete`, `ScreenshotPreview`), the `ChatViewModel` orchestrator consumer loop (~L961-1043, how `AgentToolCallsEvent`/`AgentToolResultEvent`/`AgentStatusEvent` become/complete cards), `IMcpTool.ExecuteAsync` signature, and `MessageBubble.xaml` tool-card template.

1. **Callback channel through the `IMcpTool` boundary:** `IMcpTool.ExecuteAsync(JsonElement, CancellationToken)` has no progress channel today. Add a nested-progress path with the smallest clean change — options (pick + justify): an optional `IProgress<AgentStreamEvent>`/`Action<AgentStreamEvent>` the orchestrator threads to a tool that opts in (e.g. a `ISupportsNestedProgress` interface `SpawnAgentTool` implements), OR an event on `SpawnAgentTool` the orchestrator subscribes and re-emits correlated to the parent `ToolCallId`. Do NOT broaden every tool's signature; keep it opt-in for the sub-agent tool.
2. **Correlate to the parent card:** the nested events must attach to the parent `run_subagent` `ToolExecutionItemViewModel`. Give `ToolExecutionItemViewModel` a child `ObservableCollection<ToolExecutionItemViewModel>` (nested runs) and have `ChatViewModel` route nested `AgentToolCallsEvent`/`AgentToolResultEvent` into the parent card's child collection (pending→ok/failed, same lifecycle as top-level cards). Nested `AgentStatusEvent` can update a nested status line.
3. **XAML:** in `MessageBubble.xaml`, render the child collection indented under the parent tool card (collapsible, consistent with the existing card visuals). Reuse the existing card template recursively if clean.
4. **Bounds:** depth cap stays 1 (no card-in-card-in-card). The nested caps (10 steps / 4 min) already bound it. Keep the final `trace`/`finalText` result too (don't remove it — the card list is additive).
5. Tests: nested tool events surface as child cards on the parent (mock the nested orchestrator to emit tool-call + result events → assert the parent card's child collection populates + completes); depth-1 enforced; the tool still returns its final text; no regression to normal (non-sub-agent) tool cards. Localize any new strings EN/TH.

## PART B — Local pptx generation (desktop) — BUILD (effort M, no new dependency)

**Verified:** Phase 12 shipped local xlsx (`LocalExcelPlanExecutor`, ClosedXML) + docx (`LocalDocPlanExecutor`, OpenXml). pptx falls back to server at `LocalDocPlanExecutor.cs:44-46` (`format != "docx" → false`). **`DocumentFormat.OpenXml 3.*` is already referenced** (`SranDesktop.Infrastructure.csproj:30-32`) and v3 includes `PresentationDocument` — so pptx needs NO new package. The server generator to match is `services/chat-edge-api/scripts/pptx_generator.py` (python-pptx): plan = `presentation_settings` + `slides[]`, slide types `title, section_divider, content, two_column, stats, table, chart, closing` (`SLIDE_BUILDERS`), corporate navy `0A1230` (matches `LocalDocPlanExecutor.CorporateNavyHex`).

R0: read `pptx_generator.py` fully (the exact plan schema per slide type), `LocalDocPlanExecutor.cs` + `LocalExcelPlanExecutor.cs` (the `ILocalPlanExecutor` contract, `CanExecute` gating, corporate palette), `PlanBlockViewModel.cs` (`TryExecuteLocallyAsync` ~L118-153, `BuildLocalOutputFilename` ~L155-164, the server-fallback path), and `AppHostBuilder.cs` DI wiring.

1. **New `LocalPptxPlanExecutor : ILocalPlanExecutor`** (`PlanType => "pptx"`), OpenXml `PresentationDocument`. Implement the slide-type builders that DON'T need charts: `title, section_divider, content, two_column, stats, table, closing` — matching the Python schema + corporate palette + per-slide try/except isolation (same failure semantics as the existing executors).
2. **`CanExecute` gating (mirror xlsx's chart exclusion):** return false for any plan containing a `chart` slide (charts in PresentationML are as hard as they were in ClosedXML — Phase 12 excluded excel charts for the same reason) and for `source_file_id`/template-based plans — those transparently fall back to the server generator, which keeps working. So local covers the common text/table/stats decks; charts stay server-side.
3. **Wire it:** DI in `AppHostBuilder` (the executor is currently a single `ILocalPlanExecutor?` — R0: extend to a dispatcher/collection keyed by `PlanType`, or however the xlsx+docx pair is already selected). `PlanBlockViewModel.BuildLocalOutputFilename` gets a `.pptx` branch. Server fallback stays transparent (unchanged for chart decks).
4. Same DLP/privacy property as Phase 12 E: local generation keeps deck content on-device (never transits chat-edge); content still came from the masked gateway, so no new egress.
5. Tests: a representative pptx-plan (title + content + table + stats slides) → local executor produces a valid `.pptx` (open with OpenXml, assert slide count + a known text run); a plan WITH a chart slide → `CanExecute` false → server fallback path invoked (mock the API); filename `.pptx`; parity of corporate palette with docx.

## PART C — Portal CSRF (web + desktop) — do it FULLY or defer (do NOT half-ship)

**Verified + the trap:** admin-ui has double-submit CSRF (`admin-ui/lib/api.ts` reads `csrf_token` cookie → `X-CSRF-Token`; `backend/app/middleware/csrf.py` enforces on `/admin/` only). Portal relies on `SameSite=Lax` alone. The migration sketch is documented at `backend/app/services/portal/cookies.py:32-64`. **The load-bearing catch (recon):** the desktop WPF client authenticates to `/portal/*` mutations **BY COOKIE** (`AgentKeyProvider.cs` POSTs `portal/projects/{id}/keys`, `portal/projects`; `PortalAuthClient.cs`), sending NO CSRF header. So widening `CsrfMiddleware` to `/portal/*` would **403 every desktop install in the field** unless the desktop ALSO reads `portal_csrf` and echoes `X-CSRF-Token` on its portal mutations. web-chat's portal calls are the third client.

**Decision rule:** this is safe to defer today (Lax blocks the cross-site vector while same-site; `validate_secrets()` fail-closes any `SameSite=None` deploy, so there's no silent-exposure window — it only becomes mandatory when a cross-site topology is adopted). Therefore:

- **Option 1 — implement it FULLY (all three cookie-auth clients in ONE atomic change):** only if you can do all of: (a) mint a JS-readable `portal_csrf` cookie in `set_auth_cookies` (`cookies.py`, `httponly=False`), (b) widen `CsrfMiddleware` to `/portal/*` exempting `/portal/auth/*` bootstrap, (c) echo the token in `portal/lib/api.ts`, `apps/web-chat/src/lib/api.ts`, AND the **desktop** (`AgentKeyProvider` + any portal POST in `PortalAuthClient`/`ChatEdgeApiClient` that hits `/portal/*`) — with a desktop regression test proving portal mutations still succeed. All-or-nothing: a partial rollout breaks desktop. If you ship it, ship every client + a test per client.
- **Option 2 — keep deferred (acceptable):** if the three-client coordination + desktop cookie-CSRF echo is too risky to do confidently in this pass, KEEP it deferred — but upgrade the documentation: make `cookies.py`'s note a first-class tracked task with the exact atomic-rollout checklist, and confirm `validate_secrets()` still fail-closes `SameSite=None` so the deferral stays safe. Do NOT half-implement (do not widen the middleware without shipping all three client echoes).

R0: read `admin-ui/lib/api.ts`, `backend/app/middleware/csrf.py`, `backend/app/services/portal/cookies.py:32-64`, `portal/lib/api.ts`, `apps/web-chat/src/lib/api.ts`, and the desktop `AgentKeyProvider.cs`/`PortalAuthClient.cs`. State which option you chose and why. If Option 1, the atomicity + the desktop regression test are the headline deliverables.

---

## FORMALLY DEFERRED (build NONE of these; document the close-out)

Do not implement; write a short tracked note (in the relevant code's docstring or a `docs/DEFERRED.md`) capturing the rationale + the revisit-condition, so the backlog is honestly closed rather than silently dropped:

- **Local PDF generation (desktop):** requires a NEW .NET PDF dependency (QuestPDF/PdfSharp — license-vet) AND re-solving Thai/Sarabun font embedding that the server `pdf_generator.py` (reportlab + bundled Sarabun TTF) already handles. The transparent server fallback works today. Low ROI. Revisit if offline PDF becomes a hard requirement. Effort L.
- **Client-side Pyodide (desktop local-only Python):** the "data never leaves the machine" premise is undercut — files already upload to chat-edge for the rest of the pipeline, and the Phase 13 server sandbox is hardened + done. Requires bundling a large (~10-200MB) Pyodide payload (installer bloat), relaxing the artifact CSP (`'wasm-unsafe-eval'`), a new WebView2 host + `run_python_local` tool, and it's a strictly weaker execution surface (no native wheels, memory-bound). Revisit only if a true never-uploaded local-file analysis workflow becomes a priority. Effort L. **Strongest defer of the four.**

---

## Final verification checklist

1. Per built PART: what you ran, green (desktop `dotnet build` 0/0 + `dotnet test`; any web/backend suites for PART C if Option 1). No existing test weakened.
2. **PART A:** nested sub-agent tool activity now renders live as child cards; depth-1 bound holds; final result unchanged. **PART B:** local pptx for text/table/stats decks; chart decks + templates still fall back to server transparently; no new NuGet dependency added. **PART C:** state Option 1 (all three clients + desktop regression test — CSRF enforced without breaking desktop) or Option 2 (deferred with the atomic-rollout checklist documented + `validate_secrets` fail-close confirmed).
3. **Deferred close-out:** the pdf + Pyodide notes are written with rationale + revisit-condition (so the backlog is closed, not dropped).
4. Summarize changes file by file. **This is the final planned phase** — end with a program-wide close: Phases 1–16 status, the full ecosystem test count, and confirm the masking invariant + all Phase 1–15 guarantees still hold. List the remaining OPERATIONAL items (git push, gw_ key history purge pending team coordination, sandbox live-deploy, `/data/workspace` quarantine run) as the only open work — no planned feature phases left.
