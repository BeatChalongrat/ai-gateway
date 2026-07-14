# DESKTOP FIX — PHASE 3: Data Safety (migrations, atomic writes, agent-conversation sync)

You are working on **SranDesktop** (Windows WPF, .NET 8, at `SranDesktop/`) and the **chat-edge-api** Go service (at `services/chat-edge-api/`). Phases 1–2 (agent reliability, context management) are merged — do not regress them.

## The problems this phase solves (all verified in code)

1. **No SQLite migration path.** `SranDesktop/src/SranDesktop.Infrastructure/Storage/SqliteConversationStore.cs` creates its schema with `CREATE TABLE IF NOT EXISTS` only (lines 13–88) — no `PRAGMA user_version`, no migration runner. Any future column addition silently never reaches existing installs.
2. **Non-atomic local writes, silent data loss on corruption.** Direct writes with no temp-file-then-rename at: `JsonSettingsService.cs:37` (`File.WriteAllText`), `DpapiCredentialStore.cs:86` (`File.WriteAllBytes`), `JsonProjectStore.cs:134`, `McpConnectorService.cs:355` (connector state), `SkillManager.cs:141` (disabled.json). On load-failure, settings and credentials silently reset to defaults with **no backup of the corrupt file**.
3. **Turn persistence is not transactional.** `ChatViewModel.PersistTurnAsync` (~line 714) upserts the user row and the assistant row in two separate calls under a best-effort catch — a crash between them leaves a user message with no reply.
4. **Agent-mode conversations are local-only.** Gateway-path turns are persisted ONLY to local SQLite (`PersistTurnAsync` runs when `!_turnUsedChatEdge`, ~line 705). Reinstall or a second device loses every agent conversation. chat-edge has **no import/append-without-generation endpoint** (verified: no bulk/sync/import code exists; every message insert path is coupled to LLM generation except the raw repo primitive).
5. **Composer draft is never persisted** — a typed-but-unsent message dies with the process.

This phase = **PART A (chat-edge, Go)**: import endpoints. **PART B (desktop)**: SQLite migration framework. **PART C (desktop)**: atomic writes + corruption backups + transactional turn persistence + drafts. **PART D (desktop)**: one-way push sync of agent conversations. Order: A → B → C → D (D depends on A and B; C is independent and can be done any time after B).

## Ground rules

- Read every file you touch IN FULL before editing. The reconnaissance sections are mandatory.
- Desktop: xunit + Moq + FluentAssertions in `SranDesktop.Tests`, following `Agent/` and `Storage/` folder patterns. chat-edge: follow the service-level e2e test patterns in `internal/service/*_test.go` and the wire-shape pattern in `internal/handler/file_test.go`.
- Do NOT touch: the agent loop internals from Phases 1–2 (`ChatOrchestrator`, `GatewayChunkParser`, `GatewayApiClient`), the DLP pipeline, the Python gateway backend (`backend/`) — this phase has **zero** Python work.
- New user-facing strings → BOTH `En` and `Th` in `Dict.cs` (`DictParityTests` enforces). Natural Thai.
- Finish state: desktop `dotnet build` 0/0 + `dotnet test` all green; chat-edge `go build ./...` + `go test ./...` green (at minimum the packages you touched).

---

# PART A — chat-edge: conversation import & message append (Go)

### Verified context you must rely on (do not re-derive it wrongly)

- Router: `internal/router/router.go` — protected group under `middleware.RequireAuth` (L129–339); handlers are fields on the `Handlers` struct (L34–79) and **optional handlers are wired only `if h.X != nil`** (Folder L144, Memory L309). `sendRateLimit` is applied per-route only on generation routes — import endpoints must NOT use it.
- Auth: `middleware.CallerFrom(ctx)` yields `Caller{UserID, TenantID, TenantUUID, Role, Email}`. Ownership: `ConversationService.AssertOwnership(ctx, tenantID, userID, convID)` (conversation_service.go L88–107) returns the conversation or a not-found-style error.
- Models: `chat.conversations` and `chat.messages_masked` (internal/model/conversation.go L96–160, message.go L20–80). **IDs are DB-generated** (`gen_random_uuid()`); no code path accepts a client-supplied id today. Message `Role` has a DB CHECK allowing ONLY `user | assistant | system` — do not try to store `tool` roles.
- Raw insert primitive that skips generation: `MessageRepo.Create(ctx, *model.Message)` (internal/repository/message_repo.go L64–87). Transactions via the existing `WithTx` helper. `message_count` is app-maintained — call `ConversationRepo.IncrementMessageCount(ctx, convID, delta)` (conversation_repo.go L534) yourself. A full-text `search_vector` trigger indexes inserted messages automatically (migrations/016) — no extra work.
- Idempotency template: the ONLY existing upsert is feedback — UNIQUE index + `INSERT … ON CONFLICT` (migrations/004, feedback_repo.go L34–38). Mirror that pattern.
- Migrations: plain numbered SQL in `migrations/NNN_*.sql`, applied by `scripts/apply_migrations.sh` (tracking table `chat._migrations`). Additive changes use `ADD COLUMN IF NOT EXISTS`, nullable/defaulted (see 009/019/024/025). Next free number: check the directory and use it.
- Safe defaults for imported rows: `status='completed'`, `token_usage_* NULL`, `model_id` nullable, branch fields default (`parent_id NULL, branch_index 0, is_active true`), `secure_trace_id NULL`, canonical fields default to `'legacy'` (which excludes imported content from canonical-history replay — desired).

