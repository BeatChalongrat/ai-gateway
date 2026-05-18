# AI Gateway

Multi-tenant gateway in front of 11 LLM providers, with PDPA-aware DLP, policy/quota/budget enforcement, encrypted audit logging, and dedicated admin + customer portals.

The backend is FastAPI on Postgres + Redis. The spaCy/Presidio NER model runs in its own container so backend workers stay lean. The Next.js admin app drives all governance; a separate Next.js customer portal owns the self-service surface (signup, billing, projects, team).

---

## 1. Architecture

```
                         ┌────────────────────────────────────────────────────┐
                         │            HTTP clients (SDKs, curl)               │
                         └────────────────┬───────────────────────────────────┘
                                          │  POST /v1/chat/completions  (API key)
                                          ▼
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │                       Backend  ·  FastAPI  ·  :8080                            │
 │                                                                                │
 │  middleware ─►  rate-limit ─►  api-key resolve ─►  policy resolve ─►          │
 │                                                                                │
 │  ┌──────────────────────────────────────────────────────────────────────────┐ │
 │  │  GatewayOrchestrator                                                     │ │
 │  │   1. cache lookup (Redis)                                                │ │
 │  │   2. DLP inbound:  30 regex detectors  +  NER ──► ner-service :8001 ────┼─┼─► spaCy / Presidio
 │  │   3. policy evaluator (block / mask / restore per finding)               │ │
 │  │   4. mask + token-map  (deterministic w/ DLP_HMAC_KEY when conv_id set)  │ │
 │  │   5. moderation + guardrail                                              │ │
 │  │   6. routing engine  (keyword → semantic → cost-tier → fallback chain)   │ │
 │  │   7. provider dispatcher (11 adapters + fallback)  ──────────────────────┼─┼─► OpenAI / Anthropic /
 │  │   8. DLP outbound + restore                                              │ │   Google / Groq / xAI /
 │  │   9. encrypt input_raw_encrypted (AES-256-GCM)                           │ │   DeepSeek / Qwen / Kimi /
 │  │  10. INSERT request_logs (monthly partitioned)                           │ │   Z.AI / Minimax /
 │  │  11. usage + budget + alert evaluator                                    │ │   OpenRouter
 │  └──────────────────────────────────────────────────────────────────────────┘ │
 │                                                                                │
 └──────────────┬──────────────────────────┬───────────────────────────┬─────────┘
                │                          │                           │
                ▼                          ▼                           ▼
        Postgres :5432             Redis :6379                  Mock provider :9090
        (24 tables, partitioned    (rate-limit ZSETs,           (drop-in for tests
         request_logs, alembic     response cache, NER          when PROVIDER_BASE_
         to 041)                   degrade flag)                URL_OVERRIDES set)
                ▲                          ▲
                │                          │
   ┌────────────┴────────────┐  ┌──────────┴─────────────┐
   │  Admin UI  :3000        │  │  Portal     :3001      │
   │  (Next.js, 18 pages)    │  │  (Next.js, 19 pages)   │
   │  governance, policies,  │  │  signup, billing,      │
   │  audit, alerts, models  │  │  projects, team        │
   └─────────────────────────┘  └────────────────────────┘
```

---

## 2. Services

