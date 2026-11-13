# SRAN Chat Edge API

Workspace endpoints and migration 051: [Artifact workspace contract](../../docs/ARTIFACT_WORKSPACE.md). Rebuild the sandbox for `workspace_template.py`. Agent AI repair now creates reviewable proposals; the legacy direct synchronous mode is documented separately.

Visual editor (2026-11-13): rebuild the sandbox for `visual_editor.py`, configure `OFFICE_CONVERTER_URL` for Office renders and optional `OFFICE_VISUAL_MODEL` for AI review. `IMAGE_GEN_MODEL` handles reference-image correction. Backend raw-page egress policy must allow the tenant. See [contracts, limits and isolated test commands](../../docs/VISUAL_EDITOR.md).

Editor/video follow-up (2026-11-13): [shared editor, revisions, PPTX repair and video jobs](../../docs/OFFICE_EDITOR.md). Rebuild the sandbox for `file_editor.py`; existing migration-012 lineage columns are reused. Set `VIDEO_GEN_MODEL` to an enabled verified real model for agent footage; the historical mock default is not production capability.

Media follow-up (2026-11-13): the sandbox now includes FFmpeg and three reusable motion templates; image tools accept conversation-owned reference IDs; MP4 artifacts retain video MIME and oversized files fail instead of truncating. Media config/confirmation proxies support the shared UI. See [Office/media review](../../docs/OFFICE_MEDIA_PARITY.md).

Go service that backs SRAN Web Chat (`apps/web-chat`) and Electron Desktop (`apps/desktop`). Sits between
the browser/desktop and the upstream Go gateway → FastAPI backend, owns the
conversation / message / file / knowledge-base / voice surface, and
mirrors identity from the Portal via JWT.

## What's in here

Reviewed 2026-11-12. These tables summarize the surface; `internal/router/router.go`
and `internal/config/config.go` define the full contracts. The service also owns
Office plans, `/code/run`, projects/memory, account export/deletion, task services
and the feature-gated Agent Market control plane. See the repository's
`docs/API_DESIGN.md` and `docs/OFFICE_WORKFLOWS.md` for file contracts.

| Surface | Routes | Notes |
|---|---|---|
| Auth (legacy) | `/auth/login`, `/auth/refresh`, `/auth/logout` | Returns 410 — Portal JWT is the only identity authority since Phase 1.5. |
| Conversations | `/conversations`, `/conversations/{id}` | CRUD + rename + model swap + KB binding. |
| Messages | `/conversations/{id}/messages` | Streaming SSE, non-stream, regenerate, edit, switch-branch. |
| Files | `/conversations/{id}/files`, `/files/{id}/download`, `/files/{id}` | Upload + extract + list + presigned download. |
| Models | `/models` | Gateway live catalog (5-minute cache); configured/default catalog fallback. |
| Feedback | `/messages/{id}/feedback` | Thumbs up/down for assistant messages. |
| Preferences | `/user/preferences` | Custom instructions, injected into system prompt. |
| Sharing | `/conversations/{id}/share`, `/shared/{token}` | Public read-only links with expiry. |
| Knowledge base (RAG) | `/knowledge-bases/...` | pgvector-backed retrieval, document indexing, citations. |
| Voice | `/voice/transcribe`, `/voice/synthesize` | Whisper STT + OpenAI TTS proxies. |
| Admin | `/admin/analytics/*`, `/admin/conversations`, `/admin/users`, `/admin/knowledge-bases` | Bearer-secret-gated; consumed by admin-ui. |

## Environment variables

