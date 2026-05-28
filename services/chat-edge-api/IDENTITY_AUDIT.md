# Identity Binding Audit — 2026-05-28

Pre-flight check before building the cross-conversation memory system.
Goal: confirm every user-data table carries `user_id`, every query
filters by the authenticated user's ID, and no endpoint leaks data
across users in the same tenant.

This document captures the static + behavioural audit findings, the
fixes applied in this commit, and the gap list still ahead of us.

---

## Auth middleware

| Field            | Value |
|------------------|-------|
| Token type       | Portal JWT (HS256, `services/portal/auth_service.py` mints) |
| Verify path      | `internal/auth/portal_jwt.go::ParsePortalToken` — signature, `type=="portal"`, `token_use=="access"`, expiry |
| Required claims  | `sub` (UUID), `email`, `tenant_id`, `tenant_role` |
| Extracted into   | `middleware.Caller{ UserID, TenantID, TenantUUID, Role, Email }` |
| Context key      | unexported `ctxKey` const; opaque to other packages |
| Reader helpers   | `middleware.CallerFrom(ctx) (Caller, bool)` — pre-existing |
| New helpers      | `middleware.MustCaller(ctx) Caller` + `middleware.UserIDFrom(ctx) uuid.UUID` |
| Shadow user row  | `ProvisionService.EnsureUserExists` lazily upserts identity.users so chat FKs hold |
| Fallback transport | `Authorization: Bearer …` OR `portal_access_token` cookie (Portal mirrors the token) |

Admin endpoints (`/api/v1/admin/…`) use `RequireAdmin` middleware:
shared bearer secret (`CHAT_EDGE_ADMIN_API_KEY`), constant-time
compare. Out of scope for the memory system (memory is end-user
surface, not admin).

---

## Table ownership matrix

| Table                      | `user_id`?     | `tenant_id`? | Indexed?                          | Notes |
|----------------------------|---------------|--------------|------------------------------------|-------|
| `identity.tenants`         | n/a            | self (PK)    | PK                                 | |
| `identity.users`           | self (PK)      | yes           | tenant + email                     | |
| `identity.refresh_tokens`  | yes            | n/a           | user + family                      | |
| `identity.invitation_tokens` | `created_by` | yes           | —                                  | |
| `chat.conversations`       | yes            | yes           | composite `(tenant_id, user_id)`   | |
| `chat.messages_masked`     | yes            | yes           | conv+created, tenant, parent       | |
| `chat.feedback`            | yes            | yes           | message, unique `(message_id, user_id)` | |
| `chat.conversation_files`  | **added in 013** | yes        | composite `(tenant_id, user_id)`   | denormalised from conversation |
| `chat.user_preferences`    | yes            | yes           | composite `(tenant_id, user_id)`   | |
| `chat.shared_conversations`| no             | yes           | token + conv                       | ownership via conv FK |
| `chat.knowledge_bases`     | no             | yes           | tenant                             | **intentional**: KBs are tenant-shared libraries |
| `chat.knowledge_documents` | no             | yes           | kb + tenant                        | scoped through KB |
| `chat.knowledge_chunks`    | no             | yes           | kb + doc + ivfflat embedding       | scoped through doc |

**Knowledge bases** are deliberately tenant-scoped (a tenant's
shared library, any tenant member can read). The memory system is
per-USER and lives in its own table; KB tenancy doesn't need to
change.

---

## Endpoint protection matrix

Every route below requires Portal JWT via `RequireAuth` middleware
unless flagged "PUBLIC". Auth-required routes are listed with the
ownership check that runs after the middleware extracts user_id.

### Conversations
| Endpoint                                   | Auth | Ownership check (post-fix) |
|--------------------------------------------|------|----------------------------|
| `GET    /conversations`                    | yes  | `ListByUser(tenant, user)` — repo-level user filter |
| `POST   /conversations`                    | yes  | `Create` uses `caller.UserID`/`TenantUUID` from JWT |
| `GET    /conversations/{id}`               | yes  | handler-side `c.UserID != caller.UserID` check |
| `PATCH  /conversations/{id}`               | yes  | **FIXED** — service now calls `AssertOwnership` first |
| `DELETE /conversations/{id}`               | yes  | **FIXED** — service now calls `AssertOwnership` first |
| `GET    /conversations/{id}/export`        | yes  | handler-side user check |
| `POST   /conversations/{id}/generate-title`| yes  | `titles.Generate` user-scoped |
| `POST   /conversations/{id}/share`         | yes  | `ShareService` user-scoped |
| `GET    /conversations/{id}/share`         | yes  | `ShareService` user-scoped |
| `DELETE /conversations/{id}/share`         | yes  | `ShareService` user-scoped |
| `GET    /shared/{token}`                   | **PUBLIC** | intentional — token IS the cap |

