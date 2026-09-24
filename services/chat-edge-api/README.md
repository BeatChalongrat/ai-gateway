# SRAN Chat Edge API

Go service that backs the SRAN Web Chat (`apps/web-chat`). Sits between
the browser and the upstream Gateway API (`backend/`), owns the
conversation / message / file / knowledge-base / voice surface, and
mirrors identity from the Portal via JWT.

## What's in here

| Surface | Routes | Notes |
|---|---|---|
| Auth (legacy) | `/auth/login`, `/auth/refresh`, `/auth/logout` | Returns 410 — Portal JWT is the only identity authority since Phase 1.5. |
| Conversations | `/conversations`, `/conversations/{id}` | CRUD + rename + model swap + KB binding. |
| Messages | `/conversations/{id}/messages` | Streaming SSE, non-stream, regenerate, edit, switch-branch. |
| Files | `/conversations/{id}/files`, `/files/{id}/download`, `/files/{id}` | Upload + extract + list + presigned download. |
| Models | `/models` | Static allow-list from `AVAILABLE_MODELS` env or the default set. |
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
| `MAX_FILE_SIZE_BYTES` | — | 25 MiB | Per-file upload cap. |
| `MAX_CONV_FILE_TOTAL_BYTES` | — | 100 MiB | Per-conversation total cap. |
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
| `OPENAI_API_KEY` | — | empty | Required for embeddings, Whisper, TTS. Without it those surfaces 503. |
| `OPENAI_BASE_URL` | — | `https://api.openai.com/v1` | Azure / proxy override. |
| `OPENAI_WHISPER_MODEL` | — | `whisper-1` | Speech-to-text model. |
| `OPENAI_TTS_MODEL` | — | `tts-1` | Text-to-speech model. |
| `KB_MODELS_URL` | — | `http://kb-models:8090` | The knowledge base's models (kb-models, on this machine; internal hosts only). |
| `KB_*` | — | see `docs/ops/knowledge-base.md` | Indexing workers, chunk size, hybrid retrieval weights and floors, reranker, context cap. |
| `CORS_ALLOW_ORIGINS` | — | `http://localhost:3002` | Comma-separated. |
| `CHAT_EDGE_DEV` | — | unset | Switches logging to text format. |

A starter `.env.example` lives alongside this README.

## Running

Local dev — uses the bundled docker-compose stack (`docker-compose.webchat.yml`):

```bash
docker compose -f docker-compose.webchat.yml up -d
```

That brings up Postgres (5433), Redis (6380), chat-edge-api (4000),
the Web Chat front-end (3002), and (optional, opt-in) MinIO (9000 +
9001 console).

To flip storage to MinIO without rebuilding:

```bash
STORAGE_BACKEND=s3 \
  S3_ENDPOINT=http://minio:9000 \
  S3_BUCKET=sran-chat-files \
  S3_ACCESS_KEY=minioadmin \
  S3_SECRET_KEY=minioadmin123 \
  docker compose -f docker-compose.webchat.yml up -d minio minio-init chat-edge-api
```

Standalone Go run (assuming Postgres + Redis available):

```bash
cd services/chat-edge-api
go run ./cmd/server
```

## Testing

```bash
go vet ./...
go test ./...
```

Unit tests cover:

- `internal/storage` — local backend round-trip, NotFound, path traversal, JoinKey.
- `internal/service` — text chunker edge cases + RAG context formatter.

End-to-end flows (conversation, file upload, KB, branching) are
exercised by the Web Chat manual QA — they require a running stack
and are documented in the session reports.

## Migrations

SQL files live in `migrations/`. The docker-compose stack mounts them
into Postgres's `docker-entrypoint-initdb.d`, so a fresh volume runs
them automatically. Rebuilding the schema after a code change requires
`docker compose -f docker-compose.webchat.yml down -v` to wipe the
volume.

The Phase 3 additions:

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
