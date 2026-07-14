# ECOSYSTEM FIX — PHASE 10: Correctness P0/P1 (cross-stack)

Fixes real bugs found in the ecosystem-wide audit, across **four stacks**: backend (FastAPI), chat-edge-api (Go), mcp-gateway (Node), SranDesktop (WPF). Each PART is independent — they can be done/committed separately, but all land under this phase. Nothing here changes a public contract; these are correctness/security/reliability fixes. Do not regress Phases 1–9.

## Ground rules

- Read every file you touch IN FULL; do each PART's R0 recon first and answer it in the summary.
- Follow each stack's conventions (pytest/Go test/node:test/xunit; existing error/logging patterns).
- Every fix ships with a regression test that FAILS before and passes after (or, where a live seam is impossible, a clear written justification).
- Security/DLP posture may only tighten. Do not weaken consent, masking, or auth.
- Finish state per stack: state exactly what you built/ran and that it's green.

---

# PART A — Backend (Python)

## A1 — [P0] Media output saved with wrong extension/MIME for audio/music

**Verified:** `backend/app/services/media_gen/job_loop.py:233` writes `f"{job_id}.mp4"` for EVERY job type; the webhook path `backend/app/api/routes/webhooks_replicate.py:102-110` hardcodes `f"{job.id}.mp4"` AND `content_type: "video/mp4"` unconditionally — so a `replicate_music` / TTS result is persisted as a video file with a video MIME.

