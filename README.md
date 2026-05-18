# SRAN AI Gateway

Multi-tenant AI proxy with DLP, guardrails, routing, and observability.

PDPA-aware DLP scans every request, masks sensitive values before they reach
an LLM, and restores them in the response. A policy profile decides per
detector category whether to allow / mask / block / restore. Routing picks
the cheapest model that satisfies the matched keyword rule, with a
3-tier fallback chain on provider failures. Everything is logged into a
monthly-partitioned `request_logs` table for the admin audit surface.

Tagged release: **`v0.1.0-uat`** — see [CHANGELOG.md](./CHANGELOG.md).

---

## 1. Architecture

Four functional modules in one FastAPI process, with three sidecar
services (NER, log-worker, rate-limiter) and two Next.js apps:

| Module | Responsibility | Where |
|---|---|---|
| **Safety** | DLP scanning, masking, restore; guardrails; moderation | `app/services/dlp/`, `guardrail_service.py`, `moderation_service.py` |
| **Model routing** | Keyword → semantic → cost-tier scoring → fallback chain | `routing_service.py`, `keyword_router.py`, `semantic_router.py`, `provider_dispatcher.py` |
| **Governance** | Rate limiting, response cache, quotas, budgets, policy profiles, token packs | `rate_limit_service.py`, `cache_service.py`, `quota_service.py`, `budget_service.py`, `policy/` |
| **Observability** | Request logs, encrypted audit blob, secure trace store, alerts, metrics | `request_log_service.py`, `core/dlp_audit_crypto.py`, `secure_trace_store.py`, `alert_service.py` |

Stack: FastAPI + Next.js (admin + portal) + PostgreSQL 16 + Redis 7.
Multi-provider, 11 active adapters: **OpenAI, Anthropic, Google (Gemini),
Groq, xAI (Grok), DeepSeek, Qwen, Kimi, Z.AI, Minimax, OpenRouter**.

```
HTTP clients (SDK, curl)
        │
        ▼  POST /v1/chat/completions  (API key)
┌──────────────────────────────────────────────────────────────┐
│ Backend · FastAPI · :8080                                    │
│                                                              │
│  rate-limit → api-key → policy → orchestrator:               │
│    1. cache lookup (Redis)                                   │
│    2. DLP inbound: regex detectors + NER ─► ner-service:8001 │
│    3. policy evaluate (block / mask / restore)               │
│    4. mask → <rN> tokens (deterministic w/ DLP_HMAC_KEY)     │
│    5. moderation + guardrail                                 │
│    6. routing (keyword → semantic → cost-tier → fallback)    │
│    7. provider dispatch ─► OpenAI / Anthropic / 9 others     │
│    8. DLP outbound + restore + orphan sanitiser              │
│    9. encrypt input_raw_encrypted (AES-256-GCM)              │
│   10. INSERT request_logs (monthly partitions)               │
└──────────────────────────────────────────────────────────────┘
        │                  │                    │
        ▼                  ▼                    ▼
  Postgres :5432    Redis :6379         Mock provider :9090
                                        (for tests via
                                        PROVIDER_BASE_URL_OVERRIDES)

  Admin UI :3000  ·  Customer Portal :3001
```

---

## 2. DLP Pipeline

The most mature module. Every chat completion runs through it.

### `<rN>` neutral tokens

Masked values are replaced with sequential opaque tokens:
`<r0>`, `<r1>`, `<r2>`, …

The format is **deliberately category-free**. The previous format
(`[EMAIL_0]`, `[BEARER_TOKEN_0]`, `[NATIONAL_ID_0]`) caused two failure
modes the audit caught repeatedly:

1. **AI refusal** — OpenAI's safety filter reads `[BEARER_TOKEN_0]` as a
   sensitive-data marker and refuses the whole prompt.
2. **Restore breakage** — the LLM renames tokens (`[AUTH_TOKEN_0]`
   instead of `[BEARER_TOKEN_0]`), and the unmask map can't find them.

