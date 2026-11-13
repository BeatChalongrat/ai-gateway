# SRAN AI Gateway — Architecture

Artifact workspace: [durable drafts, checkpointed proposals and review-gated publication](docs/ARTIFACT_WORKSPACE.md) use Chat Edge migration 051 and the shared Web/Electron renderer.

Office follow-up (2026-11-13): shared visual editor → fixed sandbox editing/rendering → immutable revisions. Optional vision review uses the gateway with explicit raw-page egress policy, then validates and re-renders typed corrections before publication. See [visual architecture](docs/VISUAL_EDITOR.md) and [editor/video contracts](docs/OFFICE_EDITOR.md).

Media follow-up: image references use the provider edit API; motion graphics render in the existing sandbox; premium video jobs wait in `pending_confirm` until owner confirmation. Read [Office/media parity](docs/OFFICE_MEDIA_PARITY.md) for the current source assessment.

Source review: **2026-11-12**. Runtime configuration and deployed image revisions
must be inspected separately; this document describes the checked-out code.

Office follow-up: both clients share 12 document/data starters and a pre-delivery
quality stage. The optional `docker-compose.office.yml` adds an internal
LibreOffice converter for recalculation, rendering and previews; see
[Office templates](docs/OFFICE_TEMPLATES.md) for activation and limits.

This is the system-level map: services, the request path, and the security
boundaries. For a fast setup and API examples see [README.md](./README.md).
For the deep DLP design (detector registry, masking invariant, egress policy,
sandbox trust boundary) the single source of truth is
[docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md) — this file summarizes and
points there rather than forking it.

---

## 1. What this is

A multi-tenant AI proxy whose **core value is a DLP masking gateway**: PDPA-aware
scanning masks detected sensitive text on the standard completion path and
restores it in the response. Explicit unmasked routes and image bytes have
different boundaries (section 5). Around that core the platform adds an agentic
desktop app, a code interpreter, OAuth connectors, per-user MCP workspaces,
chat + KB-RAG, web search, and image/video/audio generation.

