# Agent and workflow API readiness (FIX133 F3)

What an agent or workflow client (a script, a scheduler, another service)
can call today, what authenticates it, and what is missing before we hand
out keys for unattended use. Report only; no code changes. Paths are
relative to the repository root and were checked on this branch.

## 1. The endpoints

Two surfaces exist. The **gateway** (`/v1/*`, Go proxy on :8080 in front of
the Python backend) is the one built for API clients. **chat-edge**
(`/api/v1/*`, :4000) is the chat product's API and is authenticated as a
person, not a key.

### Gateway `/v1` (mounted in `backend/app/main.py` l.975-989)

All rows except `/v1/models` depend on `get_resolved_api_key`
(`backend/app/api/deps.py` l.258-366). Limits: 60/min per key, 3000/min per
project, 10000/min per tenant, sliding window in Redis (`rl:key:` /
`rl:proj:` / `rl:tenant:`, `backend/app/services/rate_limit_service.py`
l.265-285; the same three scopes in `rate-limiter/src/sliding_window.rs`
l.37-56). Body limit 1 MB, `/v1/chat/completions` 20 MB (`main.py` l.921-932);
the Go gateway caps everything at `GATEWAY_MAX_BODY_BYTES` = 25 MB
(`docker-compose.yml` l.335).

| Endpoint | Auth | Extra limits | Idempotent? | Notes |
|---|---|---|---|---|
| `POST /v1/chat/completions` (`public_chat.py` l.330) | key / admin JWT / service JWT | 20 MB body; per-worker `chat_inflight_limit` 64 (`config.py` l.324) | **No.** Excluded from the idempotency middleware (`idempotency.py` l.50-54 lists it as SSE) and the route does no dedupe: a client retry after a dropped connection is a second turn, charged twice | SSE or JSON; the FIX133 turn queue adds a `queued` frame and `503 queue_full` |
| `POST /v1/feedback` (`public_chat.py` l.3729) | key | | no | writes `request_logs.feedback_*` |
| `GET /v1/models` (`public_models.py` l.73-83) | **none** (only a DB session) | | read | catalogue is public to anyone who can reach :8080 |
| `POST /v1/embeddings` (`public_embeddings.py` l.40) | key | | no | |
| `POST /v1/images/generations` (`public_images.py` l.156) | key | `image_rate_limit_user_per_hour` 10, `image_rate_limit_tenant_per_day` 100 (`config.py` l.116-117; `rl:img:*`) | no | |
| `POST /v1/videos/generations` (`public_videos.py` l.144) | key | `media_rate_limit_video_per_hour` 5, `media_rate_limit_jobs_per_min` 60 (`config.py` l.123-125) | with `X-Idempotency-Key` | see the idempotency note below |
| `POST /v1/audio/generations` (`public_audio.py` l.36) | key | `media_rate_limit_audio_per_hour` 30 | with `X-Idempotency-Key` | |
| `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel`, `GET /v1/jobs/{id}/output` (`public_videos.py` l.215-338) | key | | read / cancel is safe to repeat | |
| `GET /v1/video/config`, `GET /v1/search/config` (`public_videos.py` l.116, `public_search.py` l.123) | key | | read | |
| `POST /v1/search` (`public_search.py` l.50) | key | | no | |
| `POST /v1/guardrails/check`, `GET /v1/desktop/version` (`public_guardrails.py` l.79, l.104) | key | | read | |
| `POST /v1/voice/transcribe`, `POST /v1/voice/synthesize` (`public_voice.py` l.38, l.82) | key | 1 MB body | no | |

Not there: `/v1/files` and `/v1/responses`, although `docs/API_DESIGN.md`
l.5 lists `POST /v1/responses`. An agent that needs file input must go
through chat-edge.

How `get_resolved_api_key` decides (`deps.py`):
- `Authorization: Bearer gw_...` -> SHA-256 of the raw key looked up in
  `api_keys.key_hash`; must be `status = active` and not past `expires_at`
  (`backend/app/core/security.py` l.24, `backend/app/services/api_key_service.py`
  l.78-90). `role` is copied onto the result (l.356) but **no `/v1` route
  checks it**; `require_admin` (l.370-390) guards admin routes only.
- `Bearer eyJ...`: first tried as the chat-edge service JWT (HS256,
  `iss=chat-edge-api`, `aud=gateway-api`, 60 s by default,
  `services/chat-edge-api/internal/auth/service_jwt.go` l.13-14; verified at
  `deps.py` l.184-256). It **requires** `X-User-ID` and `X-Tenant-ID` and
  fails closed without them. Otherwise it is an admin JWT.
- `X-User-ID` on a `gw_` key is honoured only with a matching
  `X-Internal-Service-Secret` (l.134-181); without it the header is ignored.

### chat-edge `/api/v1` (`services/chat-edge-api/internal/router/router.go`)

