# SMOKE TEST — bring up webchat + desktop and report what actually works

Goal: actually RUN the stack and exercise web-chat and the SranDesktop app end-to-end, then report — feature by feature — what works, what's broken, and what's not wired. This is a hands-on runtime check, NOT a code read. You MAY start services, run builds, apply migrations, and hit endpoints. Do NOT `git push`, do NOT commit, do NOT edit source to "make it pass" — if something's broken, report it, don't patch it silently. Leave the working tree clean (stop containers when done, or leave them up and say so).

Environment note: this needs Docker + the backend stack for web-chat, and Windows/.NET for the desktop app. If a piece can't run in your environment (e.g. no Docker daemon, or non-Windows so WPF can't launch), say so explicitly and smoke-test what you can — don't fake results. Honesty over coverage.

## Part 0 — Bring the stack up

1. R0: read `Makefile` (targets `bootstrap`, `up`, `prod-desktop`, `sandbox-up`, `chat-edge-migrate*`), `docker-compose.yml` + `docker-compose.webchat.yml` + `docker-compose.sandbox.yml`, and `.env.example`. Confirm what each brings up (recall from Phase 15: web-chat front-end lives in the webchat compose with its OWN postgres/redis/minio; chat-edge + mcp-gateway are behind `--profile desktop`).
2. Bring up the stack the cleanest way:
   - If `make bootstrap` exists (Phase 15): use it (it generates `.env` secrets, health-gates, migrates chat-edge, seeds). Otherwise `cp .env.example .env`, set `REDIS_PASSWORD`, `make up`, `make chat-edge-migrate-main`, `make seed`.
   - For web-chat specifically: bring up the webchat compose (`docker-compose.webchat.yml`) — it has the web-chat front-end (:3002) + its own DB/redis/minio.
   - Optionally `make sandbox-up` if you want to test the code interpreter.
3. Health-gate: confirm each service answers before testing — gateway `:8080/health` (or `/__gw/health`), backend, chat-edge `:4000`, web-chat `:3002`, portal `:3001`, admin `:3000`, mcp-gateway `:3200/health`. Report which are up and which failed to start (with the failing log lines).
4. You'll need a usable login: use the seeded user (check `make seed` / the seed script for the credentials, e.g. the UAT user) or create one via the portal. Report how you authenticated.

## Part 1 — web-chat smoke test (`:3002`, ChatGPT-like)

Drive it via HTTP against chat-edge/gateway where you can (curl/httpie with the auth cookie/token), and note what needs a real browser (SSE streaming render, voice, artifacts) vs what you can verify headless. For each, report WORKS / PARTIAL / BROKEN / COULDN'T-TEST + evidence (response, status, or the blocker):

1. **Auth**: login handoff (portal → web-chat), session cookie set, `/api/v1/...` calls authenticated.
2. **Send a message + streaming**: create a conversation, send a prompt, confirm an SSE stream returns tokens (needs a real model key on the gateway — if none is configured, note it; the DLP/routing path can still be exercised with the mock provider — check whether `mock-provider` is wired and use it).
3. **DLP masking (the core!)**: send a message containing fake PII (a name, phone, email, a fake API key like `sk-test-1234...`) and verify the outbound-to-provider path masks it — check the request log / trace (admin UI `:3000` chat observability, or the `request_logs`), and confirm the response restores tokens. This is the headline capability — prove it end-to-end.
4. **Model picker / models list**: `GET /models` returns the catalog.
5. **File upload**: upload a small file to a conversation, confirm it stores + is retrievable.
6. **Conversation ops**: rename, folder, pin, search, export (md/json), share link.
7. **Memory / KB**: create a memory, create a KB + upload a doc, search it.
8. **Media gen / web search / code interpreter**: check which are configured (they need provider keys / SANDBOX_ENABLED); report configured-and-working vs not-configured.
9. Note anything that 404s, errors, or is visibly stubbed.

## Part 2 — SranDesktop smoke test (WPF, Claude-Desktop-like)

If on Windows with .NET 8: `dotnet build SranDesktop/SranDesktop.sln -c Release` then run the app (or `dotnet run` the App project). If not on Windows, say so and cover what you can (build-only won't work cross-platform for WPF — state it).

1. **Build**: does it build clean from the current tree? (This also re-confirms the storage-gitignore fix — a fresh checkout must build.)
2. **First run / login**: FirstRunDialog, point it at the local gateway/chat-edge URLs, log in with the seeded user. Agent key self-provisions?
3. **Agent turn (the core)**: send a task that uses tools — e.g. "list the files in [a test folder] and read one" — confirm the tool-call cards render, `read_file`/`list_directory` execute, consent prompts appear appropriately.
4. **Phase A/B/C features specifically** (these are the recent work — verify they actually work at runtime, not just in tests):
   - `edit_file` makes a targeted edit to a test file (Phase A).
   - `run_command` runs something >30s and streams output live in the mini-terminal (Phase B PART 1).
   - a plan/todo renders and checks off (Phase A PART 5).
   - `run_subagent` with multiple tasks fans out (Phase B PART 2).
   - `remember` persists a note; reopen the conversation and confirm the `## Notes` context survives (Phase B PART 3).
   - inline image renders in a reply (Phase C PART 1); jump-to-latest button appears when scrolled up (C2); the danger banner shows for run_command/computer-use (C3); model picker keyboard nav (C4); System theme follows OS (C5); a delete confirmation shows the themed ConfirmDialog, not native MessageBox (C.5).
   - durable tool history: do a multi-step task, close + reopen the conversation, confirm the tool steps survived (Phase A PART 1).
5. **MCP / connectors**: does the connector marketplace load; can a stdio or HTTP MCP connect (if one's available)?
6. Report WORKS / PARTIAL / BROKEN / COULDN'T-TEST per item with the concrete evidence or blocker.

## Report format

- **Environment**: what you could/couldn't run (Docker? Windows? which services came up?).
- **web-chat table**: feature → status → evidence/blocker.
- **desktop table**: feature → status → evidence/blocker.
- **DLP masking result**: the headline — did fake PII get masked before egress, end-to-end? Show the before/after from the trace.
- **Top issues found at runtime**: anything broken/stubbed/misconfigured that the code-only audits couldn't catch (missing provider key, a 404, a service that won't start, a feature that's off-by-default).
- **What's genuinely usable today** vs **what needs config/keys/env to work**.
- Confirm: nothing pushed, nothing committed, no source edited to force a pass; state whether you left containers running or tore them down.
