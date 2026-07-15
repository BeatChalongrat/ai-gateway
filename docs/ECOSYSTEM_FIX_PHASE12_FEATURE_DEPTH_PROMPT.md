# ECOSYSTEM FIX — PHASE 12: Feature depth + carried-forward small fixes

The "close the gaps to Claude/ChatGPT" phase. PART A clears long-standing small fixes from the audits; PARTS B–F add contained, high-value features with clean seams (verified below). **The code-interpreter / sandboxed execution work is NOT here** — it's genuinely large and security-critical, and gets its own **Phase 13**; do not half-build a sandbox inside this phase. Do not regress Phases 1–11. Do NOT `git push`.

Every PART is independent (different stacks/files) — do/commit them separately. Do each PART's R0 first and answer it in the summary. Every change ships with tests that fail-before/pass-after. Finish state per stack: state what you ran and that it's green.

---

## PART A — Carried-forward small fixes (backend hardening)

All four verified STILL open at HEAD. Independent one-shots.

**A1 — Multi-worker startup race.** `backend/app/main.py` `_seed_admin_user` (~:260-320) and `_import_env_provider_keys` (~:323+) are check-then-insert with no lock, so 4 gunicorn workers on a fresh DB can double-insert. The advisory-lock pattern already exists in this file (job drain, "advisory lock 900004", ~:140). Fix: wrap each seed/import in a `pg_advisory_lock` (distinct lock ids) so only one worker runs it. Test: concurrent invocation seeds exactly one admin / imports each key once.

**A2 — SSRF in Replicate webhook.** `webhooks_replicate.py:89-98` fetches the `output` URL from the webhook body via `adapter.fetch_result_bytes(url)` (`replicate_base.py`) with only a size guard — no host allowlist, no private-IP block. Fix: before fetching, `urlparse` the URL and require the host to be on an allowlist (the Replicate delivery domain, e.g. `replicate.delivery` / `*.replicate.delivery` — R0: confirm the real delivery host from existing successful results/config), and reject private/link-local/loopback IPs. Test: a webhook `output` pointing at `169.254.169.254` / `localhost` / an arbitrary host → rejected, no fetch; a real delivery host → allowed.

**A3 — DLP `restore()` single-pass.** `app/services/dlp/transformer.py` `restore()` (~:1503, loop at ~:1555-1557) still does N sequential `str.replace` passes over the full text — the masking direction is already span/offset-based; only restore lags. Fix: make restore a single-pass substitution (one regex alternation over the token set, or an offset-splice like `mask_text`), eliminating the value-contains-token corruption path (a restored value that happens to contain another still-pending token literal). Preserve `_normalize_tokens` + `allowed_tokens`/`[REDACTED]` semantics exactly. Tests: existing restore tests stay green; add the pathological case (a restored value containing a shorter token string) → no corruption.

**A4 — Fail-open default decision.** `rate_limit_service.py` now HAS an opt-in conservative in-process ceiling (`_conservative_allow`, `_CONSERVATIVE_LIMIT=10`) but `config.py:64` defaults `rate_limit_redis_outage_mode="fail_open"`, so out-of-the-box a Redis outage still admits unlimited. This is a product/security call, not a pure bug: R0 — assess the blast radius. Recommended: flip the DEFAULT to `conservative` for the credential-guarding limiters (login, password-reset) even if general chat stays fail-open, OR document explicitly why fail-open-by-default is accepted. Implement the chosen posture; do not leave it undecided. Test: with Redis mocked-down and conservative mode, the Nth+1 login attempt in-window is throttled.

---

## PART B — LaTeX / math rendering, web-chat

**Verified:** no math today. `MessageBubble.tsx:10` TODO; pipeline is `react-markdown@9` + `remarkGfm` + `rehypeHighlight`. THREE render sites share one `markdownComponents` (`MessageBubble.tsx:88`, render at ~:674/:694/:758), and a DUPLICATE pipeline lives in `app/shared/[token]/page.tsx:142-148`. No shared Markdown wrapper.

