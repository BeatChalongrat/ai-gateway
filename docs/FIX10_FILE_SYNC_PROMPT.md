# FIX 10 — File/image sync across web↔desktop (user-attached files show on both clients)

> **Read this first — you may have no prior context.** Monorepo at the repo root: a DLP-masking AI **gateway** (Python `backend/` + Go `services/gateway/`), **chat-edge-api** (Go, `services/chat-edge-api/` — the conversation/history/SSE server + file storage), **mcp-gateway** (Node), three Next.js apps (`apps/web-chat`, `portal`, `admin-ui`), and a **WPF .NET 8 desktop app** at `SranDesktop/` (Windows-only, an agentic assistant like Claude Desktop). Core property: **all user content is DLP-masked before any third-party LLM provider sees it** — never weaken that. Both desktop and web-chat authenticate as the same user (Portal JWT) against the same chat-edge server.
>
> **Prereq context:** FIX 9 made the *visible chat transcript* server-authoritative (web↔desktop show the same conversation text). FIX 9 **explicitly deferred files to FIX 10** — this is FIX 10.

## The goal (scope locked by the user)
Make **user-attached files/images visible on BOTH clients** for the same conversation:
- A file the user uploads on **web-chat** must render/download on **desktop** when they open that conversation.
- A file the user attaches on **desktop** must show under its message on **web-chat**.

**In scope:** user-attached files + web-uploaded files (both directions), plus a server-side dedup key so re-sync doesn't duplicate.
**OUT of scope (do NOT build this pass):** pushing agent-*generated* artifacts (run_python charts, locally-generated docx/xlsx) up to the server. That's a separate, larger effort (artifact capture + upload path). Note it as a tracked follow-up (FIX 11). Do NOT half-build it.
**Already works — leave alone:** image/video *generation* already lands as server-side `conversation_files` (`is_generated=true`, `message_id` set) and is already visible to web-chat and re-rendered on desktop. No work needed there — just don't regress it.

## Working rules
- **Read every file you touch IN FULL; do the R0 below and answer it in your report BEFORE changing code.** Line numbers below are from a prior recon and drift — re-confirm exact lines.
- **Do NOT `git push`** (commit locally only; the operator pushes). **Do NOT edit to force a test pass.** Don't commit secrets. **Don't weaken DLP/consent/auth.**
- **Runtime is the bar.** This project has repeatedly shipped "tests green + code correct but it doesn't actually work when clicked" (FIX 1 dormant, FIX 6 phantom project, the stale-build toast). A change is NOT done until proven END-TO-END on the running stack (upload a file on web → see + download it on desktop, and vice versa). Each acceptance item has a live-proof; if you can't run it, mark **UNVERIFIED** — don't claim done.
- **Rebuild before testing.** Desktop must be rebuilt + restarted before any live test (a stale binary silently runs old code — this bit us on the toast crash). `dotnet build SranDesktop/SranDesktop.sln -c Release`, then run the fresh binary.
- Desktop is `net8.0-windows`/WPF (Windows only): build as above + `dotnet test`. Backend/chat-edge via `docker compose` (`Makefile`). New UI strings go in BOTH `En`/`Th` in `SranDesktop/src/SranDesktop.Presentation/Localization/Dict.cs` (`DictParityTests` enforces).

## R0 — Confirm the map (from recon; re-confirm exact lines + answer in your report)
**Server (chat-edge) — the file infra EXISTS and is complete:**
1. Routes (`services/chat-edge-api/internal/router/router.go` ~:221-224, behind RequireAuth): `POST /api/v1/conversations/{id}/files` (`handler/file.go` ~:122 Upload), `GET /api/v1/conversations/{id}/files` (~:189 List), `GET /api/v1/files/{fileID}/download` (~:217 Download, **307-redirects** to a presigned URL when the backend is S3, else streams bytes), `DELETE /api/v1/files/{fileID}` (~:258). Confirm shapes.
2. Wire model `fileResponse`/`FileToWire` (`handler/file.go` ~:45-98): `{id, conversation_id, message_id?, filename, content_type, file_size_bytes, extraction_status, created_at, download_url, is_generated?, parent_file_id?, version?}`. List returns `{"files":[...]}`. Confirm.
3. Table `chat.conversation_files` (`migrations/006_...`, +012, +013): has `message_id` (nullable, FK `messages_masked`), `tenant_id`, `user_id`, `is_generated`, `storage_path`, etc. **Confirm it has NO `client_id`** (migration `026_desktop_import.sql` added `client_id` only to `conversations` + `messages_masked`, NOT `conversation_files`). This missing dedup key is the one net-new server change.
4. A file is linked to a message via `file_ids` on the message POST (`handler/message.go` ~:303,333). List/stream responses **embed `files[]` per message** via `ListByMessageIDs` (`message.go` ~:87,270-283) and the SSE `message_files` event. Confirm.
5. Storage backend (`internal/storage/storage.go` `Backend` iface; `factory.go` picks `local`|`s3`). Confirm local = streamed bytes, S3 = presigned 307. The desktop `DownloadFileAsync` must follow the 307 (it does — confirm).