### Messages
| Endpoint                                                   | Auth | Ownership check |
|------------------------------------------------------------|------|------------------|
| `GET  /conversations/{id}/messages`                        | yes  | `ListMessages` checks `conv.UserID != userID` |
| `POST /conversations/{id}/messages`                        | yes  | `prepareSend` checks `conv.UserID != in.UserID` |
| `POST /conversations/{id}/messages/{msgID}/regenerate`     | yes  | `Regenerate` checks `conv.UserID != in.UserID` |
| `POST /conversations/{id}/messages/{msgID}/edit`           | yes  | `EditMessage` checks `conv.UserID != in.UserID` |
| `POST /conversations/{id}/messages/{msgID}/switch-branch`  | yes  | `SwitchBranch` checks `conv.UserID != userID` |
| `POST /conversations/{id}/cancel`                          | yes  | `AssertConversationAccess` checks `conv.UserID != userID` |

### Files
| Endpoint                          | Auth | Ownership check (post-fix) |
|-----------------------------------|------|----------------------------|
| `POST   /conversations/{id}/files`| yes  | `Upload` checks `conv.UserID != in.UserID` |
| `GET    /conversations/{id}/files`| yes  | `List` checks `conv.UserID != userID` |
| `GET    /files/{fileID}/download` | yes  | **FIXED** — `OpenForDownload` now takes userID, verifies `f.UserID == userID` |
| `DELETE /files/{fileID}`          | yes  | **FIXED** — `Delete` now takes userID, verifies `f.UserID == userID` |

### Workspace (Phase 4)
| Endpoint                                                       | Auth | Ownership check (post-fix) |
|----------------------------------------------------------------|------|----------------------------|
| `POST /files/{fileID}/inspect`                                 | yes  | **FIXED** — handler now checks `f.UserID != caller.UserID` |
| `POST /conversations/{convID}/excel/execute`                   | yes  | **FIXED** — `ExecutePlan` now checks `source.UserID != in.UserID` |
| `POST /conversations/{convID}/document/execute`                | yes  | **FIXED** — source path checks `s2.UserID != in.UserID`; from-scratch path checks `conv.UserID != in.UserID` |
| `GET  /conversations/{convID}/files/{fileID}/versions`         | yes  | **FIXED** — handler filters by `versions[i].UserID == caller.UserID` |
| `GET  /conversations/{convID}/generated-files`                 | yes  | **FIXED** — handler now calls `WorkspaceService.AssertConvOwnership` first |
| `POST /conversations/{convID}/files/{fileID}/rollback`         | yes  | **FIXED** — `Rollback` now calls `assertConvOwnership` + verifies target.user_id |
| `GET  /conversations/{convID}/file-compare?v1=&v2=`            | yes  | **FIXED** — `Compare` now calls `assertConvOwnership` + verifies both files' user_id |

### Feedback
| Endpoint                                  | Auth | Ownership check (post-fix) |
|-------------------------------------------|------|----------------------------|
| `POST /messages/{messageID}/feedback`     | yes  | **FIXED** — walks message → conversation → checks `conv.UserID != in.UserID` |

### Preferences
| Endpoint                       | Auth | Ownership check |
|--------------------------------|------|------------------|
| `GET /user/preferences`        | yes  | `prefs.Get(tenant, user)` — repo-level filter |
| `PUT /user/preferences`        | yes  | `prefs.Upsert(tenant, user)` — repo-level filter |

### Knowledge bases (tenant-scoped, intentional)
| Endpoint                                                    | Auth | Ownership check |
|-------------------------------------------------------------|------|------------------|
| `…/knowledge-bases…`                                        | yes  | tenant-scoped only — KBs are a tenant-shared library by design |

### Voice
| Endpoint                       | Auth | Notes |
|--------------------------------|------|-------|
| `POST /voice/transcribe`       | yes  | proxy only, no DB read; key not exposed |
| `POST /voice/synthesize`       | yes  | proxy only |

### Deprecated auth (all 410 Gone)
| Endpoint                       | Auth | Notes |
|--------------------------------|------|-------|
| `POST /auth/{register,login,refresh,logout}` | PUBLIC | 410 Gone with pointer to Portal |

---

## Issues found & fixed in this commit

### Cross-user leaks (same tenant, different user)