The system grew via a **strangler-fig migration**: a Go API gateway now sits in
front of the original FastAPI backend as the public entry point, and several hot
paths (DLP, rate-limiting, log-writing) have Rust/Go sidecars that are gated on
by env flag with fallback to the Python implementation; this does not bypass DLP.

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
│     4. MASK → neutral tokens (counters, stable via the vault)      │
│     5. moderation + guardrail                                      │
│     6. routing (keyword → semantic → cost-tier → fallback)         │
│     7. provider dispatch ──► OpenAI / Anthropic / 10 others         │
│     8. DLP outbound + RESTORE + orphan sanitiser                   │
│     9. encrypt input_raw_encrypted (AES-256-GCM)                   │
│    10. INSERT request_logs (monthly partitions)                    │
└───────┬───────────────────┬──────────────────┬───────────────────┘
        ▼                   ▼                  ▼
   Postgres            Redis (auth'd)     provider APIs
   (internal)          (internal)         (or mock :9090 in tests)
```

**The standard masking path** replaces detected sensitive text before external
completion dispatch. The token-free DLP audit event is distinct from encrypted
request traces, conversation storage and original uploaded files. Do not infer
that the platform stores no raw user data. Full details and exceptions are in
[docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md) and section 5 below.

---

## 3. Services

Started by `docker-compose.yml` (+ overlays). Ports shown are **host** ports.

| Service | Dir | Host port | Role |
|---|---|---|---|
| **gateway** (Go) | `services/gateway` | **8080** | Public entrypoint; reverse-proxies to backend |
| **backend** (FastAPI) | `backend/` | 8081 | DLP, routing, dispatch, auth, logging — authoritative |
| **chat-edge-api** (Go) | `services/chat-edge-api` | 4000 | Conversations, memory, KB/RAG, files, web search, media tools, code interpreter. `profiles: [desktop]` |
| **mcp-gateway** (Node.js/Express) | `services/mcp-gateway` | 3200 | Per-user JWT-verified MCP servers + OAuth connectors. `profiles: [desktop]` |
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
| **Desktop** (Electron/TypeScript) | `apps/desktop/` | — | Hosted Web Chat UI; host agent loop to gateway; persistence/files through chat-edge; .NET tools sidecar |

`SranDesktop/` contains the legacy WPF UI and the still-used .NET ToolsSidecar /
BrowserHost. Caddy terminates browser-facing HTTPS; see [TLS topology](docs/ops/tls.md).
Agent Market additionally uses `agent-runner`, `doc-converter`, `web-fetcher` and
the `agent_market` schema; it has a separate queued execution plane.

Sidecars default to their Python/in-process equivalents; each flips on by env
flag and fails open. See README §"Environment Variables" and `docker-compose.yml`
comments for the exact flags.

---

## 4. Feature surface (honest)

- **DLP masking gateway** — the core. Mask → dispatch → restore, per-tenant
  policy profiles, deterministic per-conversation tokens.
- **Chat + KB-RAG** — chat-edge conversations, memory, per-user knowledge bases
  with pgvector embeddings.
- **Desktop agentic app** — `apps/desktop/`, Electron hosting the shared web UI.
  TypeScript owns the turn loop, consent and host tools; .NET handles local MCP
  tools. See [desktop architecture](docs/DESKTOP_APP_ARCHITECTURE.md).
- **Office/local files** — server plan generators and Python; Desktop transfer
  tools join granted folders to conversation storage. Existing PPTX edits,
  DOCX style preservation and reusable sandbox file IDs are described in
  [Office workflows](docs/OFFICE_WORKFLOWS.md).
- **Code interpreter / sandbox** — model-authored Python (`run_python`), the
  plan-exec workspaces (Excel/Doc/Pptx), file text extraction, inspectors,
  validators, version compare and vision image resize. The binary default is
  still off (`SANDBOX_ENABLED` defaults to false in `internal/config`), but
  **the webchat stack now ships it ON** (`docker-compose.webchat.yml`) because
  every one of those paths **fails closed** — with no sandbox they return
  `Err*SandboxUnavailable` instead of running model-influenced python inside
  the secret-bearing process, so "off" means the document features are off,
  not that they degrade. `make webchat-up` builds the required per-run image
  first; chat-edge fail-LOUDs (exit 1) if enabled-but-unavailable. The
  base/desktop stack keeps the opt-in overlay. See §5 for the trust boundary.
- **User sign-in (Google SSO)** — Google-only, domain-allowlisted
  (`ALLOWED_EMAIL_DOMAINS`), enforced server-side in
  `oauth_service.login_or_create`; portal uses NextAuth + backend id_token
  re-verification, desktop uses server-side PKCE with a nonce-bound one-time
  ticket over `sran://`. Email+password is disabled by
  `PASSWORD_LOGIN_ENABLED=false` (code retained). See README **User Sign-in**.
- **OAuth connectors** — per-user GitHub / Slack / Google, tokens stored
  AES-256-GCM in `connector_tokens` (migration 063), brokered to mcp-gateway.
  Separate OAuth apps from the sign-in client above (identity vs data scopes).
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

There are **five registered raw-egress channels** and an internal code-execution
boundary. They are documented and covered by dedicated checks,
detailed in [docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md); the summary:

1. **Intentional raw egress.** `RAW_EGRESS_ALLOWED` names `web_search`,
   `media_gen`, `embeddings`, `voice` and `agent_documents`. Search/media/
   embeddings/TTS use text scanning and tenant egress policy; STT audio cannot
   be scanned as text before transcription. Agent documents use the trusted
   caller, masking and egress decision in `dlp/in_house.py`, with their own
   audit marker. Image pixels are not redacted by the text DLP detector.

2. **Code interpreter sandbox — internal trust boundary (Phase 13), not egress.**
   The interpreter processes *raw* user data, but nothing it touches can reach a
   third party from inside. Each run is an ephemeral `docker run --rm` of the
   `ai-gateway-sandbox` image, launched by trusted Go with a fixed lockdown argv
   (no model input in the argv): `--network none` **plus** a default-deny seccomp
   profile with every networking syscall removed, `--cap-drop ALL`,
   `--user 65534`, `no-new-privileges`, read-only rootfs, no inherited secrets.
   Returned text re-enters the gateway policy pipeline before model dispatch;
   policy exceptions still apply and generated image pixels are not text-masked.
   The no-network boundary is pinned by a Go test (`TestSandbox_DockerArgsAlwaysNoNetwork`)
   and the Python egress invariant test (which asserts the interpreter is
   *absent* from the raw-egress whitelist). **On by default on the webchat
   stack** (see §4); off elsewhere unless the overlay is applied. The cost of
   that default is the `/var/run/docker.sock` bind-mount into chat-edge —
   host-root-equivalent, acceptable only because the `docker run` argv is
   built entirely by trusted Go from server constants with no model input.
   The hardening path is a broker service that owns the socket and exposes
   only `execute(job)`.

---

## 6. Data & migrations

- **Backend schema** — Alembic under `backend/migrations/versions/`; latest is
  **`090_media_readiness.py`**. The backend applies migrations on
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
| **web-chat dev** | `make webchat-up` | Adds web-chat `:3002` + chat-edge on its own pg/redis, **with the code-interpreter sandbox enabled** (builds `ai-gateway-sandbox:latest` first) |
| **prod-desktop** | `make prod-desktop` | base + `docker-compose.prod.yml` + `--profile desktop` → chat-edge + mcp-gateway + nginx (`:80/:443`), all bound to 127.0.0.1 behind nginx |
| **sandbox overlay** | `+ docker-compose.sandbox.yml` on base/desktop | Enables per-run sandbox containers; the standalone webchat stack already includes this wiring. Build the sandbox image and read the overlay header first |

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
| Provider adapters (12 registered) | `backend/app/services/provider_dispatcher.py` and `providers/` |
| Admin / portal / gateway routes | `backend/app/api/routes/` |
| Sandbox launcher (Go) | `services/chat-edge-api/internal/sandbox/` |
| Config (every env var) | `backend/app/core/config.py` |
</content>