| Service | Port | Image | Role |
|---|---|---|---|
| `postgres` | 5432 | `postgres:16` | Application DB. 24 tables, monthly-partitioned `request_logs`. |
| `redis` | 6379 | `redis:7` | Rate-limit ZSETs, response cache, NER-degrade marker. |
| `mock-provider` | 9090 | built from `./mock` | Stand-in for OpenAI/Anthropic during local dev and tests; activate by setting `PROVIDER_BASE_URL_OVERRIDES`. |
| `ner-service` | 8001 | built from `./backend/ner_service` | spaCy + Presidio in a dedicated container. CPU/mem-bounded (`2 CPU / 2 GB`). Endpoints: `POST /analyze`, `GET /health`. |
| `backend` | 8080 | built from `./backend` | FastAPI gateway. 4 gunicorn workers × uvicorn. Hot-reload in dev via `docker-compose.override.yml`. |
| `admin-ui` | 3000 | built from `./admin-ui` | Next.js admin console. Cookie-auth, CSRF, rate-limited login. |
| `portal` | 3001 | built from `./portal` | Next.js customer portal. NextAuth for OAuth, JWT for API access. |
| `log-worker` | 50052 | built from `./log-worker` | Go async batch logger. Receives `LogRecord` via gRPC, batches into Postgres `COPY`. Default off (`LOG_WORKER_ENGINE=go` to activate). |
| `rate-limiter` | 50053 | built from `./rate-limiter` | Rust sliding-window rate limiter. 3-scope parallel Redis pipeline. Default off (`RATE_LIMITER_ENGINE=rust`). |
| `dlp-sidecar` | 50051 | built from `./dlp-sidecar` | Rust DLP regex engine. 22/30 detectors ported. Default off (`DLP_ENGINE=rust|shadow`). |

`docker-compose.override.yml` swaps `backend`, `admin-ui`, and `portal` into hot-reload mode with bind mounts when you run `docker compose up` directly (which `make up` does).

---

## 3. Quick start

**Prereqs:** Docker Desktop 4.27+ (Compose v2.24+).

```bash
cp .env.example .env
make up         # Linux / macOS / WSL
.\scripts\dev.ps1 up   # Windows PowerShell (alternative)
make seed       # first boot — creates admin + portal users
```

Then:

| URL | What | Credentials |
|---|---|---|
| http://localhost:3000 | Admin UI | `admin` / `admin1234` |
| http://localhost:3001 | Customer portal | `somchai@acme.ai.gateway` / `P@ssword` |
| http://localhost:8080/health | Backend health | — |
| http://localhost:8080/docs | Backend OpenAPI | — |
| http://localhost:8001/health | NER service | — |
| http://localhost:9090/health | Mock provider | — |

**Seeded portal users** (all password `P@ssword`):

| Email | Name | Role | Tenant |
|---|---|---|---|
| `somchai@acme.ai.gateway` | สมชาย ใจดี | owner | Acme Corporation |
| `suda@acme.ai.gateway` | สุดา รักงาน | member | Acme Corporation |
| `wichai@techstart.ai.gateway` | วิชัย เทคโนโลยี | owner | TechStart |
| `malee@techstart.ai.gateway` | มาลี สมาร์ท | member | TechStart |

**Seeded tenants:** Acme Corporation (`acme-corp-001`), TechStart (`techstart-001`).
**Seeded projects:** Customer Support Bot, Internal Assistant (Acme) · AI Chatbot MVP (TechStart).

For a customer-facing UAT box, see `scripts/uat/README.md` — bundled `01_truncate.sql` → `02_seed_uat.py` → `03_verify.sql` runbook.

Useful targets (`make help` for the full list):

| Command | Effect |
|---|---|
| `make up` / `make down` | Start / stop, keep volumes |
| `make clean` | Stop + wipe Postgres + Redis volumes |
| `make fresh` | `clean` + rebuild images + seed (use after adding Next.js routes — stale `.next` cache survives plain restart) |
| `make seed` | Re-run idempotent seeder |
| `make clean-portal-users` | Remove portal users not in the seed set |
| `make test` | Run backend pytest inside the container |
| `make shell-backend` / `shell-admin` / `shell-portal` | Exec into the container |

---

## 4. Module breakdown

### 4.1 DLP pipeline

The DLP scanner runs **30 regex detectors + 1 NER detector** by default, in registration order, against every inbound message (and outbound completion if the policy profile sets `scan_responses=true`). System-role messages are skipped unless `scan_system_messages=true`.

Pipeline stages (`backend/app/services/dlp/`):