R0: read `job_loop.py` (how `content_type` from `result_meta` is used at ~:247), `webhooks_replicate.py`, and the media adapters — establish the correct extension/MIME per `job_type` (video→mp4/video/mp4, music+tts→derive from adapter result, likely mp3/wav + audio/*).

Fix: derive extension AND content-type from the job type / adapter result, in BOTH the job-loop and webhook persistence paths. Add a per-`job_type` test asserting an audio/music job gets a non-`.mp4` filename and an `audio/*` content-type (the audit noted `test_mock_video_e2e` only covers the video happy path).

## A2 — [P1] "block" PII-egress policy leaks NER-only PII (names) on search AND media

**Verified (Phase 9 follow-up):** `scan_search_query` (`app/services/dlp/egress_policy.py`) and `scan_media_prompt_with_profile` (`app/services/media_gen/submit_helpers.py`) both scan with `ner_enabled=False`. That's correct for the default `allow` policy (only the regex-detectable credential class needs blocking). But a tenant set to `block` promises "fail closed on ANY identity PII" — and with NER off, a **name** (`"ประวัติของ สมชาย"`) produces zero detections, so `enforcement.match_actions` is empty and the query/prompt passes through. Emails/phones/Thai-IDs (regex) block correctly; names (NER-only) leak.

Fix: run the scan with `ner_enabled=True` **when the tenant's policy is `block`** (both search and media), so names are caught and the block is truly fail-closed. Keep `ner_enabled=False` for `allow` (cheap; only credentials matter there). Do it as a single conditional scan, not a double scan, where practical.

Tests: `block` tenant + name-only query → `SearchQueryBlockedError`; `block` tenant + name-only media prompt → 403; `allow` tenant unchanged (name passes, credential still blocks); confirm the extra NER cost only applies on the `block` path.

## A3 — [P1] Image-route raw-egress audit not itemized like search/media

**Verified (Phase 9 self-report):** `public_images.py` records raw egress as a light `policy_action` marker rather than the full findings-JSON (`{raw_egress, channel, categories}`) that `audit_raw_egress` writes for search/media.

Fix: route the image path through the same `audit_raw_egress` helper (`egress_policy.py`) so all three channels write identical, category-itemized audit rows (counts only, never values). Test: an image gen with identity PII writes a `raw_egress:true` row with `categories` populated.

---

# PART B — chat-edge-api (Go)

## B1 — [P0] SSE handler busy-spins at 100% CPU after client disconnect

**Verified:** `internal/handler/message.go`, `sendStream` (~:419-427), duplicated in `Regenerate` (~:733-738) and `Edit` (~:887-892):
```go
select {
case <-r.Context().Done():
    stream.Cancel()          // no break/nil — Done() stays ready forever
case ev, ok := <-stream.Events:
```
Once the request context is cancelled, `Done()` is permanently ready; with no pending `Events`, the `select` fires the Done case in a tight loop calling `stream.Cancel()` every iteration until the pump closes the channel — burning a core for one upstream round-trip. Many simultaneous disconnects → CPU DoS.

R0: read all three loops and `pumpStreamWithTools` (`message_service.go` ~:3483-3608) to confirm the drain-after-cancel design (the pump sends a terminal event then closes, so there's no goroutine leak — only the spin).

Fix: after the first `stream.Cancel()`, stop selecting on the Done channel — set it to a `nil` channel (a nil channel blocks forever in `select`) or use a `disconnected` bool guard, so only `Events` drives the post-cancel drain. Apply to all three handlers. Test: a stream test that cancels `r.Context()` mid-stream and asserts the handler drains to completion without a spin (e.g., assert `Cancel` is called once, or bound the loop iterations).

---

# PART C — mcp-gateway (Node)

## C1 — [P0/security] Built-in none-auth MCP servers have no auth and are shared across all users

**Verified:** `server.js` (~:130-131) — the `auth_type === 'none'` branch (filesystem, memory) skips JWT verification entirely and proxies to a single shared child. Anyone who can reach `:3200` drives the filesystem server (read/write `/data/workspace`) and the shared memory graph, with no per-user isolation.

R0: determine the deployment exposure of `:3200` (docker-compose networking — is it client-reachable or internal-only?) and how the desktop calls none-auth servers today. Decide the fix accordingly and state the reasoning.

Fix: require a valid portal JWT for none-auth servers too (verify like oauth2, just don't fetch a provider token), AND scope the filesystem root per user (`/data/workspace/{sub}` or equivalent) so one user can't read another's files. If per-user filesystem roots are impractical this phase, at minimum gate access behind JWT and document the shared-state caveat loudly. Tests: unauth request to a none-auth server → 401; two users get isolated filesystem roots (or the documented shared behavior is explicitly asserted).

## C2 — [P1] Concurrent first-acquire leaks a child process + port

**Verified:** `lib.js::InstanceManager.acquire` (~:186-234) reads `existing` (undefined), `await fetchToken`, then both concurrent callers for the same `(sub, connectorId)` fall through to `spawnChild` + `entries.set`; the second `set` overwrites the first, orphaning the first child (never swept/evicted) and leaking its port from `usedPorts` forever.

Fix: coalesce concurrent acquisitions — store a pending-promise per key so the second caller awaits the first's result, OR re-check `entries.get(key)` after the `await` and evict/reuse the loser. Test: `Promise.all([acquire(k), acquire(k)])` → exactly one child spawned, one port used.

## C3 — [P1] Proxy buffers the whole upstream body (breaks streaming MCP)

**Verified:** `server.js:156` `res.send(Buffer.from(await upstream.arrayBuffer()))` blocks until the upstream stream closes — a `text/event-stream` MCP response (long tool calls / notifications) never streams incrementally and a long call stalls.

Fix: pipe `upstream.body` through to the response instead of buffering (preserve status + content-type headers, keep `Mcp-Session-Id` semantics). Test/verification: a mocked upstream emitting chunked/event-stream data reaches the client incrementally (or, if a full harness is impractical, assert the handler pipes rather than `arrayBuffer()`s and justify).

## C4 — [P1] Node JWT verify accepts refresh tokens as bearer

**Verified:** `lib.js::verifyPortalJwt` (~:114-137) validates `alg=HS256`, `type=portal`, `exp`, `sub` — but NOT `token_use`. The Go side rejects non-`access` tokens (`auth/portal_jwt.go:83`). A refresh token carrying `type=portal` could be used as a bearer against the MCP gateway.

Fix: add `if (payload.token_use !== 'access') return null;` to match the Go verifier. Test: a `token_use=refresh` token is rejected; an `access` token passes.

## C5 — [P2] `/health` leaks user-instance count

`server.js` (~:103-108) — `/health` is unauthenticated and returns `user_instances` count. Minor info leak; either drop the field from the unauth response or gate the detailed health behind auth. Small; bundle it here.

---

# PART D — SranDesktop (C#)

## D1 — [P0/security] `read_file` can read the app's own state (conversation DB, credentials) with zero consent

**Verified:** `FolderGrantValidator.ForbiddenRoots` (`Application/Services/FolderGrantValidator.cs`) forbids GRANTING `%APPDATA%\SRAN Desktop`, but `PathGuard.BuiltInRoots[0]` is the user profile (`Infrastructure/Mcp/Tools/ToolHelpers.cs` ~:49-53), and the app-state dir is UNDER the user profile — so `PathGuard.Resolve` allows it. `ReadFileTool.DefaultPermission => AllowAlways`, so the model can read `data.db` (all conversations, plaintext), `settings.json`, `connectors.json`, `credentials.dat` (DPAPI-encrypted) with no consent prompt. Combined with an outbound channel (`open_url`/a connector), that's a prompt-injection exfil path for the whole chat history.

Fix: add a HARD DENY of the app-state subtree(s) in `PathGuard.Resolve`, checked BEFORE any allow-root (built-in OR granted), reusing `FolderGrantValidator.AppStateDir` (and ideally `%LOCALAPPDATA%\SRAN Desktop` for the WebView2 dir). No path — built-in, granted, or `..\`-normalized — may resolve inside it. R0: confirm the exact app-state paths (`DefaultDbPath`, `DpapiCredentialStore` path, connectors.json, skills dir) so the deny covers all of them. Tests: `read_file`/`write_file`/`list_directory`/`search_files` targeting `data.db`, `credentials.dat`, `settings.json`, and a `..\`-escape into the state dir → all denied; a normal doc under the user profile still allowed; a granted folder still allowed.

## D2 — [P1] Stale image attachments leak across turns (image-mode → next normal turn)

**Verified:** `ChatViewModel.SendAsync` (~:738) unconditionally sets `_pendingTurnImagePaths = attachedImagePaths;`. Only the gateway path consumes+clears it (`RunTurnAsync` ~:908-909); the chat-edge path (`RunChatEdgeTurnAsync`) never reads or nulls it. So an image-mode turn WITH attachments leaves the paths set → the NEXT normal gateway turn silently re-attaches those old images as vision blocks.

Fix: scope the pending image paths to the turn that owns them — capture as a local in `SendAsync`, or clear `_pendingTurnImagePaths` at the top of `RunChatEdgeTurnAsync` (and anywhere a turn ends without consuming it). Test: image-mode turn with attachments, then a normal turn → the normal turn's gateway request carries NO image blocks.

## D3 — [P1] Agent-sync queue strands ids on the offline path

**Verified:** `AgentConversationSyncService.DrainQueueAsync` snapshots + `_pending.Clear()` up front, then on server-unreachable `return`s without re-queuing the undrained ids. They only re-sync if the app is later restarted (`SyncAllAsync` re-discovers unsynced rows) — but there is no periodic `SyncAllAsync`, so a specific conversation can stay unsynced indefinitely if the app runs for a long time and that conversation gets no new turns.

Fix: on the offline `return`, re-add the not-yet-processed ids back to `_pending` (so the next debounce/trigger retries them). Optionally add a low-frequency periodic `SyncAllAsync` safety sweep. Test: queue 3 ids, first push throws offline → remaining ids are still pending (not lost); next drain retries them.

## D4 — [P1] MCP health sweep is serial and can stall on one hung connector

**Verified:** `McpConnectorService.RefreshRemoteToolsAsync` (~:149) awaits `ConnectOneAsync` per connector in a loop with each connector's ~30s timeout, on the health-timer thread — one slow/hanging server serializes the whole sweep and the post-login initial dial.

Fix: parallelize with `Task.WhenAll` and a bounded per-dial timeout so one hung server can't block the others. Preserve `StatusChanged` semantics. Test: two connectors, one that hangs past the per-dial timeout → the other still resolves within a bounded time and its status updates.

## D5 — [P2] Dispose the message-search CancellationTokenSource

`ConversationListViewModel._messageSearchCts` (~:240) is cancelled but never disposed on replacement. Minor handle churn (the VM is a singleton, so not a leak that grows unbounded, but tidy it). Dispose the previous CTS when creating a new one. No test needed if trivial; mention it.

---

## Final verification checklist

1. Per stack: state what you ran and that it's green — backend pytest (A1–A3 + regressions), Go test (B1), node test (C1–C4), desktop `dotnet build` 0/0 + `dotnet test` (D1–D5). No existing test weakened.
2. **Security re-statement (written):** for D1 and C1, describe before/after — what an attacker-controlled model output (D1) or an unauthenticated `:3200` client (C1) could read/do, before vs after.
3. **DLP posture:** confirm A2 makes `block` truly fail-closed (names now caught) and `allow` is byte-for-byte unchanged; A3 makes all three raw-egress channels audit-identical.
4. Summarize changes file by file per stack. List follow-up for Phase 11 (web reliability: test harness for the 3 Next apps, production cookie domain, cross-service contract tests, DLP streaming tail-window) WITHOUT implementing them.