**web-chat — server-authoritative already, the reference behavior:**
6. `apps/web-chat/src/lib/api.ts`: `uploadFile` (~:857, multipart `POST /conversations/{id}/files`, field `file`) runs BEFORE send; the returned id is passed as `file_ids` on `sendMessage` (~:323-328). `listFiles`/`downloadFile`/`fileObjectUrl` (~:361-406). Render via `components/chat/MessageAttachments.tsx` (images → authed blob → objectURL; not a data URI). Confirm — this is the shape desktop must match.

**Desktop — the divergent client (where the work is):**
7. Local `files` table (`SranDesktop/src/SranDesktop.Infrastructure/Storage/SqliteConversationStore.cs` ~:46-56): `id, conversation_id, name, mime_type, size_bytes, local_path, server_url, created_at`. **`server_url` is currently never populated.** It's a *local working-file manifest* (`UpsertFileAsync` ~:437 written by `ChatViewModel.RecordAttachmentsInManifestAsync`; `GetFilesAsync` ~:406 read by `AppendFileManifestAsync` to inject a `## Files in this conversation` block so the agent can `read_file` local paths). Confirm.
8. `ChatEdgeApiClient` (`Infrastructure/Api/ChatEdgeApiClient.cs`): `UploadFileAsync` (~:275, `POST .../files`, returns `FileAttachmentDto`), `DownloadFileAsync` (~:295, follows 307). **`ListFilesAsync` does NOT exist** — confirm (it's a needed add). 
9. **The orphan bug:** desktop user attachments ARE uploaded to the server (`ChatViewModel.UploadOneAsync` ~:838 → `IFileService.UploadAsync` → `UploadFileAsync`, storing `dto.Id` in `pending.FileId`), **BUT `pending.FileId` is never consumed** — the agent turn passes `fileIds: null` (`RunChatEdgeTurnAsync` ~:1428), so the server file row is created with `message_id=NULL` and shows under no message on web. Confirm this is the desktop→web gap.
10. **The discard bug:** on opening a server conversation, `ListMessagesAsync` (~:642) maps `MessageDto` (`Models/ChatEdgeModels.cs` ~:26-37) which **has no `files` field** — so the server's per-message `files[]` is dropped at deserialize. No web-uploaded file ever reaches the desktop UI. Confirm this is the web→desktop gap.
11. Render surface: `MessageViewModel.InlineAttachments`/`InlineImages` (`ViewModels/MessageViewModel.cs` ~:18,45) rendered in `Controls/MessageBubble.xaml` (~:54,602). Generated media is downloaded by `AttachGeneratedFilesAsync` (~:1494, **only `image/*`+`video/*` rendered inline**, others skipped). This is the seam to render synced attachments. Confirm.

**Distinguish (critical — don't over-sync):** local working files the agent reads/edits on the user's own disk (manifest rows, `server_url` NULL, injected as `[local paths — use read_file]`) are the user's OWN project files and must **NOT** sync. Only *conversation attachments* (a file the user attached to a chat, or a web upload) sync. The code already separates these (manifest local-path rows vs. a real `UploadFileAsync`/`FileId`). Preserve that separation.

## Design — least-invasive, three concrete pieces
### Part A — Server dedup key (the one net-new server change)
- New migration `027_conversation_files_client_id.sql` (mirror `026`): add nullable `client_id` to `chat.conversation_files` + a **unique partial index** `(conversation_id, client_id) WHERE client_id IS NOT NULL`. Respect the Alembic/schema-drift guard used elsewhere (fail-loud). 
- Upload handler/service: accept an optional `client_id` (multipart field or header) and, when present, **upsert** on `(conversation_id, client_id)` — return the existing row instead of inserting a duplicate (idempotent re-upload). No `client_id` → today's behavior unchanged. This lets desktop re-sync safely.
- Keep caps + MIME allow-list + tenant/user scoping exactly as-is. No auth change.

### Part B — Desktop→web: stop orphaning attachments (attach file_ids on send)
- Consume `pending.FileId`: pass the uploaded file id(s) as `file_ids` on the send path so the server links the file to the message (`RunChatEdgeTurnAsync` ~:1428 currently `fileIds: null`). For the **agent path** (non-chat-edge turn, FIX 9 landed text via `AgentConversationSyncService`), decide the least-invasive way to attach: either (a) also attach `file_ids` when importing the user message, or (b) accept that agent-path attachments upload with `client_id` and get linked — **confirm in R0 which path a user actually attaches files on**, and only wire the path(s) that real attachments flow through. Don't build a path no attachment uses.
- Send the desktop's deterministic file id (the existing `SHA256(conv+localpath)`-style key, `ChatViewModel.cs` ~:1650) as `client_id` on upload → idempotent, dedup-safe.
- Result: a desktop-attached file shows under its message bubble on web-chat.

### Part C — Web→desktop: render server files on the desktop
- Stop discarding `files[]`: add a `files` field to `MessageDto` (`ChatEdgeModels.cs`) — the server already sends it per message — OR add `ListFilesAsync` on `ChatEdgeApiClient` + call it on conversation open. Prefer extending `MessageDto` (single round-trip, matches web). Confirm which is cleaner given the load path.
- On load (`OpenConversationAsync`/`ListMessagesAsync` map ~:644-658): populate a message's `InlineAttachments` from its server `files[]`. Fetch bytes lazily via the existing `DownloadFileAsync` (follows the 307), like `AttachGeneratedFilesAsync` does. Images render inline; non-image attachments show a download chip (don't silently skip them the way generated-file rendering does — a synced PDF/doc should be openable). Cache downloaded bytes/path locally for offline view (write `server_url` on the local `files` row so it's no longer perpetually NULL).
- Deletes: if a file is deleted server-side, it should disappear on the other client on next load (List no longer returns it). Confirm no resurrection.

### Part D — DLP / auth invariants (must hold)
- File bytes are **user content stored in the user's own conversation store** — they are NOT sent to a third-party LLM provider by this feature. Confirm the sync path does not route file bytes through the gateway/provider egress. (The only provider-bound content is still the masked transcript text; attachments are storage artifacts.) State this explicitly in the report.
- Upload/list/download stay behind the same Portal-JWT RequireAuth + tenant/user scoping — no new unauthenticated surface, no cross-tenant read. Confirm the download endpoint checks the caller owns the conversation/file.

## Acceptance / live proof (the bar — fill honestly)
1. **Web→desktop:** upload an image + a PDF on web-chat in a conversation → open that conversation on desktop (rebuilt binary) → the image renders inline, the PDF shows a working download chip. Show it.
2. **Desktop→web:** attach an image on desktop, send → open web-chat as the same user → the image shows under that message bubble (not orphaned). Show it.
3. **Dedup:** re-open / re-sync the desktop conversation → no duplicate file rows on the server (the `client_id` upsert held). Verify the row count.
4. **Deletes:** delete a synced file on one client → gone on the other after reload; stays gone (no resurrection).
5. **Generated media not regressed:** an image-gen turn still renders on both clients (it already did — prove it still does).
6. **DLP/auth intact:** confirm file bytes are not egressed to any provider by sync; a turn with PII in the *text* is still masked (unchanged); the download endpoint rejects a file the caller doesn't own (tenant/user scope). 
7. Tests: desktop `dotnet build` 0/0 + `dotnet test` (MessageDto files mapping, attach-on-send, dedup key where extractable); chat-edge `go test` (client_id upsert idempotency + migration). `DictParityTests` green if strings added. Live-proof table filled; any row you can't run → UNVERIFIED.

## Report
- R0 answers (the map, confirmed with current line numbers) — especially: which turn path real user attachments flow through, and confirm `conversation_files` had no `client_id`.
- What changed, file by file (should center on: migration 027; chat-edge upload `client_id` upsert; `ChatViewModel` attach-`file_ids`-on-send; `MessageDto.files` + desktop render/download + local-cache `server_url`).
- The live-proof table (both directions, dedup, deletes, generated-media-not-regressed, DLP/auth).
- Explicit statements: user-attached files now sync both ways; agent-generated artifacts deferred to FIX 11; file bytes are not egressed to any LLM provider by this feature; DLP/consent/auth unchanged. Commits local only, nothing pushed.