1. R0: confirm the three MessageBubble sites + the share-page duplicate; confirm no other `<ReactMarkdown>` call sites (grep).
2. **First extract a shared `<Markdown>` component** (plugins + components in ONE place) and switch all sites (MessageBubble ×N + share page) to it — so math (and future plugins) wire once and the public share view can't drift.
3. Add `remark-math` + `rehype-katex` + `katex` (deps) to the shared component; import the katex stylesheet once (lazy/where it won't bloat first paint — R0 note the current concern that made the TODO defer it; a scoped import is fine). Render `$…$` inline and `$$…$$` block.
4. XSS: `rehype-katex` with `output: 'html'` is safe by default (no raw HTML passthrough; the pipeline has no `rehype-raw`) — confirm and keep it that way.
5. Tests (Vitest): inline + block math render to katex markup; a malformed `$…$` degrades gracefully (no throw); the share page renders math identically. CRLF/streaming interplay unaffected.

## PART C — LaTeX / math rendering, desktop

**Verified:** no math. `MarkdownRenderer.cs` uses Markdig `UseAdvancedExtensions()` (no math ext); `RenderBlock` (:81) + `AddInlines` are the block/inline seams. No WPF math lib; the only rich-render surface is the **WebView2 already used by the artifact renderer** (`ArtifactControl` + `ArtifactSandbox`).

1. R0: decide the render mechanism — (a) enable Markdig `.UseMathematics()` to get `MathInline`/`MathBlock`, then render each math node via a small **WebView2+KaTeX** control reusing the existing artifact WebView2 infra (locked CSP, bundled libs — note KaTeX would need bundling into `Assets/WebLibs` like React/Mermaid, offline), OR (b) a WPF math NuGet (WPF-Math). Recommend (a) for consistency with the sandboxed offline artifact stack and to avoid a new math engine. Justify the pick.
2. Implement: `MathBlock` case in `RenderBlock`, `MathInline` case in `AddInlines`, rendering through the chosen surface, theme-aware (KaTeX CSS vars or a color pass). Keep it from blocking the progressive-streaming path (Phase 4) — math can render on completion if mid-stream `$…$` is incomplete (reuse the fence-guard idea: don't render an unterminated `$$`).
3. Perf/offline: if KaTeX, bundle it (`Assets/WebLibs`, `binary` in .gitattributes already covers `*.min.js`), CSP allows the appassets host.
4. Tests: `SyntaxHighlighter`-style pure test for math node detection/splitting where logic is extractable; the streaming guard doesn't flash a half `$$`. (WebView rendering itself is not unit-testable — cover the detection/guard logic, note the visual part.)

## PART D — MCP stdio transport, desktop

**Verified:** HTTP-only. `McpRemoteClient.cs` POSTs `{baseUrl}/mcp` (JSON-RPC over streamable HTTP); `McpConnector.cs` has only `ServerUrl`/`AuthType`, no `command`/`transport`. This blocks every local-process MCP server (the popular ones).

1. R0: read `McpRemoteClient` + `McpConnectorService` + `McpConnector`/connector persistence fully; establish the two edit points (client construction + connector model).
2. Introduce an `IMcpTransport` abstraction (send JSON-RPC request → response) with the existing HTTP path as one impl and a new **stdio** impl: spawn the server via `Process` with `RedirectStandardInput/Output`, frame JSON-RPC 2.0 over stdio (Content-Length headers per the MCP stdio spec — R0 confirm the framing), handle lifecycle (start on first use, dispose on connector removal/app exit, surface crashes as `Disconnected`).
3. Extend `McpConnector` with `transport` (`http`|`stdio`), `command`, `args` (and env if needed); keep existing HTTP connectors byte-compatible (default `transport=http`). Consent + DLP tool-arg policy for stdio tools go through the SAME `PermissionService`/orchestrator path as today — a local-process MCP tool is at least as sensitive as `run_command`, so its `DefaultPermission` must be `Ask` (verify it can't auto-run). Security note in the summary: a stdio server is arbitrary local code — the consent gate is the control.
4. Tests: stdio framing round-trip against a mock echo server (a tiny script), transport selection by connector config, crash → Disconnected, HTTP connectors unaffected.

## PART E — Local Office generation, desktop

**Verified:** fully server-side. `PlanBlockViewModel.ExecuteAsync` → `ChatEdgeApiClient.Execute{Excel,Document}PlanAsync` → chat-edge Python (openpyxl/python-docx) → download URL. No OpenXML/office NuGet anywhere. The plan JSON (`PlanModels.cs`) is the reusable contract.

1. R0: read `PlanModels.cs` + `PlanBlockViewModel` + the Python executors (`services/chat-edge-api/scripts/excel_executor.py`, `docx_generator.py`) to learn the exact plan schema a local executor must interpret.
2. Add a **local executor** behind an interface, using a NuGet (recommend `ClosedXML` for xlsx, `DocumentFormat.OpenXml` for docx/pptx — R0 pick, note licensing: prefer MIT/Apache) that consumes the SAME plan object and produces the file locally. Wire `PlanBlockViewModel.ExecuteAsync` to try local first, **fall back to the existing chat-edge path** when the local executor doesn't support that plan/format (so nothing regresses). A setting or capability check decides; default can stay server for parity until local is proven.
3. Why it matters: the desktop's whole value is agentic local work — generating a spreadsheet without a server round-trip (and without the content leaving for chat-edge) is squarely on-mission. Note the DLP angle: local generation keeps the doc content on-device (it never transits chat-edge), which is a privacy plus — but the CONTENT still came from the model via the gateway (masked), so no new egress.
4. Tests: a representative excel-plan and doc-plan → local executor produces a valid file (open with the lib, assert cells/paragraphs); unsupported plan → falls back to server path (mock the API); parity of a simple plan between local and server output shape.

## PART F — Sub-agents, desktop

**Verified:** `ChatOrchestrator` is single-agent (single tool loop, `MaxIterations`/deadline caps at :70/:206). Tools are `IMcpTool` via `McpToolHost`. Clean seam: a tool that runs a nested orchestrator.

1. R0: read `ChatOrchestrator` + `IChatOrchestrator` + `McpToolRegistry`/`McpToolHost` registration in `AppHostBuilder`.
2. New `SpawnAgentTool : IMcpTool` (name e.g. `run_subagent`): args `{ "task": "...", "tools"?: [...] }`. `ExecuteAsync` runs a NESTED `IChatOrchestrator` loop with a fresh message list (system + the task), a RESTRICTED tool subset (never itself — no recursive sub-agent spawning; enforce a depth cap of 1 this phase), its own bounded `MaxIterations`/deadline, and returns the nested final answer as the `ToolResult`. Inject `IChatOrchestrator` (or a factory) into the tool.
3. Guards: `DefaultPermission` — the sub-agent inherits consent for the tools IT calls (the nested run raises `ConsentRequested` through the same handler), so the sub-agent tool itself can be `AllowAlways` (it does nothing but delegate) — but confirm the nested run's tool calls still each hit consent. No recursion (depth cap), bounded steps, cancellation propagates from the parent CT.
4. Why it matters: long research/multi-file tasks currently burn the single loop's 25 steps; delegation keeps the main thread's context clean. Tests: sub-agent runs a nested loop to completion and returns text; recursion is refused (a sub-agent can't spawn a sub-agent); parent cancellation aborts the nested run; nested tool calls still go through consent.

---

## Final verification checklist

1. Per stack/PART: what you ran, green. New web tests in CI; new desktop tests in `dotnet test`; backend pytest for A1–A4.
2. **Security notes (written):** A2 (SSRF now host-allowlisted + private-IP blocked), D (stdio MCP = arbitrary local code, gated by consent — confirm it can't auto-run), F (no recursive sub-agents, bounded, consent preserved on nested tool calls).
3. **DLP/masking unchanged:** A3 restore refactor preserves exact restore semantics (round-trip a masked→restored payload, identical output); E local Office keeps content on-device without new egress.
4. Summarize changes file by file per PART; list what you deferred. Explicitly state that the **code-interpreter / sandboxed execution** work is intentionally NOT in this phase and belongs to **Phase 13** (a real sandbox: container/seccomp/rlimit or a WASM/pyodide path — the current chat-edge Python exec is plan-schema-only with only temp-dir + timeout isolation, insufficient for free-form code).