The service refuses to start when any `required` variable is missing.
Optional knobs fall back to sensible defaults documented in
`internal/config/config.go`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `PORT` | — | `4000` | HTTP listener port. |
| `DATABASE_URL` | ✓ | — | pgx-compatible Postgres DSN. |
| `REDIS_URL` | ✓ | — | go-redis-style URL with DB number (e.g. `redis://host:6379/1`). |
| `GATEWAY_API_URL` | ✓ | — | Base URL of the upstream Gateway API (no trailing slash). |
| `GATEWAY_API_KEY` | — | empty | Bearer credential for the Gateway. Empty → fall back to service JWT. |
| `USER_JWT_SECRET` | ✓ | — | Required for boot; legacy /auth endpoints are 410 now. |
| `SERVICE_JWT_SECRET` | ✓ | — | Signs the chat-edge → Gateway service JWT. |
| `CSRF_SECRET` | ✓ | — | Required for boot; legacy /auth endpoints are 410 now. |
| `PORTAL_JWT_SECRET` | ✓ | — | MUST match the Portal's `portal_jwt_secret`. |
| `ACCESS_TOKEN_EXPIRY` | — | `1h` | Legacy knob; retained for env parsing. |
| `REFRESH_TOKEN_EXPIRY` | — | `168h` | Same. |
| `SERVICE_JWT_TTL` | — | `60s` | Per-call service JWT TTL. |
| `COOKIE_SECURE` | — | `false` | Toggle `Secure` cookie attribute. **Must be `true` in production.** |
| `MAX_CONTEXT_MESSAGES` | — | `50` | History cap threaded to the Gateway. |
| `AVAILABLE_MODELS` | — | empty | JSON array of model entries; empty → `DefaultModels`. |
| `RATE_LIMIT_MESSAGES` | — | `30` | Per-user message-send rate cap. |
| `RATE_LIMIT_WINDOW` | — | `1m` | Bucket length. |
| `CHAT_EDGE_ADMIN_API_KEY` | — | empty | Required for `/admin/*` routes; empty → routes 503. |
| `FILE_STORAGE_ROOT` | — | `./data/uploads` | Local-backend base path. |
| `MAX_FILE_SIZE_BYTES` | — | 200 MiB | Per-file upload cap. |
| `MAX_CONV_FILE_TOTAL_BYTES` | — | 1 GiB | Per-conversation total cap. |
| `MAX_FILES_PER_MESSAGE` | — | `5` | Attachment count cap per send. |
| `EXTRACT_PYTHON_BINARY` | — | `python3` | Used by `scripts/extract_text.py`. |
| `STORAGE_BACKEND` | — | `local` | `local` or `s3`. |
| `S3_BUCKET` | for s3 | — | Bucket name when `STORAGE_BACKEND=s3`. |
| `S3_REGION` | — | `us-east-1` | Required by the AWS SDK; MinIO ignores it. |
| `S3_ENDPOINT` | — | empty | Set to e.g. `http://minio:9000` for MinIO / R2 / Wasabi. |
| `S3_ACCESS_KEY` | — | empty | Static creds; empty → default credential chain. |
| `S3_SECRET_KEY` | — | empty | Same. |
| `S3_FORCE_PATH_STYLE` | — | `false` | Auto-on when `S3_ENDPOINT` is set. |
| `PRESIGNED_URL_EXPIRY` | — | `15m` | TTL on presigned download URLs. |
| `OPENAI_API_KEY` | — | empty | Legacy compatibility configuration; current voice calls route through the gateway and KB uses `KB_MODELS_URL`. Verify wiring in `cmd/server/main.go`. |
| `OPENAI_BASE_URL` | — | `https://api.openai.com/v1` | Azure / proxy override. |
| `OPENAI_WHISPER_MODEL` | — | `whisper-1` | Speech-to-text model. |
| `OPENAI_TTS_MODEL` | — | `tts-1` | Text-to-speech model. |
| `KB_MODELS_URL` | — | `http://kb-models:8090` | The knowledge base's models (kb-models, on this machine; internal hosts only). |
| `KB_*` | — | see `docs/ops/knowledge-base.md` | Indexing workers, chunk size, hybrid retrieval weights and floors, reranker, context cap. |
| `CORS_ALLOW_ORIGINS` | — | `http://localhost:3002` | Comma-separated. |
| `CHAT_EDGE_DEV` | — | unset | Switches logging to text format. |