`<rN>` carries no semantic hint, so the safety filter ignores it and the
LLM echoes tokens verbatim. When `metadata.conversation_id` is set on the
request, tokens are HMAC-derived from `DLP_HMAC_KEY` so the same value
gets the same token across turns — needed for multi-turn restore.

### Round-trip flow

```
user input   ─► detect ─► mask ─► [LLM input has <rN>]
                                       │
                                       ▼ provider returns text with <rN>
                                       │
LLM output ─► outbound scan ─► restore ─► orphan-sanitiser ─► user
                                                 │
                                                 └─ any leftover <rN>
                                                    (LLM hallucination)
                                                    becomes [REDACTED]
```

### Detectors (31 total)

Registered in `backend/app/services/dlp/detectors/registry.py`:

| Category | Detectors |
|---|---|
| **Identity** | `passport`, `thai_national_id` (mod-11), `thai_tax_id` (mod-11 + strong-prefix relax), `date_of_birth` |
| **Financial** | `credit_card` (Luhn), `iban`, `thai_bank_account`, `thai_promptpay`, `income` |
| **Credential / secret** | `bearer_token`, `api_key_secret`, `private_key`, `password_field`, `db_connection_string` |
| **Standard PII** | `email`, `phone` (trivial-digit guard), `ssn`, `address`, `thai_address`, `thai_name`, `line_id`, `vehicle_plate` |
| **PDPA Section 26 (sensitive)** | `religion_belief`, `health_condition`, `criminal_record`, `biometric_data`, `medical_record` |
| **Network** | `ip_address`, `mac_address` |
| **NER (via microservice)** | `presidio_ner` — PERSON / LOCATION / ORGANIZATION / GPE / NRP |
| **NER-off fallback** | `thai_name_no_prefix` — only when `ner_enabled=false` |

### Policy profiles

Two seeded profiles in `app/services/policy/`:

| Profile | Behaviour |
|---|---|
| `default` | Mask PII categories; restore identity / financial on outbound (user sees own values back); block on `severity=critical` credentials |
| `strict` | Block on any `secret` / `credential` / sensitive-category finding |

Per-category action is per-direction (inbound vs outbound), stored in
`policy_profiles` (migration 015), edited from the admin UI.

### E2E test results (`v0.1.0-uat`)

50-case round-trip suite (`test_dlp_e2e_50.py` at repo root):

| Metric | Score |
|---|---|
| Fully correct end-to-end | **41 / 50** |
| Mask accuracy | 38 / 39 (97%) |
| Round-trip restore | 35 / 39 (90%) |
| AI refusal rate | **0 / 50** |
| False positives | 3 (documented limitations) |
| False negatives | 1 (documented limitation) |

Before / after traces are committed at repo root as
`test50_results_before.json` and `test50_results_after.json` for diff.

---

## 3. Quick Start

**Prereqs**: Docker Desktop 4.27+ (Compose v2.24+).

```bash
git clone https://github.com/BeatChalongrat/ai-gateway.git
cd ai-gateway
cp .env.example .env
make up         # Linux / macOS / WSL
# or:  .\scripts\dev.ps1 up   (Windows PowerShell)
make seed       # first boot — creates admin + portal users
```

| URL | What | Default credentials |
|---|---|---|
| http://localhost:8080/health | Backend health | — |
| http://localhost:8080/docs | OpenAPI (Swagger) | — |
| http://localhost:3000 | Admin UI | `admin` / `admin1234` |
| http://localhost:3001 | Customer portal | `somchai@acme.ai.gateway` / `P@ssword` |
| http://localhost:8001/health | NER service | — |
| http://localhost:9090/health | Mock LLM provider | — |

Ports inside the stack: Postgres `:5432`, Redis `:6379`.

Useful `make` targets (`make help` for the full list):

| Command | Effect |
|---|---|
| `make up` / `make down` | Start / stop, keep volumes |
| `make clean` | Stop + wipe Postgres + Redis volumes |
| `make fresh` | `clean` + rebuild images + seed |
| `make seed` | Re-run idempotent seeder |
| `make test` | Backend pytest inside the container |
| `make shell-backend` | Exec into the backend container |

