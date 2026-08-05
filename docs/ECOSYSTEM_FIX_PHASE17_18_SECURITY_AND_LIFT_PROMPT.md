# ECOSYSTEM FIX — PHASE 17+18: Security P0 + lift every sub-7 area to ≥7

Consolidated from the 3-model audit (Fable 5 + Opus + GPT). Two goals: **(17) close the security P0s** all three models flagged, and **(18) raise every area currently scored below 7 up to ≥7** — voice mode, observability, connectors ecosystem, code-interpreter UX. Cross-stack. Do not regress Phases 1–16 + A. Do NOT `git push`.

Independent PARTs — do/commit separately, each with R0 + tests. The three models agreed on these; where they conflicted, the resolved truth is stated.

---

# BLOCK 17 — Security P0 (do first)

## 17A — Purge the committed admin JWT (verified: real, tracked)
**Verified at HEAD:** `admin-ui/.tmp_bugs/tok` is a real HS256 JWT (`eyJhbGci…`, `sub:22f0d459…`) and `admin-ui/.tmp_bugs/body.json` are TRACKED (Opus found it; GPT missed it — it IS in HEAD).
1. `git rm -r admin-ui/.tmp_bugs/` and add `.tmp_bugs/` to `.gitignore`.
2. **Rotate** whatever admin credential that token authenticates (assume compromised — it's been on the shared GitLab). Prove the old token is now rejected (401).
3. History purge (the token is in past commits): document it as a coordinated `git filter-repo --replace-text` + `--force-with-lease` task (team-warned) — OR do it if you can coordinate; rotation (step 2) is what actually removes the risk.

## 17B — Sandbox control-plane broker (all three: P0)
**Verified:** chat-edge holds `/var/run/docker.sock` (`docker-compose.sandbox.yml`, ~L50) = host-root-equivalent; the mitigating broker is documented but NOT built. The job container itself is well-isolated (Phase 13) — the control plane is the hole.
R0: read `internal/sandbox/sandbox.go` (how it shells `docker run`), `docker-compose.sandbox.yml`.
Fix: replace direct docker-socket access with a **minimal broker** — a tiny privileged sidecar that accepts ONLY "run image X with these fixed hardened flags + this input dir" (no arbitrary docker API), so a chat-edge compromise can't drive the daemon. If a full broker is too large this pass, at minimum: run the socket access through a locked-down proxy (e.g. read-only, single-endpoint) and document the residual. State what you shipped.

## 17C — NER posture: fail-closed option + Thai coverage (all three: the DLP recall hole)
**Resolved truth:** NER is **off by default** (identity names can egress by non-detection, not bypass). GPT correctly noted a **Thai regex fallback exists** (`registry.py` `thai_name_no_prefix`); Fable/Opus overstated "no Thai." So: regex catches structured PII + some Thai names, but novel English/out-of-context names still slip.
R0: read `detectors/entities.py` (the `ner_enabled=False` defaults), `public_chat.py` (~L1336/2455 pass-through), `registry.py` Thai fallback, and how the `search`/`media` `block` tenant policy (Phase 10 A2) already flips NER on.
Fix: (a) make NER **enable-able per tenant/policy** and default it ON for the `block`/strict posture (the compliance path already does this for search/media — extend to chat); (b) ship/verify a **Thai NER model or strengthen the Thai regex set** so Thai names are caught even NER-off; (c) benchmark recall before/after on a seeded PII set (names EN+TH, phones, IDs) and report the numbers. Do not make NER-on the global default if latency is prohibitive — make it the compliance-tenant default + document the tradeoff.

## 17D — Stop storing detector-missed content plaintext at rest
**Resolved truth:** detected PII IS masked/placeholdered in logs (`safe_input_snapshot`) — Opus overstated "raw PII plaintext." BUT text the detector judged **clean** is stored raw in `request_logs.input_raw/output_raw` (`log-worker/db/inserter.go` ~L50-54, `observability.py:259`), so detector-missed PII persists plaintext.
Fix: encrypt `input_raw`/`output_raw` at rest (reuse the existing AES-256-GCM helper — same one for provider keys), OR store only the masked snapshot + a retention policy, so a DB leak doesn't expose detector-missed business/PII content. State which.

## 17E — Release integrity + nginx hardening (all three)
1. `releases/latest.json` has `"sha256": ""` (verified) — the desktop auto-update integrity check can't verify. Populate the real SHA-256 of the published installer and confirm `UpdateService` (Phase 1/R11) rejects a mismatch (it already verifies — the empty hash defeats it).
2. `infra/nginx/nginx.conf` (~L35) has TLS commented + no security headers. Add: forced HTTP→HTTPS redirect, HSTS, `X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors`, a baseline CSP, and remove server tokens. R0: confirm the current nginx config + which upstreams it fronts.

---

# BLOCK 18 — Lift the sub-7 areas to ≥7

## 18A — Voice: conversational mode (scored 2–4, biggest consumer gap)
**Verified:** dictation + TTS hooks exist but are never assembled into a hands-free loop; the waveform is dead (`ChatInput.tsx` ~L582 passes `stream={null}` so `AudioWaveform`/`RecordingBar` always uses the CSS fallback).
R0: read the existing voice hooks (`useWhisperInput`/dictation, `useVoiceOutput`/TTS), `ChatInput.tsx`, `RecordingBar.tsx`/`AudioWaveform`.
Fix: (a) **fix the dead waveform** — pass the live `MediaStream` so the reactive visualization works; (b) **assemble a hands-free conversational loop**: listen → transcribe → send → speak the reply (read-aloud) → auto-re-listen, with a clear on/off control, barge-in/stop, and turn detection (silence timeout). This is the ChatGPT-Advanced-Voice / Gemini-Live equivalent. Doesn't need camera/screen (note those as future). Aim: a usable hands-free chat loop = ~7. Tests for the state machine (idle→listening→sending→speaking→listening; stop; error).

## 18B — Observability: tracing + metrics (scored ~6)
**Verified:** no distributed tracing anywhere (zero OTel across the 4 services); `request_id` not bound into structlog contextvars; chat-edge + mcp-gateway have no `/metrics`.
R0: read how `request_id` flows today (backend middleware, chat-edge), existing Prometheus wiring (backend has metrics; which services lack it).
Fix: (a) bind `request_id` into structlog contextvars so every log line in a request carries it (backend) + propagate a trace/correlation id across gateway→backend→chat-edge; (b) add OpenTelemetry tracing (or at minimum W3C traceparent propagation + spans on the hot path) across backend, chat-edge, mcp-gateway; (c) add `/metrics` to chat-edge + mcp-gateway (request count/latency/errors). Wire the existing `alerts.yml` to an Alertmanager route (currently routes nowhere). Aim ≥7. R0: confirm what's already there so you extend, not duplicate.

## 18C — Connectors: expose what's already brokered + harden (scored 3.5–6.5)
**Resolved truth:** the OAuth broker (Phase 7) and desktop MCP client (stdio+HTTP) are genuinely capable — Opus's 3.5 was too harsh (it judged mcp-gateway alone). The real gaps: the managed catalog is 4 hardcoded servers, `mcp.sran.ai` catalog URLs are placeholders, npx deps use `"latest"` (no lockfile — supply-chain), and Google connectors are brokered but not surfaced.
R0: read `mcp-gateway/lib.js` (SERVERS, deps), the connector catalog in desktop (`McpConnectorService` embedded catalog + `McpCatalogUrl`), `connector_oauth_service.py` (which providers are wired: google/github/slack).
Fix: (a) **surface the already-brokered Google connectors** (gmail/gdrive/gcal) in the desktop marketplace (Phase 7 wired the broker + provider configs — expose them); (b) replace placeholder `mcp.sran.ai` catalog URLs with real reachable ones OR make the catalog clearly "self-hosted, configure your URL"; (c) **pin mcp-gateway deps** (replace `"latest"` + add a lockfile — supply-chain); (d) live `tools/list` instead of hardcoded tool strings that can drift. Aim ≥7 (a real, if small, connector platform).

## 18D — Code interpreter in web-chat + surface the results (scored 6.5)
**Verified:** the sandbox + `/code/run` (Phase 13) is server-side and works, but web-chat has NO code-interpreter UI — no execution timeline, console, files/images panel; and it's stateless per call (no persistent kernel).
R0: read how tool calls / `run_python` surface in `chatStore.consumeStream` + the message rendering; the backend `/code/run` contract.
Fix: (a) **surface run_python in web-chat** — when the model runs code, show a code+output panel (stdout/stderr, generated charts inline, produced files as downloads) like ADA/analysis; (b) **persistent kernel across a conversation's tool calls** (so `df` from step 1 survives to step 3) — the biggest capability gap vs ADA; R0 whether the sandbox can hold a warm kernel per conversation or if that needs the Phase-13 sandbox to support a session. If persistent-kernel is too large this pass, ship the UI surfacing (a real jump 6.5→7) and track the kernel as follow-up. State the split. Also: implement the **run_python OUTPUT DLP gate** the Phase-13 doc claimed but code doesn't (`tools.go` ~L1284-1390 returns stdout raw) — the output must flow through the masked pipeline like any tool result.

---

## Final verification checklist
1. Per PART: what you ran, green (backend pytest, chat-edge go test, web vitest, desktop dotnet — state per stack; note anything you couldn't execute here).
2. **Security statement:** 17A token purged+rotated (old rejected); 17B what the broker now prevents; 17C recall benchmark numbers (before/after, EN+TH names); 17D what's now encrypted/dropped at rest; 17E sha256 populated + nginx headers present.
3. **Lift statement:** for each 18 area, the new score you'd assign and why it's now ≥7 (voice hands-free loop works; tracing+metrics+request_id present; Google connectors surfaced + deps pinned; code panel + output DLP gate).
4. Summarize changes file by file per stack. List what remains for the remaining consolidated phase (19+20: CD/IaC/k8s/alerting completion + consumer polish — Settings/custom-instructions, editable Canvas, desktop inline images/jump-to-bottom/consent-banner, model-picker keyboard nav) WITHOUT implementing.
