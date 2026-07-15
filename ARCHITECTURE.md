# SRAN AI Gateway — Architecture

This is the system-level map: services, the request path, and the security
boundaries. For a fast setup and API examples see [README.md](./README.md).
For the deep DLP design (detector registry, masking invariant, egress policy,
sandbox trust boundary) the single source of truth is
[docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md) — this file summarizes and
points there rather than forking it.

---

## 1. What this is

A multi-tenant AI proxy whose **core value is a DLP masking gateway**: PDPA-aware
scanning masks sensitive values before any request reaches a third-party LLM and
restores them in the response. Around that core the platform adds an agentic
desktop app, a code interpreter, OAuth connectors, per-user MCP workspaces,
chat + KB-RAG, web search, and image/video/audio generation.

The system grew via a **strangler-fig migration**: a Go API gateway now sits in
front of the original FastAPI backend as the public entry point, and several hot
paths (DLP, rate-limiting, log-writing) have Rust/Go sidecars that are gated on
by env flag with fail-open fallback to the Python implementation.

---

## 2. Request path (the important part)

The public entry point is the **Go gateway on `:8080`** — *not* the FastAPI
backend. The backend is an internal service; its host port moved to `:8081` for
debugging only. External clients must hit `:8080`.

```
   HTTP client (SDK / curl / web-chat / desktop app)
        │
        ▼  POST /v1/chat/completions   (Authorization: Bearer gw_…)
┌──────────────────────────────────────────────────────────────────┐
│  Go gateway ·  :8080  (services/gateway)   ← PUBLIC ENTRYPOINT     │
│    coarse API-key auth · request-ID · Prometheus · body cap        │
│    transparent reverse proxy (streaming included) →                │
└───────────────────────────────┬──────────────────────────────────┘
                                 ▼  http://backend:8080 (internal)
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI backend · container :8080 / host :8081  (backend/)        │
│                                                                    │
│   rate-limit → api-key → policy → orchestrator:                    │
│     1. cache lookup (Redis)                                        │
│     2. DLP inbound: regex detectors + NER ──► ner-service:8001     │
│     3. policy evaluate (allow / mask / redact / block)             │
│     4. MASK → neutral tokens (deterministic w/ DLP_HMAC_KEY)       │
│     5. moderation + guardrail                                      │
│     6. routing (keyword → semantic → cost-tier → fallback)         │
│     7. provider dispatch ──► OpenAI / Anthropic / 9 others         │
│     8. DLP outbound + RESTORE + orphan sanitiser                   │
│     9. encrypt input_raw_encrypted (AES-256-GCM)                   │
│    10. INSERT request_logs (monthly partitions)                    │
└───────┬───────────────────┬──────────────────┬───────────────────┘
        ▼                   ▼                  ▼
   Postgres            Redis (auth'd)     provider APIs
   (internal)          (internal)         (or mock :9090 in tests)
```

**The masking invariant** — raw sensitive values reach a provider only as neutral
tokens; the raw value lives in transient request memory and is never written to
any log/DB/external system (only the token-free `DLPAuditEntry` is persisted).
Full detail, including the deliberate exceptions, is in
[docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md). Two summarized below.

---

## 3. Services

Started by `docker-compose.yml` (+ overlays). Ports shown are **host** ports.