For a clean UAT box, see `scripts/uat/README.md` —
`01_truncate.sql` → `02_seed_uat.py` → `03_verify.sql` runbook plus an
`.env.uat.template`.

---

## 4. API Usage

### Issue an API key

Admin UI → **API Keys** → create a project-scoped key. Keys start with
`gw_`.

### Chat completion (OpenAI-compatible schema)

```bash
curl -sS -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer gw_<your_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Summarize: customer somchai@test.com called about a login issue."}
    ]
  }'
```

DLP runs transparently. The LLM never sees `somchai@test.com` — it
sees `<r0>`. The reply is restored to the user with the original email
in place. The response includes a `policy_action` field
(`allow` / `mask` / `block`) and `policy_reasons`.

### Multi-turn restore

Add `metadata.conversation_id` to derive deterministic tokens across
turns:

```bash
curl -sS -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer gw_<your_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Reply to John at john@test.com"}
    ],
    "metadata": {"conversation_id": "thread-42"}
  }'
```

A later turn in `thread-42` that echoes `<r0>` will resolve to
`john@test.com` via the Redis-backed conversation vault.

### Models

```bash
curl -sS http://localhost:8080/v1/models -H "Authorization: Bearer gw_<your_key>"
```

Returns the DB-backed model registry (`model_configs` table) — the same
list the admin UI's **Models** page edits.

---

## 5. Admin UI (`:3000`)

Cookie session auth, CSRF-protected, rate-limited login. Routes live in
`backend/app/api/routes/admin*.py`.

| Page | What |
|---|---|
| **Dashboard** | Request volume, cost, top models, recent activity |
| **Requests** | Full audit log — input/output snapshots, masked vs raw, `dlp_findings_json`, latency, cost |
| **Secure Trace** | Per-request 4-stage trace (user→service, service→AI, AI→service, service→user) — admin-only |
| **DLP** | Per-detector toggles, policy profile editor (per-category + per-direction action), `<rN>` round-trip preview |
| **Routing** | Keyword rules, semantic router config, cost-tier weights |
| **Models** | Live `model_configs` table — pricing, fallback tiers; **Model Discovery** pulls fresh upstream catalogs |
| **Providers** | Per-provider API key + base URL overrides |
| **Alerts** | Rule editor + delivery channels (email / webhook) |
| **Token Packs** | Prepaid credit grants (per-tenant) |
| **Portal Users** | Operator view of customer portal users |
| **Settings** | NER toggle, retention days, system flags |

---

## 6. Testing

```bash
make test                                            # full backend pytest
docker compose exec backend pytest tests/test_dlp_pipeline_suite/   # subset
docker compose exec backend pytest -k orchestrator                  # filter
python test_dlp_e2e_50.py                            # E2E round-trip (requires running stack)
```

Test layout (`backend/tests/`):

- `test_dlp_detectors/` — per-detector accuracy
- `test_dlp_pipeline_suite/` — mask/restore round-trip, indexing, overlap resolution
- `test_dlp_regression_suite/test_known_bugs.py` — anchored regressions for each shipped bug fix
- `integration/` — `test_dlp_roundtrip_live.py`, `test_multiturn_vault.py`, etc. (require live stack)
- `test_routing_*`, `test_policy_*`, `test_streaming_dlp.py`, `test_e2e_dlp_flow.py`

**Current status**: 2449 passing · 95 failing (baseline, pre-existing) ·
19 skipped · 3 xfailed. The 95 failures are not regressions from
`v0.1.0-uat` — they pre-date this branch and are tracked in the
roadmap below.

---

## 7. Known Limitations

Pulled from [`CHANGELOG.md`](./CHANGELOG.md):

- **Medical condition names** (เบาหวาน, ภูมิแพ้, ความดันสูง) mask even
  in generic role-noun contexts ("the customer has diabetes"). This is
  intentional under **PDPA Section 26** (health is sensitive). Cases
  T19 / T20 in the E2E test.
- **Labeled salary amounts** ("เงินเดือน 85,000 บาท") mask when an
  income label is present. Income is PII under most frameworks.
  Case T27.