1. **`preprocessor.py`** — NFKC normalization, zero-width strip, homoglyph fold, base64 decode-and-scan (findings are mapped back to original offsets).
2. **`language.py`** — single language detect across the joined message text so every Presidio call uses the same spaCy pipeline.
3. **`detectors/registry.py`** — runs each detector and merges matches.
4. **`classifier.py`** — assigns baseline severity per finding category.
5. **`transformer.py`** — masks each match with an indexed placeholder (`[EMAIL_0]`, `[PHONE_1]`) and builds a `span_map` for restore. When `metadata.conversation_id` is set, tokens are HMAC-derived from `DLP_HMAC_KEY` so the same value gets the same token across turns.
6. **`conversation_vault.py`** — Redis-backed store keyed by `conversation_id` for restoring tokens the model echoes back from earlier turns.

#### Detectors (`backend/app/services/dlp/detectors/`)

| Category | Detectors |
|---|---|
| **Standard PII** | `email`, `phone`, `ssn`, `credit_card`, `iban`, `passport`, `date_of_birth`, `address`, `ip_address`, `mac_address`, `vehicle_plate` |
| **Credentials / secrets** | `bearer_token`, `api_key_secret`, `private_key`, `password_field`, `db_connection_string` |
| **Thai-specific** | `thai_national_id` (with checksum), `thai_tax_id`, `thai_bank_account`, `thai_promptpay`, `thai_address`, `thai_name`, `line_id` |
| **PDPA Section 26** *(sensitive special category)* | `religion_belief`, `health_condition`, `criminal_record`, `biometric_data`, `medical_record` |
| **Financial** | `income` |
| **NER (via microservice)** | `presidio_ner` — PERSON, LOCATION, ORGANIZATION, GPE, NRP |
| **NER-disabled fallback** | `thai_name_no_prefix` — only registered when `ner_enabled=false`, to keep coverage for bare Thai names |

#### NER microservice (`backend/ner_service/`)

A separate FastAPI process — `main.py` + `Dockerfile` — preloads `en_core_web_lg` (preferred) or `en_core_web_sm` (fallback) into a Presidio `AnalyzerEngine` on startup. Optional Thai model `th_core_news_*` is registered if installed. The container is the only place where the heavyweight spaCy model lives; gateway workers stay light. The client at `backend/app/services/ner_service_client.py` has a 2.0 s default timeout and fails open — a NER outage degrades DLP coverage but does not drop the request.

#### Guardrails

`guardrail_service.py` and `moderation_service.py` run after DLP. `dlp_prompt.py` adds DLP-aware system prompt suffixes that warn the model not to echo masked tokens.

### 4.2 Model routing

Selection happens in `app/services/routing_service.py` with helpers in `keyword_router.py`, `keyword_rule_service.py`, and `semantic_router.py`. The order:

1. **Keyword router** — DB-backed rules in `keyword_rules` (migration 018). Match on substring → force a specific model. Edited from the admin UI under **Routing**.
2. **Semantic router** — only loaded if `requirements-semantic.txt` is installed; embedding-based fallback.
3. **Cost-tier scoring** — `model_registry._derive_cost_tier()` buckets candidates into `budget` / `standard` / `premium` from real input-token prices.
4. **Fallback chain** — `provider_dispatcher.dispatch_with_fallback` walks the routing result's fallback list on `ProviderError`.

#### Model registry (`providers/model_registry.py`)

DB-backed (`model_configs` table, migrations 036–038). Seeded with concrete models for every wired provider; reloaded into an in-memory singleton at FastAPI startup. The admin UI **Models** page edits live rows; **Model Discovery** (`services/discovery/`) pulls fresh model lists and pricing from upstream catalogs.

### 4.3 Governance