1. **Conversation rename / model-change / KB-rebind / delete** — `ConversationService.Rename`, `SetModel`, `SetKnowledgeBase`, `Delete` only took tenant_id; any same-tenant user could mutate another user's conversation. **Fix**: added `AssertOwnership(tenantID, userID, convID)` and made it the first thing each mutator calls.

2. **File download / delete** — `FileService.OpenForDownload` and `Delete` took only tenant_id. **Fix**: both now take `userID`; verify via the `user_id` column on `chat.conversation_files` (added in migration 013).

3. **Workspace list / rollback / compare** — `WorkspaceHandler.ListGenerated` hit the repo directly; `WorkspaceService.Rollback` / `Compare` only verified conversation scope. Any same-tenant user could list, roll back, or diff another user's artifacts. **Fix**: `WorkspaceService` now depends on `ConversationRepo` and runs `assertConvOwnership` first; `ListGenerated` calls a new exported `AssertConvOwnership` before listing.

4. **Excel inspect / execute / versions** — `ExcelHandler.Inspect` and `.Versions` hit the file repo directly without a user check. **Fix**: handlers now check `f.UserID != caller.UserID`; `ExcelService.ExecutePlan` now checks `source.UserID != in.UserID`.

5. **Document inspect / execute** — same pattern as Excel. **Fix**: handler checks `f.UserID`; service checks `s2.UserID` on the with-source path; new from-scratch branch verifies `conv.UserID` via the conversations repo (newly threaded into `DocService`).

6. **Feedback** — `FeedbackService.Create` looked up the message tenant-scoped but didn't verify the caller owned the parent conversation. Means a same-tenant user could probe whether a message ID existed by toggling 404 vs 422. **Fix**: walk message → conversation → check `conv.UserID != in.UserID`.

### Schema

7. **`chat.conversation_files` lacked `user_id`** — ownership had to be inferred through the conversation chain on every check, and the upcoming memory system's "this user's artifacts" queries would need a join. **Fix**: migration `013_add_file_user_id.sql` adds `user_id` (nullable → backfill from conversations → NOT NULL), indexes `(tenant_id, user_id)`. Every Create path (FileService.Upload, ExcelService.ExecutePlan, DocService.ExecutePlan, WorkspaceService.Rollback) now sets `user_id` on the new row.

### Helpers

8. **No `MustUserID(ctx)` convenience** — every handler does `caller, _ := middleware.CallerFrom(r.Context())`. **Fix**: added `middleware.MustCaller(ctx)` and `middleware.UserIDFrom(ctx)`. Existing call sites unchanged; memory-system code can use the new form.

---

## Issues found & NOT fixed (deferred / out of scope)

- **`ConversationService.Get` ownership lives in the handler, not the service** — handler does `c.UserID != caller.UserID` after the tenant-scoped Get. Works correctly today; asymmetric with the other methods. Deferred to a follow-up cleanup pass — changing it risks regressions in the export + title paths that also call Get.

- **`ExcelService.ValidateWorkbook` is tenant-scoped only** — exposed as a method but no HTTP route currently invokes it. Will need a user_id parameter when a route does. Tracked but not changed; touching it without a caller is dead-weight churn.

- **Knowledge-base routes are tenant-scoped** — intentional per the schema (KBs are tenant-shared libraries). Memory system is per-user and lives in a separate table; this stays.

- **`Compare` belt-and-braces user_id check** — added a redundant `v1.UserID != in.UserID` check after `assertConvOwnership` to fail closed if migration 013's backfill ever missed a row. Same pattern in `Rollback`. Kept as defence in depth; not strictly needed.

---

## Ready for the memory system?

- [x] Every conversation has `user_id` (and `tenant_id`)
- [x] Every query that returns user data filters by the authenticated user's id from the JWT
- [x] No endpoint leaks cross-user data within a tenant
- [x] JWT user_id is used everywhere (never request body / URL path)
- [x] Auth helpers (`CallerFrom` / `MustCaller` / `UserIDFrom`) are consistent
- [x] File ownership verified through both the conversation chain AND the denormalised `user_id` column on `chat.conversation_files`
- [x] `go vet ./...` passes
- [x] `go build ./...` passes
- [x] `go test ./...` passes (service package only — others have no tests)

S1 (the `user_memories` table) can land safely on top of this foundation:

- Primary key on `(user_id, ...)` won't have to worry about cross-user pollution from any of the file / workspace / feedback paths.
- The new `MustCaller(ctx)` helper gives memory handlers a one-liner for the common case.
- File-backed memories (e.g. "remember this PDF the user uploaded") can query `chat.conversation_files WHERE user_id = $1` directly thanks to migration 013's denormalised column + index.