| Group | Auth | Limits | Idempotent? | Notes |
|---|---|---|---|---|
| `/auth/register`, `/login`, `/refresh`, `/logout` (l.178-181) | none | | | chat-edge's own tokens: `ACCESS_TOKEN_EXPIRY` 1 h, `REFRESH_TOKEN_EXPIRY` 168 h (`internal/config/config.go` l.139-140) |
| everything under l.186-491: conversations, messages, projects, artifacts, files, memories, knowledge-bases, skills, cloud-tasks, code/run, voice, search, share, account exports, usage | `RequireAuth`: `portal_access_token` cookie or `Authorization: Bearer <portal JWT>` (`internal/middleware/auth.go` l.95-96, l.202-208); portal access tokens live 60 min (`backend/app/core/config.py` l.131) and are renewed through `POST /portal/auth/refresh` (`backend/app/api/routes/portal_auth.py` l.368) | `RATE_LIMIT_MESSAGES` 30 per `RATE_LIMIT_WINDOW` 1 m per user (`config.go` l.247-248) on send, answer, regenerate, video/audio submit and `code/run` (router l.256-279, l.439) | **send is**: a client `X-Request-Id` is the idempotency key; same key + same content replays, same key + different content is `409 IDEMPOTENCY_KEY_REUSED` (`internal/handler/message.go` l.526-530, l.602-604, l.672-687) | no long-lived credential: a workflow must hold a refresh token and rotate it |
| `/admin/*` (l.502-529) | exact-match `Bearer CHAT_EDGE_ADMIN_API_KEY` (`internal/middleware/admin_auth.go` l.29-39) | | | one shared secret, no per-caller identity |
| `/internal/auth/revoke`, `/internal/accounts/{id}/...` (l.159-163) | internal service auth | | | portal -> chat-edge only |
| `GET /shared/{token}` (l.496) | none (token in path) | | read | |

## 2. What is missing

1. **No scopes on API keys.** `api_keys` has `key_id, key_prefix, key_hash,
   tenant_id, project_id, name, role, status, expires_at, created_at,
   last_used_at, portal_user_id` (`backend/app/models/api_key.py` l.12-37) and
   nothing that says "this key may only embed" or "may only search". Scopes
   exist only on `connector_tokens.scopes` (`backend/app/models/connector_token.py`
   l.48). `role` is stored but never consulted on `/v1`. One leaked
   embeddings key can run chat completions and image generation.
2. **No agent credential for chat-edge.** Its API is authenticated by a
   person's 60-minute portal token or chat-edge's own 1-hour token; there is
   no key that can act as a user on a schedule without a refresh loop, and
   the admin key is one shared secret for all admin callers.
3. **Audit gaps.** `admin_audit_log` is hash-chained
   (`backend/app/services/audit_integrity.py` l.19-22) and admin key create /
   revoke write to it (`backend/app/api/routes/admin.py` l.2282, l.2318), but
   the portal's `POST /{project_id}/keys`, `/keys/{key_id}/rotate` and
   `DELETE /keys/{key_id}` (`backend/app/api/routes/portal_projects.py` l.364,
   l.391, l.438) write no audit row at all. Most keys are created there.
4. **No per-key usage.** `request_logs` carries `request_id, tenant_id,
   project_id, user_id` (`backend/app/models/request_log.py` l.13-24; `user_id`
   is the portal owner or the trusted `X-User-ID`) but no `api_key_id` and no
   turn id, so "which key spent this" and "which chat-edge turn was this
   gateway call" cannot be answered from the table.
5. **Chat completions are not idempotent.** A retried `POST /v1/chat/completions`
   runs and bills twice. The media idempotency middleware
   (`backend/app/middleware/idempotency.py`) is triggered by an
   `X-Idempotency-Key` header on `/v1/videos/` and `/v1/audio/` only
   (l.40-42, l.95), and it namespaces the cache by the **client-supplied**
   `X-Tenant-ID` header (l.106-114), not by the authenticated key's tenant:
   callers that omit the header share one namespace, and a caller who sends
   another tenant's id with an identical body would be served that tenant's
   cached reply. It should key on the resolved tenant and key id.
6. **`/v1/models` is unauthenticated**, which is fine for a catalogue but
   means it cannot be scoped or attributed either.

## 3. Recommended minimal design

Smallest set of changes that makes an unattended client safe to issue keys
to; each item is independent and can ship alone.

1. **`api_keys.scopes`** (text, comma-separated like `connector_tokens.scopes`;
   migration backfills existing rows with `*`). A `require_scope("chat")`
   dependency next to `require_admin` in `deps.py`, added per router:
   `chat`, `embeddings`, `images`, `media`, `search`, `voice`, `guardrails`.
   `ResolvedApiKey` gains `scopes`; the portal key form gains checkboxes.
2. **Long-lived agent tokens without a new table**: an `api_keys` row with
   `role = agent`, `portal_user_id` set, explicit `scopes` and a required
   `expires_at`, issued from `portal_projects.py`. For chat-edge, accept such a
   key on `/api/v1` by having chat-edge call the gateway to resolve it (the
   reverse of today's service JWT) and act as `portal_user_id`; this gives a
   scheduler a credential that is revocable in one place.
3. **Audit rows for portal key events**: call `AdminAuditService().record`
   from the three `portal_projects.py` endpoints with the same action names
   the admin routes use, so the hash chain covers every key lifecycle event.
4. **`request_logs.api_key_id`** (indexed) set from `ResolvedApiKey.key_id`
   in the chat path, plus a `client_request_id` column that stores the
   caller's `X-Request-Id`; chat-edge already generates one per send
   (`message.go` l.509-513), so a chat-edge turn and its gateway call become
   joinable. Per-key usage and per-key rate-limit reporting fall out of this.
5. **`Idempotency-Key` on `/v1/chat/completions`**, handled in the route,
   not in the SSE-buffering middleware: Redis `idem:chat:<key_id>:<sha256(body)>`
   for 24 h holding `in_flight | done(request_id)`. A repeat while in flight
   returns `409`; a repeat after completion returns the stored non-streamed
   result (or replays the final message for a stream) and is not billed.
   Namespacing by `key_id` also fixes the media middleware's tenant-header
   problem if the same helper is reused there.