A starter `.env.example` lives alongside this README.

## Running

From the repository root, use the self-contained webchat stack. The Make target
builds the sandbox image required by document processing before starting it:

```bash
make webchat-up
```

See the root README and `docs/ops/tls.md` for current ports. Browser-facing
chat-edge and Web Chat use Caddy HTTPS on 4000 and 3002; their plain HTTP debug
listeners are loopback-only on 24000 and 23002. Postgres and Redis are service
dependencies, not browser endpoints. Do not merge this Compose file with the
base `docker-compose.yml`.

To flip storage to MinIO without rebuilding:

```bash
STORAGE_BACKEND=s3 \
  S3_ENDPOINT=http://minio:9000 \
  S3_BUCKET=sran-chat-files \
  S3_ACCESS_KEY=minioadmin \
  S3_SECRET_KEY=minioadmin123 \
  docker compose -p ai-gateway -f docker-compose.webchat.yml up -d minio minio-init chat-edge-api
```

Standalone Go run (assuming Postgres + Redis available):

```bash
cd services/chat-edge-api
go run ./cmd/server
```

## Testing

Office regressions run from the repository root with
`python -m unittest discover -s services/chat-edge-api/scripts/tests -v` and
inside the sandbox through `scripts/agent-market/python-lane.sh`. They reopen
real files. `OFFICE_CONVERTER_URL` optionally enables shared LibreOffice
recalculation and render checks for generic chat and plan cards. Full visual
Office fidelity is not guaranteed by those checks. See `docs/OFFICE_TEMPLATES.md`
for the catalog, internal Compose overlay and live converter test setup.

```bash
go vet ./...
go test ./...
```

Tests include:

- `internal/storage` — local backend round-trip, NotFound, path traversal, JoinKey.
- `internal/service` — RAG, message/tool execution, persisted artifacts and Office plan contracts.
- `internal/handler` — API ownership, conversation-scoped code inputs and plan validation.

End-to-end flows (conversation, file upload, KB, branching) require a running
stack. Historical QA reports do not establish that a new checkout passes them;
the current Office reference distinguishes executed tests from pending live QA.

## Migrations

SQL files live in `migrations/`. The docker-compose stack mounts them
into Postgres's `docker-entrypoint-initdb.d`, so a fresh volume runs
them automatically. An existing volume does not re-run those hooks: inspect
and apply outstanding SQL migrations in order using the deployment's database
procedure and backups. Rebuilding a container does not update that schema.
`down -v` deletes data and is only a deliberate disposable-development reset,
not a required migration step.

The source migration head is `050_deleted_data_purge.sql`; this does not prove
the running database has applied it. Early RAG migrations include:

| File | Adds |
|---|---|
| `010_enable_pgvector.sql` | `vector` extension. |
| `011_create_knowledge_base.sql` | `chat.knowledge_bases`, `chat.knowledge_documents`, `chat.knowledge_chunks`, plus `conversations.knowledge_base_id`. |

## Layout

```
cmd/server/             entry point + dependency wiring
internal/auth/          Portal JWT parsing + service JWT issuance
internal/config/        env loader (caarlos0/env)
internal/handler/       HTTP handlers — one file per resource
internal/middleware/    logging / CORS / auth / admin auth / rate limit
internal/model/         ORM-style row structs (no business logic)
internal/repository/    SQL CRUD — every public method is tenant-scoped
internal/router/        Chi route registration
internal/service/       business logic — orchestrates repo + gateway + storage
internal/storage/       blob backend (local + S3) — Phase 3 Session 6
migrations/             ordered SQL, runs on first DB volume mount
scripts/                extract_text.py (PDF/DOCX/XLSX), migrate_to_s3.sh
```