| Service | Dir | Host port | Role |
|---|---|---|---|
| **gateway** (Go) | `services/gateway` | **8080** | Public entrypoint; reverse-proxies to backend |
| **backend** (FastAPI) | `backend/` | 8081 | DLP, routing, dispatch, auth, logging — authoritative |
| **chat-edge-api** (Go) | `services/chat-edge-api` | 4000 | Conversations, memory, KB/RAG, files, web search, media tools, code interpreter. `profiles: [desktop]` |
| **mcp-gateway** (Go) | `services/mcp-gateway` | 3200 | Per-user JWT-verified MCP servers + OAuth connectors. `profiles: [desktop]` |
| **ner-service** (FastAPI + spaCy/Presidio) | `backend/ner_service` | 8001 | NER detection sidecar for DLP |
| **dlp-sidecar** (Rust) | `dlp-sidecar/` | 9102 (metrics) | gRPC DLP engine; gated `DLP_ENGINE=rust\|shadow` |
| **rate-limiter** (Rust) | `rate-limiter/` | 9101 (metrics) | gRPC sliding-window; gated `RATE_LIMITER_ENGINE=rust` |
| **log-worker** (Go) | `log-worker/` | 9100 (metrics) | gRPC async request-log sink; gated `LOG_WORKER_ENGINE=go` |
| **mock-provider** | `mock/` | 9090 | Fake LLM for tests (`PROVIDER_BASE_URL_OVERRIDES`) |
| **postgres** (pgvector/pg16) | — | internal | Main DB + chat-edge DB + KB embeddings |
| **redis 7** (auth'd) | — | internal | Session store, DLP token vault, rate-limit state, cache |
| **prometheus** | `infra/prometheus` | 9091 | Scrapes gateway/backend/redis |
| **grafana** | `infra/grafana` | 3100 | Dashboards |
| **nginx** | `infra/nginx` | 80/443 | Single-domain reverse proxy. `profiles: [desktop, scaling]` |

Frontends and clients:

| Client | Dir | Host port | Talks to |
|---|---|---|---|
| **admin-ui** (Next.js) | `admin-ui/` | 3000 | gateway + chat-edge admin |
| **portal** (Next.js) | `portal/` | 3001 | gateway (+ OAuth connectors) |
| **web-chat** (Next.js) | `apps/web-chat/` | 3002 | gateway + chat-edge (`docker-compose.webchat.yml`) |
| **SranDesktop** (.NET 8 WPF) | `SranDesktop/` | — | gateway + chat-edge + mcp-gateway |

Sidecars default to their Python/in-process equivalents; each flips on by env
flag and fails open. See README §"Environment Variables" and `docker-compose.yml`
comments for the exact flags.

---

## 4. Feature surface (honest)

- **DLP masking gateway** — the core. Mask → dispatch → restore, per-tenant
  policy profiles, deterministic per-conversation tokens.
- **Chat + KB-RAG** — chat-edge conversations, memory, per-user knowledge bases
  with pgvector embeddings.
- **Desktop agentic app** — `SranDesktop/`, a .NET 8 WPF client over
  gateway + chat-edge + MCP.
- **Code interpreter / sandbox** — model-authored Python (`run_python`) and
  plan-exec workspaces (Excel/Doc/Pptx). **OFF by default** (`SANDBOX_ENABLED`
  defaults to false); when off, `run_python` returns 503 and plan-exec falls back
  to the in-process path. See §5 for the trust boundary.
- **OAuth connectors** — per-user GitHub / Slack / Google, tokens stored
  AES-256-GCM in `connector_tokens` (migration 063), brokered to mcp-gateway.
- **MCP per-user workspaces** — `mcp-gateway` hosts stdio MCP servers behind a
  JWT-verified per-user proxy.
- **Web search** — `/v1/search` → Tavily/Brave/SerpAPI/Google/Bing (raw-egress;
  see §5).
- **Image / video / audio generation** — `/v1/images|videos|audio/generations`
  and the chat-edge media tool path (raw-egress; see §5). Video is flag-gated
  (`VIDEO_GEN_ENABLED`).
- **Per-tenant DLP egress policy** — `tenants.search_pii_egress` /
  `media_pii_egress` (migration 064) let a compliance-strict tenant fail search /
  media closed on any identity PII.
- **Observability** — encrypted audit blob, secure 4-stage trace store,
  monthly-partitioned `request_logs`, Prometheus/Grafana.

---

## 5. Security boundaries

The masking invariant has exactly **two deliberate raw-egress channels** and one
**internal trust boundary**. All three are documented, machine-checked, and
detailed in [docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md); the summary:

1. **Web search + media generation — intentional raw egress (Phase 9).** These
   two channels send user content to providers *without* identity-PII masking,
   because masking breaks the feature (a search for `<PERSON_1>` returns garbage;
   a masked image prompt yields the wrong image). Bounded by: the credential /
   secret class is *always* withheld (403 on leak), per-tenant `block` mode fails
   closed, every PII-carrying egress is audited as `raw_egress_allow` with
   category counts only (never the text), and the whitelist is a single constant
   pinned by `tests/test_dlp_egress_invariant.py` (CI fails if a new outbound-HTTP
   module isn't classified).

2. **Code interpreter sandbox — internal trust boundary (Phase 13), not egress.**
   The interpreter processes *raw* user data, but nothing it touches can reach a
   third party from inside. Each run is an ephemeral `docker run --rm` of the
   `ai-gateway-sandbox` image, launched by trusted Go with a fixed lockdown argv
   (no model input in the argv): `--network none` **plus** a default-deny seccomp
   profile with every networking syscall removed, `--cap-drop ALL`,
   `--user 65534`, `no-new-privileges`, read-only rootfs, no inherited secrets.
   Interpreter stdout/artifacts re-enter the model only through the normal DLP
   mask pipeline. Pinned by a Go test (`TestSandbox_DockerArgsAlwaysNoNetwork`)
   and the Python egress invariant test (which asserts the interpreter is
   *absent* from the raw-egress whitelist). **OFF by default.**

---

## 6. Data & migrations

- **Backend schema** — Alembic under `backend/migrations/versions/`; latest is
  **`064_tenant_pii_egress_policy.py`**. The backend applies migrations on
  startup.
- **Chat-edge schema** — separate SQL migrations in
  `services/chat-edge-api/migrations`, applied by the Postgres init hook on a
  *fresh* volume only. On an existing volume run `make chat-edge-migrate-main`
  (main stack) or `make chat-edge-migrate` (webchat stack) after pulling new ones
  — the Go binary fail-closed *verifies* migrations, it does not apply them.
- `request_logs` is monthly-partitioned; the AES-GCM audit blob and secure trace
  store hold the encrypted per-request detail.

---

## 7. Deployment shapes

| Shape | Command | What runs |
|---|---|---|
| **dev** | `make up` (`docker-compose.yml`) | Core stack, ports on 0.0.0.0. chat-edge/mcp/nginx are `profiles: [desktop]` and do **not** start here |
| **web-chat dev** | `docker-compose.webchat.yml` overlay | Adds web-chat `:3002` + chat-edge on its own pg/redis |
| **prod-desktop** | `make prod-desktop` | base + `docker-compose.prod.yml` + `--profile desktop` → chat-edge + mcp-gateway + nginx (`:80/:443`), all bound to 127.0.0.1 behind nginx |
| **sandbox overlay** | `+ docker-compose.sandbox.yml` on webchat | Wires chat-edge to spawn per-run sandbox containers (`SANDBOX_ENABLED=1`); requires building `ai-gateway-sandbox:latest` and a docker socket — read the header in that file first |

`REDIS_PASSWORD` is **mandatory** for every shape — the redis service and its
consumers (`?:` guards in compose) refuse to start without it.

---

## 8. Where things live in the backend

| Concern | Path |
|---|---|
| DLP detectors / classifier / transformer | `backend/app/services/dlp/` |
| Egress whitelist (raw-egress invariant) | `backend/app/services/dlp/egress_policy.py` |
| Policy profiles + evaluator | `backend/app/services/policy/` |
| Orchestrator (request lifecycle) | `backend/app/services/orchestrator.py` |
| Provider adapters (11) | `backend/app/services/providers/` |
| Admin / portal / gateway routes | `backend/app/api/routes/` |
| Sandbox launcher (Go) | `services/chat-edge-api/internal/sandbox/` |
| Config (every env var) | `backend/app/core/config.py` |
</content>