### A1 — Migration: client-id idempotency columns

New `migrations/0NN_desktop_import.sql`:

- `ALTER TABLE chat.conversations ADD COLUMN IF NOT EXISTS client_id TEXT;` + partial unique index on `(tenant_id, user_id, client_id) WHERE client_id IS NOT NULL`.
- `ALTER TABLE chat.messages_masked ADD COLUMN IF NOT EXISTS client_id TEXT;` + partial unique index on `(conversation_id, client_id) WHERE client_id IS NOT NULL`.
- `ALTER TABLE chat.conversations ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'server';` (values `server` | `desktop_agent`; no CHECK constraint needed — document in a comment).

Backward-compatible: all nullable/defaulted, no backfill, existing reads unaffected.

### A2 — Import endpoints

Two endpoints, both in a new optional handler (e.g. `ImportHandler`), wired `if h.Import != nil`, under `RequireAuth`, WITHOUT `sendRateLimit`:

**1. `POST /api/v1/conversations/import`** — create-or-get a conversation by client id.

Request:
```json
{"client_id":"<desktop conversation uuid>", "title":"...", "model_id":"...", "created_at":"RFC3339 optional"}
```
Behavior: upsert by `(tenant, user, client_id)` — if a conversation with that client_id exists for this caller, return it (200); otherwise insert with `origin='desktop_agent'` (201). Response either way:
```json
{"conversation":{...standard conversationResponse...}, "created": true|false}
```
Validation: `client_id` required non-empty (422 otherwise); `model_id` optional (do NOT hard-validate against the live model catalog — an old local conversation may reference a retired model; store as-is).

**2. `POST /api/v1/conversations/{id}/messages/import`** — append messages without generation.

Request:
```json
{"messages":[
  {"client_id":"<desktop message id>", "role":"user|assistant", "content":"...",
   "model_id":"... optional", "created_at":"RFC3339"}
]}
```
Behavior:
- `AssertOwnership` first (404 on failure, same non-leaking behavior as other routes).
- Reject: empty array, >200 messages per request, any message with role other than `user`/`assistant`, empty `client_id`, content longer than 128 KB — all 422 with the standard error envelope.
- Insert in ONE transaction (`WithTx`): `ON CONFLICT (conversation_id, client_id) DO NOTHING` per message; `status='completed'`; preserve the supplied `created_at` (this is historical data — do NOT stamp NOW()).
- `IncrementMessageCount` by the number of rows actually inserted (conflicts don't count — use the insert's returned/affected count, not `len(messages)`).
- Response 200: `{"imported": N, "skipped": M}` where skipped = conflict-deduped.

Idempotency guarantee to state in a code comment and prove in tests: replaying the exact same request produces `imported:0, skipped:N` and no duplicate rows.

### A3 — Tests (Go)

- Wire-shape tests for both request/response DTOs (pattern: `internal/handler/file_test.go`).
- Service/repo-level tests (pattern: existing `internal/service/*_test.go` / `internal/repository/*_test.go` — reuse their DB test harness): import creates conversation with origin; second import with same client_id returns existing (`created:false`); message import inserts with preserved timestamps; exact-replay yields 0 imported; message_count incremented by actually-inserted count only; cross-tenant/user access → not-found; oversized batch and bad role → 422; a `tool` role in the payload → 422 (never reaches the DB CHECK).

---

# PART B — Desktop: SQLite migration framework (C#)

### B0 — Reconnaissance (answer in final summary)

1. `SqliteConversationStore.cs` full read: current `Schema` (baseline), `InitializeAsync` single-flight pattern, `DefaultDbPath()`.
2. Where does `SqliteScheduledTaskStore` live and does it use the SAME database file as `SqliteConversationStore` or its own? Apply the migration framework to every SQLite database the app owns (shared runner, per-DB version).
3. `IConversationStore` interface: current members (you will add batch upsert in PART C and sync-state members in PART D).