| Concern | Service | Storage |
|---|---|---|
| Rate limiting | `rate_limit_service.py` — per-key, per-project, per-tenant sliding window | Redis sorted sets (one pipeline per scope) |
| Response cache | `cache_service.py` — fail-open, gated off when DLP findings present | Redis `SETEX` |
| Quota | `quota_service.py` — daily request count per project | Postgres count on `request_logs` |
| Budget | `budget_service.py` — running cost cap per project | Postgres SUM on `request_logs.estimated_cost_usd` |
| Policies | `policy/profile_service.py` + `policy/evaluator.py` — per-direction per-detector actions (`allow` / `mask` / `block` / `restore`) | `policy_profiles` table (migration 015) |
| Token packs | `services/portal/` — prepaid credit grants | `token_pack`, `credit_transaction` (migrations 025, 031) |
| Retention | `jobs/dlp_retention.py`, `jobs/log_archive_job.py` | `request_logs_archive` partition |
| Partition maintenance | `jobs/partition_maintenance_job.py` — pre-creates next-month partition | `request_logs` (migration 041) |

The job scheduler in `backend/app/scheduler.py` registers all three jobs.

### 4.4 Observability

| Surface | Module | Notes |
|---|---|---|
| Request logs | `request_log_service.py` + `models/request_log.py` | One INSERT per request. Columns include masked + raw text snapshots (size-capped), `dlp_findings_json`, `dlp_trace`, `policy_profile_id`, `estimated_cost_usd`. |
| Encrypted audit blob | `core/dlp_audit_crypto.py` + `request_logs.input_raw_encrypted` (migration 039) | AES-256-GCM, key derived from `MASTER_ENCRYPTION_KEY`. |
| Secure trace store | `services/secure_trace_store.py` | Off-DB store for full request/response traces; admin-only access via `routes/admin.py`. |
| Admin audit log | `admin_audit_service.py` + `models/admin_audit_log.py` | Every admin mutation. Integrity chain via `audit_integrity.py`. |
| Alerts | `alert_service.py` + `alert_evaluator.py` | Post-request evaluation; rules in `alert_rules` (migrations 014, 017). |
| Usage rollups | `usage_service.py` | Aggregates for the admin Usage page and the portal Usage page. |
| Metrics | `observability/metrics.py`, `observability/phase_timer.py` | Per-stage latency hooks the orchestrator emits. |

### 4.5 Provider adapters (`backend/app/services/providers/`)

11 active adapters dispatched via `provider_dispatcher.py`:

| Provider | Adapter file | Notes |
|---|---|---|
| OpenAI | `openai_adapter.py` | Native API |
| Anthropic | `anthropic_adapter.py` | Native API |
| Google AI (Gemini) | `google_adapter.py` | Native API |
| Groq | `groq_adapter.py` | OpenAI-compatible |
| xAI (Grok) | `xai_adapter.py` | OpenAI-compatible |
| DeepSeek | `deepseek_adapter.py` | OpenAI-compatible |
| Qwen (Alibaba) | `qwen_adapter.py` | OpenAI-compatible |
| Kimi (Moonshot) | `kimi_adapter.py` | OpenAI-compatible |
| Z.AI (Yi) | `zai_adapter.py` | OpenAI-compatible |
| Minimax | `minimax_adapter.py` | Native API |
| OpenRouter | `openrouter_adapter.py` | Meta-provider, used as a fallback aggregator |

Shared infrastructure: `base.py` (adapter ABC), `base_urls.py` (URL overrides honored from `PROVIDER_BASE_URL_OVERRIDES`), `http_client.py` (shared httpx client with 5/30/5/5 s timeouts), `parsers.py` (response normalization), `error_handler.py` (provider-error classification for fallback decisions), `openai_compatible_adapter.py` (mixin used by the five OpenAI-compat adapters).

A `_pending/` folder holds Mistral, Cohere, and Perplexity adapters that the dispatcher imports but which are not part of the seeded model registry yet.

---

## 5. Multi-tenancy

| Entity | Model | Migration |
|---|---|---|
| Tenant | `models/tenant.py` | 002 |
| API key | `models/api_key.py` (tenant- + project-scoped) | 003, 005 |
| Project | `models/project.py` | — |
| Project member (RBAC inside a tenant) | `models/project_member.py` | 034 |
| Portal user | `models/portal_user.py` (email + OAuth) | 025, 026, 032 |
| Tenant invite | `models/tenant_invite.py` (with role + revoke) | 033 |
| Token pack (prepaid credits) | `models/token_pack.py` (tenant-scoped) | 031 |
| Credit transaction | `models/credit_transaction.py` | — |
| Org settings | `models/org_settings.py` (global NER toggle, defaults) | 015, 024 |
| System setting | `models/system_setting.py` (singleton-style flags) | 024 |

