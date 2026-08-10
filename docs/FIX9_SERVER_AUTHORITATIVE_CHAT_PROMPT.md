# FIX 9 — Server-authoritative chat (web↔desktop show the same conversation, ChatGPT/Claude-style)

> **Read this first — you may have no prior context.** Monorepo at the repo root: a DLP-masking AI **gateway** (Python `backend/` + Go `services/gateway/`), **chat-edge-api** (Go, `services/chat-edge-api/` — the conversation/history/SSE server), **mcp-gateway** (Node), three Next.js apps (`apps/web-chat`, `portal`, `admin-ui`), and a **WPF .NET 8 desktop app** at `SranDesktop/` (Windows-only, an agentic assistant like Claude Desktop). Core property: **all user content is DLP-masked before any third-party LLM provider sees it** — never weaken that. Both the desktop and web-chat authenticate as the same user (Portal JWT) and talk to the same chat-edge server.

## The goal
Today, a desktop agent conversation is stored **local-first in SQLite** and only *eventually* mirrored to the server (debounced push-sync), so **web-chat and desktop don't reliably show the same conversation**. The big players (ChatGPT/Claude/Gemini) don't do local-first sync — the **server is the single source of truth**, and every client is a view of it. We're moving to that model, with ONE nuance the agent app forces:

- **Visible conversation (user text + final assistant text) → SERVER-AUTHORITATIVE.** Written to the server as the turn completes; read from the server; cached locally only for offline viewing. This makes web and desktop show the same chat.
- **Agent working state (the tool chain: assistant-with-tool_calls, tool-role results, screenshots, plus tool_executions / permissions / drafts / notes / branch_sets) → STAYS LOCAL.** The server schema literally cannot store it (chat-edge messages accept only role user/assistant + text — verified below), and the agent needs the richer local chain to *continue* a task.

So: **the transcript everyone sees is the server's; the agent's private working memory stays on the desktop.**

## Working rules
- **Read every file you touch IN FULL; do the R0 below and answer it in your summary before changing code.**
- **Do NOT `git push`** (commit locally only; the operator pushes). **Do NOT edit to force a test pass**, don't commit secrets, don't weaken DLP/consent/auth.
- **Runtime is the bar.** This project has repeatedly shipped "tests green + code correct but it doesn't actually work when clicked." A change is NOT done until proven END-TO-END on the running stack (send a turn on desktop → see it in web-chat, and vice versa). Each acceptance item below has a live-proof; if you can't run it, mark UNVERIFIED — don't claim done.
- Desktop is `net8.0-windows`/WPF (Windows only): `dotnet build SranDesktop/SranDesktop.sln -c Release` + `dotnet test`. Backend/chat-edge via `docker compose` (`Makefile`). New UI strings go in BOTH `En`/`Th` in `SranDesktop/src/SranDesktop.Presentation/Localization/Dict.cs` (`DictParityTests` enforces).

## R0 — Confirm the map (all verified in a prior recon; re-confirm exact lines, they drift)
1. **Turn paths** (`SranDesktop/src/SranDesktop.Presentation/ViewModels/ChatViewModel.cs`): the dispatch in `RunTurnAsync` (~:1010, branch ~:1018). The **chat-edge path** (`RunChatEdgeTurnAsync` ~:1335) already streams through chat-edge which **stores both messages server-side** and adopts the server message id (`IsServerMessage=true` ~:1358). The **agent path** calls the gateway `/v1/chat/completions` **directly** (`GatewayApiClient`), bypassing chat-edge, and persists to LOCAL SQLite in the `!_turnUsedChatEdge` finally block (~:925-931: `PersistTurnAsync` + `QueueConversationSync` debounced). Confirm.
2. **The server write already exists**: `AgentConversationSyncService.PushConversationAsync` (~:202-268) already (a) ensures the server conversation via `ImportConversationAsync` (idempotent by local id as `client_id`) and (b) appends user/assistant TEXT via `ImportMessagesAsync` (text-only, idempotent). Today it's debounced fire-and-forget (`QueueConversationSync`, ~5s). Confirm this is the mechanism to promote to authoritative.
3. **The message read merge** (`OpenConversationAsync` ~:628-669): today it MERGES server (`ListMessagesAsync`) + local (`GetMessagesAsync`, which supplies the tool chain via `AgentChainCodec.GroupChains`), dedup by id, with an offline `catch` that falls back to local (~:648). Confirm `ListMessagesAsync` returns full assistant text (it does — `MessageDto` has `content/role/model_id`), and that the tool chain is attached via `vm.AgentToolChain` (~:686).
4. **Server can't hold the tool chain**: `services/chat-edge-api/internal/service/import_service.go:177-214` rejects any role ≠ user/assistant and stores only role/content/model_id/client_id/created_at — no tool_calls, no tool role, no screenshots. So the tool chain is structurally local-only. Confirm.
5. **`BuildGatewayMessages`** (~:1970, ~:2084) rebuilds the agent request from the in-memory `Messages` list, prepending each message's LOCAL `AgentToolChain`. So the agent's working context comes from the local chain, NOT the server transcript. Confirm — this is why agent working state must stay local even when the visible transcript is server-authoritative.
6. Confirm the conversation LIST is already server-read-into-local-cache (`ConversationListViewModel` ~:610-616 pulls server then repaints local), and identity is the same Portal JWT on both clients.