### B1 — Migration runner

New `SranDesktop/src/SranDesktop.Infrastructure/Storage/SqliteMigrator.cs`:

- `public static async Task MigrateAsync(SqliteConnection conn, IReadOnlyList<string> migrations, ILogger? logger, CancellationToken ct)` — reads `PRAGMA user_version`; for each migration with index ≥ current version: run it inside a transaction together with `PRAGMA user_version = N+1`; log each applied step. (Note: `PRAGMA user_version` cannot be parameterized — build the SQL string from the integer index, never from user input.)
- Migration list is a `static readonly string[]` per store. **Migration 0 (baseline)** = the existing `Schema` string verbatim. A fresh install runs 0..N; an existing install (user_version 0 but tables exist) must not fail — baseline uses `CREATE TABLE IF NOT EXISTS`, so running it on an existing DB is a no-op; from then on user_version is authoritative. Document this bootstrap rule in a comment.
- `SqliteConversationStore.InitializeAsync` switches from executing `Schema` to calling the migrator.

### B2 — First real migrations (needed by PART C/D)

- Migration 1: `ALTER TABLE conversations ADD COLUMN server_id TEXT;` + `ALTER TABLE messages ADD COLUMN server_id TEXT;` + index on `messages(server_id)`. (The `source`/`last_synced_at` columns already exist in the baseline — verified — so only server-id mapping is new.) SQLite has no `ADD COLUMN IF NOT EXISTS` — that's exactly why the version gate must be correct.
- Migration 2: `CREATE TABLE IF NOT EXISTS drafts (conversation_id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL);`

### B3 — Tests

`SqliteMigratorTests` (in-memory or temp-file SQLite, pattern: existing `Storage/SqliteConversationStoreTests`):
- Fresh DB → all migrations applied, user_version == count.
- DB at version 1 → only later migrations run (prove with a probe table/column).
- Migration failure mid-list → transaction rolled back, user_version unchanged for the failed step, earlier steps retained.
- Legacy DB simulation: create the baseline schema manually with user_version 0, then migrate → no exception, columns from migration 1 exist afterwards.

---

# PART C — Desktop: atomic writes, corruption backups, transactional turns, drafts

### C1 — Atomic file writer

New `SranDesktop/src/SranDesktop.Infrastructure/Storage/AtomicFile.cs`:

- `Write(string path, string contents)` / `WriteBytes(string path, byte[] bytes)`: write to `path + ".tmp"`, flush to disk (`FileStream` + `Flush(flushToDisk: true)`), then `File.Move(tmp, path, overwrite: true)`. Clean up a stale `.tmp` from a previous crash before writing.
- Apply at every direct-write site listed in problem #2: `JsonSettingsService.cs:37`, `DpapiCredentialStore.cs:86`, `JsonProjectStore.cs:134`, `McpConnectorService.cs:355`, `SkillManager.cs:141`. (Do NOT change `WriteFileTool` — agent-written user files are out of scope.)

### C2 — Corruption backup instead of silent reset

In `JsonSettingsService.LoadOrDefault` (catch at ~lines 42–58) and `DpapiCredentialStore` (its `CryptographicException`/`JsonException` catch at ~lines 68–76): before falling back to defaults/empty, copy the unreadable file to `{name}.corrupt-{yyyyMMdd-HHmmss}` next to it and log an **Error** (not silent). Behavior otherwise unchanged (app still starts with defaults). Add a settings-load test proving the backup file appears.

### C3 — Transactional turn persistence

- Add `Task UpsertMessagesAsync(IReadOnlyList<Message> messages, CancellationToken ct = default)` to `IConversationStore` + `SqliteConversationStore` — all rows in ONE SQLite transaction.
- `ChatViewModel.PersistTurnAsync` uses it (user + assistant in one call). Keep the existing best-effort catch and the skip-errored-assistant behavior.
- Test: transaction atomicity — force a failure on the second row (e.g. constraint violation), assert the first row was not persisted.

### C4 — Draft persistence

- On composer text change (debounce ~1s, and immediately on conversation switch/app exit): save `InputText` to the `drafts` table for the current conversation (empty text deletes the row). On opening a conversation: restore the draft. On successful send: delete it.
- Keep it invisible: no UI beyond the text reappearing. Respect the existing MVVM structure (`ChatViewModel` owns `InputText` — put storage behind `IConversationStore` or a small `IDraftStore` on top of the same DB).
- Tests: store round-trip; empty-text deletes; send clears.

---