The customer portal at `/projects/[id]` exposes project-scoped API keys, team members, usage, and billing. The admin UI at `/portal-users`, `/portal-token-packs`, `/portal-settings` is the operator counterpart.

---

## 6. Database migrations

Alembic versions live in `backend/migrations/versions/`. Forward-only; tested by `make test`.

| Range | Theme |
|---|---|
| **001–010** | Schema bootstrap: tenants, API keys, usage columns, quotas, admin audit log, provider keys, dashboard indexes |
| **011–017** | Governance v1: policies → policy profiles + org settings, DLP findings on `request_logs`, alert rules + delivery fields, admin users table, audit actor-key widening |
| **018–023** | DLP / routing add-ons: keyword rules, per-tenant NER toggle, policy-profile-id + DLP trace on `request_logs`, AI review + provider config |
| **024–028** | Portal foundation: system settings, portal users + OAuth, seeded demo tenant, admin audit log extensions |
| **029–035** | Multi-tenancy maturity: portal tenancy + finalize, tenant-scoped token packs, preferred-language, invite roles + revoke, project members + archive, project-creator backfill |
| **036–038** | Model registry → DB: `model_configs`, seed rows, zero-price fill |
| **039–041** | Storage hardening: encrypted audit blob (`input_raw_encrypted`), compound indexes on `request_logs`, **monthly range partitioning of `request_logs`** |

---

## 7. API routes (`backend/app/api/routes/`)

| Surface | Routes |
|---|---|
| **Gateway (public, API-key auth)** | `public_chat.py` — `POST /v1/chat/completions` (streaming + non-streaming) · `public_models.py` — `GET /v1/models` · `health.py` — `GET /health` |
| **Auth** | `auth.py` (gateway), `portal_auth.py` (portal email + OAuth), `admin_session.py` (admin cookie session + CSRF) |
| **Admin (cookie auth, RBAC)** | `admin.py`, `admin_dashboard.py`, `admin_dlp.py`, `admin_ai_review.py`, `admin_system_settings.py`, `admin_token_packs.py`, `admin_portal_users.py`, `admin_model_discovery.py` |
| **Portal (JWT)** | `portal_me.py`, `portal_billing.py`, `portal_team.py`, `portal_projects.py` |

Interactive OpenAPI is at `http://localhost:8080/docs`.

---

## 8. Environment variables

Required in production (the values in `docker-compose.yml` are dev-only defaults):

| Var | Purpose |
|---|---|
| `JWT_SECRET` | Backend JWT signing |
| `PORTAL_JWT_SECRET` | Portal-issued JWT signing |
| `MASTER_ENCRYPTION_KEY` | AES-256-GCM key derivation for `input_raw_encrypted` |
| `DLP_HMAC_KEY` | Deterministic per-conversation token derivation |
| `NEXTAUTH_SECRET` | NextAuth on the portal |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, plus per-provider keys via the admin **Providers** page | Provider auth |

Optional:

| Var | Default | Effect |
|---|---|---|
| `NER_SERVICE_URL` | `http://ner-service:8001` | Where the gateway calls NER. Unset → legacy in-process loader. |
| `NER_SERVICE_TIMEOUT_SECONDS` | `2.0` | Client timeout before NER falls open. |
| `PROVIDER_BASE_URL_OVERRIDES` | empty | Comma-list of `provider=url`. Set to redirect `openai/anthropic/openrouter` at the in-cluster mock for tests. |
| `PORTAL_PUBLIC_URL` | `http://localhost:3001` | Stamped into verification + invite emails. |
| `CORS_ORIGINS` | dev defaults | Admin + portal origins. |
| `GOOGLE_CLIENT_ID` / `GITHUB_CLIENT_ID` (+ secrets) | empty | Portal OAuth providers. |
| `NEXT_PUBLIC_OAUTH_ENABLED` | `false` | Surface OAuth buttons in the portal. |