## Design — make it server-authoritative with the least-invasive seam (Option a from recon)
Do NOT re-home the agent loop through chat-edge (that's option b — huge, unsupported). Instead:

### Part A — Land the visible turn on the server SYNCHRONOUSLY (not debounced)
- In the agent-path completion (`ChatViewModel.cs` ~:925-931), after the turn finishes, **await** the server write of the user+assistant TEXT (reuse `AgentConversationSyncService`'s import path — ensure-conversation + append-messages) so the turn is on the server before the UI considers it "done" (or awaited-with-a-brief-optimistic-UI, so it's reliably server-landed, not eventually). Keep `PersistTurnAsync` writing the LOCAL tool chain unchanged (that's the agent working state).
- Adopt the server message ids for the user+assistant rows where possible (like the chat-edge path does), or keep the local-id-as-client_id mapping already used — but the server copy must exist reliably per turn. Handle failure: if the server write fails (offline), keep the local copy and retry (the existing offline re-queue) — but surface that the turn isn't yet synced rather than silently diverging.
- Result: a desktop turn appears in web-chat immediately (web reads the same server conversation).

### Part B — Prefer the SERVER on read; keep local as cache + tool-chain source
- In `OpenConversationAsync` (~:628-669): make the **visible transcript** come from the server (`ListMessagesAsync`) as the source of truth when online — so a conversation edited/continued on web shows its latest messages on desktop. Keep the LOCAL read for (i) the **tool chain** (always local — `AgentChainCodec` rows, attached as `AgentToolChain`) and (ii) **offline fallback** (when the server call throws, render from local cache — the existing `catch` shape).
- Web-created conversations: their messages now load from the server into the desktop transcript (today only metadata pulls; this closes the "web→desktop message bodies" gap). The tool chain will simply be empty for web-origin conversations (they have no agent tools) — that's correct.
- Keep writing local rows as a cache on read (so offline still works).

### Part C — Two-way visibility + deletes (scope: chat only, NOT files this pass)
- **Web→desktop:** the list pull already surfaces web conversations; Part B now loads their messages. Confirm a conversation created on web appears on desktop with its messages.
- **Desktop→web:** Part A lands agent turns on the server → they appear in web-chat (once `message_count>0`, per web-chat's sidebar filter).
- **Deletes:** make a delete on either client propagate. Desktop delete already calls the server delete API (confirm) — ensure a server-side delete is reflected on desktop (the pull's full-list reconciliation removes `source='server'` rows; decide how a deleted *synced-local* conversation is handled — today it can be resurrected by re-push, which is wrong for a user-intended delete; a deleted conversation should stay deleted). Fix the resurrection-on-delete footgun if present.
- **Files/images across clients: OUT OF SCOPE this pass** — desktop agent conversations rarely have files, the server has no file idempotency key for cross-client dedup, and it's a separate migration + blob-transfer effort. Note it as a tracked follow-up (FIX 10). Do NOT half-build file sync.

### Part D — Offline behavior (read-cache, not online-only)
- Server-authoritative WHEN ONLINE; fall back to the local cache when offline (list + transcript). The agent tool chain is always local (offline agent-resume must keep working). Do NOT make chat hard-online-only — that would break agent resume and offline history. Keep the existing graceful-degradation catches.

## Acceptance / live proof (the bar)
1. **Same conversation both places:** send an agent turn on desktop → open web-chat as the same user → the conversation + messages appear. Create/continue a conversation on web → open desktop → its messages appear in the transcript. Show both directions.
2. **Agent working state preserved:** a multi-step tool task on desktop, then reopen the conversation → the tool chain (tool cards / working context) is still there (local), AND the visible transcript matches the server. Continue the task → `BuildGatewayMessages` still has the local tool context (agent resume works).
3. **Offline:** disconnect → the sidebar + an already-opened conversation still render from cache; agent resume still works from local; on reconnect, pending turns land on the server.
4. **Deletes:** delete on desktop → gone on web (and stays gone, not resurrected). Delete on web → gone on desktop.
5. **DLP intact:** a turn with PII is still masked before the provider (server write stores the same masked transcript the gateway produced; no raw PII newly persisted).
6. Tests: desktop `dotnet build` 0/0 + `dotnet test` (sync/persist/merge logic where extractable); chat-edge `go test`. Live-proof table filled honestly; any row you can't run → UNVERIFIED.

## Report
- R0 answers (the map, confirmed with current line numbers).
- What changed (should center on `ChatViewModel.cs` turn-landing + read-merge, `AgentConversationSyncService` promote-to-synchronous, and the delete/resurrection fix) — file by file.
- The live-proof table (both-directions visibility, agent state preserved, offline, deletes, DLP).
- Explicit statement: visible transcript is now server-authoritative (web↔desktop consistent), agent working state stays local, files deferred to FIX 10. Two local commits max, nothing pushed.