- **Short `sk-test-*` API keys** (under 20 chars after the prefix) are
  not detected — the gateway can't distinguish them from product codes
  like `sk-test-cream-001`. Precision over recall for credentials.
  Case T41.
- **Orphan `<rN>` may appear in ~5% of LLM responses**. The orphan
  sanitiser strips hallucinated tokens, but the outbound scan can mint
  new tokens whose index happens to match a hallucinated one and the
  sanitiser preserves them as legitimate. Run-to-run variance from
  LLM stochasticity.

---

## 8. Roadmap

Near-term:
- Close out the 95 baseline pytest failures (mix of pre-Phase-3 churn
  and integration tests that need a fresh DB)
- Outbound token-index allocation rework so hallucinated indices
  cannot collide with newly-minted outbound masks
- Address detector v2: structural form rather than keyword list
- Medical NER refinement (Thai-specific) instead of the keyword regex
- Full Rust DLP cutover (`DLP_ENGINE=rust`) — currently 22/30
  detectors ported, in shadow mode

Production hardening:
- Move AES encrypt off the event loop
- Reconcile backend DB pool with Postgres `max_connections`
- Set `maxmemory` + eviction policy on Redis
- K8s manifests under `infra/k8s/` need probes + HPAs

---

## 9. Repo Layout

```
ai-gateway/
├── backend/                       # FastAPI gateway
│   ├── app/
│   │   ├── api/routes/            # admin / portal / gateway routes
│   │   ├── services/
│   │   │   ├── dlp/               # detectors/, classifier, transformer, ...
│   │   │   ├── providers/         # 11 adapters
│   │   │   ├── policy/            # profile_service, evaluator
│   │   │   └── orchestrator.py
│   │   └── models/                # SQLAlchemy ORM
│   ├── migrations/versions/       # alembic 001 → 041
│   ├── ner_service/               # FastAPI + spaCy/Presidio (own image)
│   └── tests/
├── admin-ui/                      # Next.js admin
├── portal/                        # Next.js customer portal
├── mock/                          # Mock LLM provider for tests
├── dlp-sidecar/                   # Rust DLP sidecar (22/30 detectors)
├── log-worker/                    # Go batch logger
├── rate-limiter/                  # Rust sliding-window rate limiter
├── scripts/uat/                   # UAT bringup
├── test_dlp_e2e_50.py             # 50-case DLP round-trip E2E
├── CHANGELOG.md                   # release notes
└── docker-compose.yml             # base stack
```

---

## 10. Environment Variables

Required in production (dev defaults live in `docker-compose.yml`):

| Var | Purpose |
|---|---|
| `JWT_SECRET` | Backend JWT signing |
| `PORTAL_JWT_SECRET` | Portal-issued JWT signing |
| `MASTER_ENCRYPTION_KEY` | AES-256-GCM key for `input_raw_encrypted` |
| `DLP_HMAC_KEY` | Deterministic per-conversation `<rN>` derivation |
| `NEXTAUTH_SECRET` | NextAuth on the portal |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … | Provider auth (or set via admin **Providers** page) |

Optional:

| Var | Default | Effect |
|---|---|---|
| `NER_SERVICE_URL` | `http://ner-service:8001` | Where the gateway calls NER |
| `NER_SERVICE_TIMEOUT_SECONDS` | `2.0` | Client timeout (fails open) |
| `PROVIDER_BASE_URL_OVERRIDES` | empty | Redirect providers at the mock for tests |
| `LOG_WORKER_ENGINE` | `python` | Set to `go` to enable the gRPC log sidecar |
| `RATE_LIMITER_ENGINE` | `python` | Set to `rust` to enable the gRPC rate-limit sidecar |
| `DLP_ENGINE` | `python` | `shadow` runs Rust+Python in parallel (diff logged); `rust` cuts over |
| `CORS_ORIGINS` | dev defaults | Admin + portal origins |

Full list: `backend/app/core/config.py` and `.env.example`.

---

## 11. License & Contributing

See repo top-level for license. PRs welcome — open against `main` with
a passing `make test` (failures must not exceed the 95 baseline).