---

## 9. Repo layout

```
ai-gateway/
├── backend/
│   ├── app/
│   │   ├── api/routes/         # 18 route modules (admin / portal / gateway)
│   │   ├── core/               # config, db, exceptions, dlp_audit_crypto
│   │   ├── domain/             # dataclasses (dlp, routing, providers, ...)
│   │   ├── jobs/               # dlp_retention, log_archive, partition_maintenance
│   │   ├── middleware/         # error_handler, ...
│   │   ├── models/             # 22 SQLAlchemy ORM models
│   │   ├── observability/      # metrics, phase_timer
│   │   ├── repositories/
│   │   ├── schemas/            # pydantic request/response
│   │   ├── services/
│   │   │   ├── dlp/            # detectors/, classifier, transformer, language, ...
│   │   │   ├── discovery/      # model + pricing discovery
│   │   │   ├── policy/         # profile_service, evaluator, built-in profiles
│   │   │   ├── providers/      # 11 active adapters + base + http_client
│   │   │   ├── admin/, portal/, gateway/, email/
│   │   │   └── orchestrator.py, routing_service.py, cache_service.py, ...
│   │   ├── main.py, scheduler.py
│   ├── migrations/versions/    # alembic 001 → 041
│   ├── ner_service/            # FastAPI + spaCy/Presidio (own image)
│   ├── tests/                  # pytest, integration/, dlp_detectors/, ...
│   ├── scripts/                # seed_*, clean_portal_residue, ...
│   └── Dockerfile, requirements*.txt
├── admin-ui/                   # Next.js admin (18 pages)
├── portal/                     # Next.js customer portal (19 pages)
├── mock/                       # Mock LLM provider used in tests
├── dlp-sidecar/                # Rust DLP sidecar (22/30 detectors, gRPC :50051)
├── log-worker/                 # Go async batch logger (gRPC :50052, COPY to Postgres)
├── rate-limiter/               # Rust rate-limiter sidecar (gRPC :50053)
├── infra/
│   ├── docker/, terraform/
│   └── k8s/                    # backend, admin-ui, ner-service deployments
├── scripts/                    # dev/seed/migrate/smoke (.sh + .ps1)
│   └── uat/                    # UAT bringup: 01_truncate.sql, 02_seed_uat.py, 03_verify.sql, .env.uat.template
├── docs/                       # architecture notes, runbooks
├── examples/
├── docker-compose.yml          # base stack
├── docker-compose.override.yml # dev hot-reload + bind mounts
├── Makefile                    # up/down/seed/test/clean/fresh
├── RUNBOOK.md
└── README.md
```

---

## 10. Running tests

```bash
make test                              # full backend pytest
docker compose exec backend pytest tests/test_dlp_pipeline_suite/   # subset
docker compose exec backend pytest -k orchestrator                  # filter
```

Test areas (`backend/tests/`):

- `test_dlp_detectors/` — per-detector accuracy (`test_email.py`, `test_thai_national_id.py`, `test_passport.py`, ...).
- `test_dlp_pipeline_suite/` — mask/restore round-trip, multi-value indexing, overlap resolution, token scope.
- `test_dlp_regression_suite/test_known_bugs.py` — anchored regressions.
- `integration/` — `test_dlp_roundtrip_live.py`, `test_dlp_dataflow_hops.py`, `test_multiturn_vault.py`, `test_fixes_live.py` (require a running stack).
- `test_routing_engine.py`, `test_routing_priority.py`, `test_routing_cost_scoring.py` — model selection.
- `test_policy_evaluator.py`, `test_policy_*.py` — policy profile semantics.
- `test_streaming_dlp.py` — outbound DLP across SSE chunks.
- `test_e2e_dlp_flow.py` — end-to-end DLP path.
- `test_admin_secure_trace.py`, `test_usage_cost_split.py`, `test_currency_service.py`, `test_model_discovery.py`, ...