# PART D — Desktop: one-way push sync of agent conversations

### D0 — Reconnaissance (answer in final summary)

1. `ConversationSyncService` (`SranDesktop.Infrastructure/Services/`): exact reconciliation behavior — how server conversations are merged into local SQLite, and what happens to local `source='local'` rows during reconciliation (they must never be deleted by it — verify and state the guarantee).
2. `ConversationListViewModel.LoadAsync` merge logic — where a duplicate could appear once a local conversation also exists server-side.
3. `IChatEdgeApiClient` — where to add the two new import calls (follow the existing request/DTO conventions in `ChatEdgeApiClient.cs`, including `Deadline()` usage — use the 30s default).

### D1 — API client methods

Add to `IChatEdgeApiClient`/`ChatEdgeApiClient`:
- `ImportConversationAsync(clientId, title, modelId, createdAt, ct)` → `POST api/v1/conversations/import`, returns the server conversation id + created flag.
- `ImportMessagesAsync(serverConversationId, batch, ct)` → `POST api/v1/conversations/{id}/messages/import`, returns imported/skipped counts. DTOs mirror PART A exactly.

### D2 — Sync service

New `AgentConversationSyncService` (`SranDesktop.Infrastructure/Services/`, behind an Application-layer interface):

- **What syncs:** conversations with `source='local'`; messages with role `user`/`assistant` and non-empty string content whose `server_id IS NULL`. Tool executions, screenshots, and tool-call metadata do NOT sync (chat-edge cannot represent them — verified; they remain local-only, which is fine: the transcript is the valuable part).
- **Flow per conversation:** ensure server conversation exists (`ImportConversationAsync` with the local id as `client_id`; store returned id in `conversations.server_id`, set `last_synced_at`) → send unsynced messages in `created_at,sort_order` order, batches of ≤200 → on success mark each local message's `server_id` (server doesn't return per-message ids — the local message id IS the client_id, so marking = set `server_id = 'synced'`? NO: add the mapping honestly — have the endpoint response include `imported`/`skipped` only, and mark local rows as synced with a dedicated flag instead. Concretely: reuse `messages.server_id` as a nullable "synced marker" storing the SERVER CONVERSATION id, or add a `synced_at TEXT` column in a migration — pick one, document it, and be consistent).
- **Triggers:** (a) after `PersistTurnAsync` completes, debounced ~5s, fire-and-forget with logging; (b) once on startup after auth; (c) skipped silently when offline (any HttpRequestException → log Debug, retry next trigger). Never block or fail the UI path.
- **Setting:** `SyncAgentConversations` (bool, default true) in `UserSettings` + a toggle in Settings → Desktop Integration. Dict keys (EN/TH), e.g. `settings.syncAgentChats` — EN `"Back up agent chats to your account"` / TH `"สำรองแชทโหมดเอเจนต์ไปยังบัญชีของคุณ"` (+ a one-line description key). When off, the service does nothing.
- **No duplicate rows in the sidebar:** after a local conversation gains a `server_id`, the server-list reconciliation (D0.1/D0.2) must treat the server copy and the local copy as the SAME conversation — dedupe by `server_id`. The local row stays authoritative (agent mode still reads/writes local); the server copy is a backup mirror. Write a regression test for the merge (a local conversation with server_id X + a server list containing X → one sidebar entry).
- **Deletes:** when the user deletes a local agent conversation that has a `server_id`, also call the existing server delete endpoint, best-effort. Server-side deletions of the mirror do NOT delete local data (one-way push).

### D3 — Tests

- Sync service with mocked `IChatEdgeApiClient` + real temp SQLite: first sync imports conversation + all messages and marks them; second sync sends nothing; a new turn syncs only the two new messages; offline (client throws) → no state corruption, retried next trigger; setting off → zero API calls.
- Merge dedupe regression test per D2.
- `DictParityTests` still green.

---

## Final verification checklist

1. chat-edge: `go build ./...`, `go test ./...` green; migration file numbered correctly and listed by `scripts/apply_migrations.sh` logic. Desktop: `dotnet build` 0/0, `dotnet test` all green.
2. Idempotency proof: state in the summary the exact behavior of replaying (a) conversation import, (b) message import, (c) desktop sync of an already-synced conversation.
3. Data-safety trace: describe what now happens on (a) power loss during settings save, (b) corrupt credentials file at startup, (c) crash between user and assistant row of a turn, (d) reinstalling the app after conversations synced.
4. Summarize changes file by file, answer ALL reconnaissance questions (B0, D0), and list follow-up risks for Phase 4 (progressive markdown streaming + syntax highlighting) WITHOUT implementing them.
