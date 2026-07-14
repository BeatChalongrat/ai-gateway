# DESKTOP FIX — PHASE 3 HOTFIX: sync wedges permanently after a web-deleted mirror

Small, surgical fix in **SranDesktop** (Windows WPF, .NET 8, at `SranDesktop/`). Desktop-only — **do NOT touch chat-edge or the gateway**; the server-side resurrect logic from Phase 3 is correct and stays as-is. Do not regress anything from Phases 1–3.

## The bug (verified end-to-end in code)

`AgentConversationSyncService.PushConversationAsync` (`SranDesktop/src/SranDesktop.Infrastructure/Services/AgentConversationSyncService.cs`, lines ~129–170) only calls `ImportConversationAsync` when the local conversation has **no** `server_id` (lines 132–140). The server-side resurrect of a web-deleted mirror lives inside `ImportService.getExisting` and is therefore only reachable through `POST /conversations/import` — which a synced conversation never calls again.

Failure sequence:

1. A conversation syncs → local row stores `server_id`.
2. The user deletes the mirror in the web UI (soft delete).
3. Next sync: `server_id` is set → conversation import is skipped → `ImportMessagesAsync` hits `POST /conversations/{id}/messages/import` → `AssertOwnership` returns not-found for a soft-deleted conversation (`services/chat-edge-api/internal/service/conversation_service.go:85-101`, by design) → **HTTP 404**.
4. `ChatEdgeApiClient` surfaces that as `HttpRequestException` with `StatusCode == NotFound`. In `SyncAllAsync` (lines 65–70) and `DrainQueueAsync` (lines 112–116) the broad `catch (HttpRequestException or TaskCanceledException)` — written for "server unreachable" — catches it and **`return`s, aborting the whole loop**.

Net effect: one web-deleted mirror permanently stops sync for **every** conversation, on every trigger, silently (Debug-level log only).

## The fix — two parts, both in `AgentConversationSyncService`

### 1. Recover from a dead mirror (re-import → resurrect → retry)

In `PushConversationAsync`, when `ImportMessagesAsync` throws `HttpRequestException` with `StatusCode == System.Net.HttpStatusCode.NotFound` (existing precedent for this exact pattern: `ConversationListViewModel.cs:213`):

- Log a warning ("server mirror missing for {Id}, re-importing").
- Call `ImportConversationAsync` again with the SAME local conversation id as `client_id` (the server resurrects the soft-deleted row and returns it — the server id will normally be unchanged, but treat the returned id as authoritative).
- Persist the returned id via `SetConversationServerIdAsync`, update the local `serverId` variable, and **retry the message push once**. Guard with a single boolean per `PushConversationAsync` call — if the retry 404s again (should be impossible; a hard-deleted row means `ImportConversationAsync` created a fresh one), let the exception propagate rather than looping.
- Structure note: this method is plain async (no iterator restrictions) — a small local function or an outer retry loop with a `reimported` flag both work; keep it readable.

### 2. A per-conversation failure must not abort the sweep

In `SyncAllAsync` and `DrainQueueAsync`, split the current broad catch:

- **Connection-level** failures (`HttpRequestException` with `StatusCode == null`, and `TaskCanceledException`) → keep today's behavior: Debug log + `return` (the server is unreachable; hammering the remaining conversations is pointless).
- **HTTP-level** failures (`HttpRequestException` with a non-null `StatusCode`) → log a Warning for that conversation and **`continue`** to the next one. One poisoned conversation must never starve the rest.
- `DrainQueueAsync` already has a generic `catch (Exception)` → `continue` path for other errors — align the ordering of the catch clauses so the status-code case is evaluated before the connection case (exception filters `when (...)` make this clean).

Behavior table to implement exactly:

| Failure during one conversation's push | SyncAllAsync / DrainQueueAsync |
|---|---|
| `HttpRequestException`, `StatusCode == null` (offline) | Debug log, stop the whole sweep (retried next trigger) |
| `TaskCanceledException` (timeout) | same as offline |
| `HttpRequestException`, `StatusCode == 404` on messages/import | handled INSIDE `PushConversationAsync` (re-import + one retry); only if the retry also fails does it surface, then: Warning log, continue to next conversation |
| `HttpRequestException`, any other status (401/422/500…) | Warning log, continue to next conversation |
| Any other exception | Warning log, continue (existing DrainQueueAsync behavior; add the same to SyncAllAsync) |

### Prerequisite check (do first)

Read `ChatEdgeApiClient.ImportMessagesAsync` / `ImportConversationAsync` and the shared `ReadAsync`/`EnsureSuccessAsync` helpers to confirm a non-success response throws `HttpRequestException` **with `StatusCode` populated** (the codebase pattern at `ConversationListViewModel.cs:213` implies it does). If any path throws without a status code, fix that helper so the status is attached — the whole fix keys off it.

## Tests (extend the existing Phase 3 sync test file, mocked `IChatEdgeApiClient` + temp SQLite)

1. **Resurrect-and-retry:** conversation with `server_id` set; `ImportMessagesAsync` throws 404 once, then succeeds after `ImportConversationAsync` is called again → messages marked synced; `ImportConversationAsync` called exactly once; final `server_id` matches the re-import response (use a DIFFERENT id in the mock response to prove the client adopts the authoritative one).
2. **No infinite loop:** `ImportMessagesAsync` throws 404 on both attempts → exception surfaces from `PushConversationAsync`; `ImportConversationAsync` called exactly once.
3. **Sweep isolation:** two local conversations; the first fails with 422; the second must still be pushed (`SyncAllAsync`).
4. **Offline still stops the sweep:** first conversation throws `HttpRequestException` with null `StatusCode` → second conversation is NOT attempted.
5. Existing Phase 3 sync tests (first-sync, second-sync-sends-nothing, setting-off, dedupe regression) remain green and unmodified.

## Final verification

1. `dotnet build SranDesktop/SranDesktop.sln` 0 errors / 0 new warnings; `dotnet test` all green.
2. Trace review: read the final `PushConversationAsync` + both sweep methods top-to-bottom and confirm (a) at most ONE re-import per push call, (b) offline short-circuits the sweep, HTTP errors don't, (c) no behavior change when the mirror is healthy (the happy path makes exactly the same calls as before).
3. Summarize the change file by file. No other refactoring — this is a hotfix.