The `Makefile`'s `make test` target deliberately unsets compose-injected env vars (`POSTGRES_HOST`, `REDIS_HOST`, ...) so tests that assert pydantic `Settings` defaults see the class defaults, not the container env.

---

## 11. Performance notes

Measurements from the live dev stack on this repo (single host, defaults, idle baseline; see `RUNBOOK.md` for the full audit):

- **Per-request gateway latency (no provider):** ~65–115 ms — dominated by 30 sequential regex detectors (~15–25 ms), one NER HTTP call (21 ms warm, p95 ~27 ms), AES encrypt (5–10 ms, blocks event loop), 8–12 Postgres SELECTs, one INSERT.
- **NER service is the first bottleneck.** Single uvicorn worker, GIL-bound spaCy → ~47 analyze/sec ceiling per pod. Each chat completion is one NER call inbound + (optionally) one outbound — effective **23–47 chat-completions/sec per NER pod**.
- **Postgres connections:** 4 backend workers × `pool_size=10 + overflow=20` requests up to 120 connections vs. the default `max_connections=100`. Under sustained load this becomes the second wall.
- **`synchronous_commit=on` + `fsync=on`** on Postgres — every write blocks for fsync. Acceptable on local SSD, much heavier on networked storage.
- **Redis** is not a throughput bottleneck (3–6 ops/request, ceiling well above 100k ops/sec), but `maxmemory=0 / noeviction` means a leak will OOM the box.
- **Request log row size** is currently ~1 KB average in dev; production traffic with retained snapshots and the encrypted blob lands around 3–10 KB. `request_logs` is monthly range-partitioned (migration 041) and a `request_logs_archive` partition exists for cold rows.
- **Scaling path:**
  1. Replicate the NER service (`--workers N` in the Dockerfile CMD, or scale the K8s `Deployment` — `infra/k8s/ner-service-deployment.yaml`).
  2. Reconcile backend pool with `max_connections`.
  3. Move AES encrypt off the event loop and run regex detectors in a threadpool.
  4. Set `maxmemory` + an eviction policy on Redis.

These are honest figures from this build, not theoretical headroom.

### Phase 1–3 improvements (shipped)

The four scaling items above have largely landed. The legacy / Python path is still the default everywhere so the gateway behaves identically to the pre-Phase-1 build; each replacement is gated by an env var and falls back to the legacy path on any error (timeout, missing dep, sidecar unreachable).

| Area | Before | After | Method |
|---|---|---|---|
| NER throughput | ~47 RPS | ~200 RPS | 4 uvicorn workers in `ner-service` |
| DB pool | `pool=10 overflow=20` | `pool=20 overflow=10` | explicit `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` |
| PG write latency | 3–5 ms/write | <1 ms | `synchronous_commit=off` + `wal_writer_delay=200ms` |
| Request logging | sync INSERT, blocks event loop | fire-and-forget Go COPY batch | Go `log-worker` sidecar (:50052) |
| Rate limiter | Python async Redis pipeline | Rust sliding-window (:50053) | Rust `rate-limiter` sidecar |
| Fallback routing | 503 on provider failure | 3-tier model fallback chain | `FallbackOrchestrator` + `model_tiers.py` |
| DLP engine *(in progress)* | Python regex 15–25 ms | Rust <1 ms (target) | `dlp-sidecar` Rust sidecar (:50051) — 22/30 detectors ported, shadow mode pending |

**Activation** (all default to the Python / legacy path):

- `LOG_WORKER_ENGINE=go` — route request logs through the Go COPY worker.
- `RATE_LIMITER_ENGINE=rust` — route rate-limit checks through the Rust sidecar.
- `DLP_ENGINE=shadow` — run the Rust DLP scanner in parallel with Python and log diffs (no cutover yet).
- `DLP_ENGINE=rust` — full cutover (only after a 7-day shadow window with diff = 0).
