# SRAN AI Gateway

A multi-tenant AI platform whose core is a **DLP masking gateway**: PDPA-aware
scanning masks sensitive values before any request reaches a third-party LLM and
restores them in the response. Around that core it adds an agentic desktop app,
a code interpreter, OAuth connectors, per-user MCP workspaces, chat + KB-RAG,
web search, and image/video/audio generation.

The public entry point is the **Go API gateway on `:8080`**, which reverse-proxies
to the FastAPI backend behind it. The backend stays authoritative for DLP,
routing, dispatch, auth, and logging.

> **Read [ARCHITECTURE.md](./ARCHITECTURE.md) for the full picture** — services,
> the request path, and the security boundaries. This README is quick-start +
> pointers. The deep DLP design lives in
> [docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md).

---

## Request path at a glance

```
HTTP client ──► Go gateway :8080 ──► FastAPI backend (internal :8080 / host :8081)
 (SDK, curl,     PUBLIC ENTRY        DLP mask ─► route ─► provider dispatch ─► restore
  web-chat,      auth · proxy        (OpenAI / Anthropic / 9 others, or mock :9090)
  desktop)
```

The LLM never sees raw sensitive values — only neutral mask tokens. The reply is
restored to the caller with the originals in place. See
[ARCHITECTURE.md §2](./ARCHITECTURE.md) for the full pipeline and
[docs/DLP_ARCHITECTURE.md](./docs/DLP_ARCHITECTURE.md) for the masking invariant,
the two intentional raw-egress channels (web search + media), and the code-
interpreter sandbox trust boundary.

---

## Services & clients (short list)

**Services** (full table with ports and flags in
[ARCHITECTURE.md §3](./ARCHITECTURE.md)):

- **gateway** (Go, `:8080`) — public entrypoint / reverse proxy
- **backend** (FastAPI, host `:8081`) — DLP, routing, dispatch, auth, logging
- **chat-edge-api** (Go, `:4000`) — chat, memory, KB/RAG, files, search, media,
  code interpreter *(desktop profile)*
- **mcp-gateway** (Go, `:3200`) — per-user MCP servers + OAuth connectors
  *(desktop profile)*
- **ner-service** (`:8001`) — spaCy/Presidio NER for DLP
- **dlp-sidecar** (Rust), **rate-limiter** (Rust), **log-worker** (Go) —
  gRPC sidecars, each **off by default**, flip on by env flag, fail open to the
  Python/in-process path
- **postgres** (pgvector) + **redis 7** (auth'd) — internal only
- **prometheus** `:9091` / **grafana** `:3100` — observability
- **mock-provider** `:9090` — fake LLM for tests

**Frontends / clients** — three Next.js apps: **admin-ui** `:3000`,
**portal** `:3001`, **web-chat** `:3002` (`apps/web-chat/`); plus **SranDesktop**,
a .NET 8 WPF desktop client (`SranDesktop/`).

---

## Quick Start

**Prereqs**: Docker Desktop 4.27+ (Compose v2.24+).

```bash
git clone https://github.com/BeatChalongrat/ai-gateway.git
cd ai-gateway
make bootstrap      # happy path: gen .env + build + start + migrate + seed
```

`make bootstrap` generates `.env` with strong secrets (or **backfills** any
missing hard-required var — e.g. `REDIS_PASSWORD` — into an existing `.env`,
never overwriting your values), brings up the dev stack, applies migrations, and
seeds the admin and portal users. `make help` lists every target. Raw steps:
`make up` (start) then `make seed` (create users).

> **`REDIS_PASSWORD` is MANDATORY** — redis runs `--requirepass` and its
> consumers fold it into the `redis://` URL, so the stack refuses to start
> without it (compose `${REDIS_PASSWORD:?}`). You don't set it by hand:
> `make bootstrap` generates/backfills it, and `make up` runs a **preflight**
> that fails loud with a fix hint (rather than a cryptic compose error) if it's
> ever missing. To set one manually: `openssl rand -hex 32`.

> **`make bootstrap`, `make sandbox-up`, and an expanded `make help`** are landing
> in a parallel change. If they aren't in your `Makefile` yet, use `make up` +
> `make seed` and the sandbox instructions in `docker-compose.sandbox.yml`.

| URL | What | Default credentials |
|---|---|---|
| http://localhost:8080/health | Gateway health (public entry) | — |
| http://localhost:8081/docs | Backend OpenAPI (debug direct) | — |
| http://localhost:3000 | Admin UI | `admin` / `admin1234` |
| http://localhost:3001 | Customer portal | `somchai@acme.ai.gateway` / `P@ssword` |
| http://localhost:3002 | Web chat | portal credentials (webchat overlay) |
| http://localhost:8001/health | NER service | — |
| http://localhost:9090/health | Mock LLM provider | — |
| http://localhost:9091 · :3100 | Prometheus · Grafana | — · `admin`/`admin` |

### Deployment shapes

| Shape | Command | Notes |
|---|---|---|
| **dev** | `make up` | Core stack. chat-edge / mcp-gateway / nginx are on the `desktop` profile and don't start here |
| **web-chat dev** | `make webchat-up` | Adds web-chat `:3002` + chat-edge. Code-interpreter sandbox is **ON by default** here — it also builds the required `ai-gateway-sandbox:latest` image first |
| **prod-desktop** | `make prod-desktop` | base + prod + `--profile desktop` (chat-edge + mcp + nginx `:80/:443`), bound to 127.0.0.1 |
| **sandbox** | `+ docker-compose.sandbox.yml` (or `make sandbox-up`) | Enables the code interpreter on the **base/desktop** stack; the webchat stack already has it inline. Read that file's header first — needs a built sandbox image + docker socket |

> **Why the webchat stack enables the sandbox by default.** chat-edge fails
> *closed* on every python path it shells out to: PDF/DOCX/XLSX text
> extraction, the Excel/Doc/Pptx plan executors, inspectors, validators,
> version compare, large-image vision resize and `run_python`. Without a
> sandbox those return `Err*SandboxUnavailable` rather than running
> model-influenced python in the secret-bearing process — so "sandbox off" is
> not a degraded mode, it is *document features off*. Opt out with
> `SANDBOX_ENABLED=0` knowing that. Note the stack bind-mounts
> `/var/run/docker.sock` into chat-edge (host-root-equivalent); the
> justification and the hardening path are in the header of
> `docker-compose.webchat.yml`.

**Public URLs are baked in at build time.** `NEXT_PUBLIC_*` values are inlined
by the bundler, so setting them only as container env has no effect on the
browser bundle. Two vars matter for a non-localhost deploy — set them in
`.env` and **rebuild** the images (`make build`), not just restart:

| Var | Default | Baked into |
|---|---|---|
| `WEB_CHAT_API_URL` | `http://localhost:4000/api/v1` | web-chat → chat-edge API base |
| `WEB_CHAT_PUBLIC_URL` | `http://localhost:3002/chat` | portal's "Open SRAN Chat" link + the three redirect allowlists (`middleware.ts`, login, oauth-complete). Required (`:?`) in `docker-compose.prod.yml` |

### Migrations

- **Backend** (Alembic, `backend/migrations/versions/`, latest **064**) applies on
  backend startup — nothing to run by hand.
- **Chat-edge** SQL migrations apply via the Postgres init hook on a *fresh*
  volume only. On an existing volume run `make chat-edge-migrate-main` (main
  stack) or `make chat-edge-migrate` (webchat stack). `make bootstrap` handles
  this for you.

For a clean UAT box see `scripts/uat/README.md`.

---

## API Usage

Create a project-scoped API key in **Admin UI → API Keys** (keys start with
`gw_`), then call the OpenAI-compatible endpoint **through the gateway on `:8080`**:

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

DLP runs transparently — the LLM sees a mask token, not `somchai@test.com`, and
the reply is restored to you. The response carries `policy_action`
(`allow` / `mask` / `block`) and `policy_reasons`.

**Multi-turn restore**: add `"metadata": {"conversation_id": "thread-42"}` so
tokens are HMAC-derived (`DLP_HMAC_KEY`) and the same value resolves across turns
via the Redis conversation vault.

**Models**: `GET /v1/models` returns the DB-backed registry (`model_configs`),
the same list the admin UI's **Models** page edits.

---

## Admin UI (`:3000`)

Cookie session auth, CSRF-protected, rate-limited login (routes in
`backend/app/api/routes/admin*.py`). Pages: Dashboard, Requests (full audit log),
Secure Trace (per-request 4-stage), DLP (detector toggles + policy editor +
round-trip preview), Routing, Models (+ discovery), Providers, Alerts, Token
Packs, Portal Users, Settings.

---

## Testing

```bash
make ci             # full local gate (mirrors .github/workflows/ci.yml)
make test-unit      # backend unit lane (the CI gate)
make test-all       # full backend suite incl. integration (needs live stack)
make go-test        # chat-edge-api Go vet + tests
make web-test       # vitest for the three web apps
```

Test layout under `backend/tests/` (per-detector, pipeline round-trip, regression
anchors, integration) plus Go tests under `services/*`. **For current
pass/fail counts and detector totals, see CI — the GitHub Actions
[`ci.yml`](./.github/workflows/ci.yml) and GitLab [`.gitlab-ci.yml`](./.gitlab-ci.yml)
pipelines are the live source of truth** (hard-coded numbers here rot). The DLP
detector registry itself is `backend/app/services/dlp/detectors/registry.py`.

---

## Environment Variables

Required in production (dev fallbacks live in `docker-compose.yml`; **`REDIS_PASSWORD`
is required in every mode**):

| Var | Purpose |
|---|---|
| `REDIS_PASSWORD` | Redis AUTH — **mandatory**, stack won't start without it |
| `JWT_SECRET` / `PORTAL_JWT_SECRET` | Backend / portal JWT signing |
| `MASTER_ENCRYPTION_KEY` | AES-256-GCM key for `input_raw_encrypted` |
| `DLP_HMAC_KEY` | Deterministic per-conversation mask-token derivation |
| `INTERNAL_SERVICE_SECRET` | Trust anchor for chat-edge → gateway `X-User-ID` |
| `NEXTAUTH_SECRET` | NextAuth on the portal |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, … | Provider auth (or set via admin **Providers**) |

Selected optional flags (full list in `backend/app/core/config.py` and
`.env.example`):

| Var | Default | Effect |
|---|---|---|
| `DLP_ENGINE` | `python` | `shadow` runs Rust+Python in parallel; `rust` cuts over |
| `RATE_LIMITER_ENGINE` | `python` | `rust` enables the gRPC rate-limit sidecar |
| `LOG_WORKER_ENGINE` | `python` | `go` enables the gRPC log sidecar |
| `SANDBOX_ENABLED` | `false` | Enables the code interpreter (see sandbox overlay) |
| `VIDEO_GEN_ENABLED` | `false` | Surfaces video-gen models + routes |
| `NER_SERVICE_URL` | `http://ner-service:8001` | NER sidecar location (fails open) |
| `NEXT_PUBLIC_OAUTH_ENABLED` | `false` | Portal OAuth connectors (GitHub/Slack/Google) |

---

## Repo Layout

```
ai-gateway/
├── services/
│   ├── gateway/            # Go public API gateway (:8080)
│   ├── chat-edge-api/      # Go chat/RAG/files/search/media/code-interpreter
│   └── mcp-gateway/        # Go per-user MCP + OAuth connectors
├── backend/                # FastAPI backend (DLP, routing, dispatch, logging)
│   ├── app/services/dlp/   # detectors/, classifier, transformer, egress_policy
│   ├── app/services/policy/
│   ├── app/services/providers/   # 11 adapters
│   ├── migrations/versions/      # alembic, latest 064
│   └── ner_service/        # spaCy/Presidio NER (own image)
├── admin-ui/               # Next.js admin (:3000)
├── portal/                 # Next.js customer portal (:3001)
├── apps/web-chat/          # Next.js web chat (:3002)
├── SranDesktop/            # .NET 8 WPF desktop client
├── dlp-sidecar/            # Rust DLP gRPC sidecar
├── rate-limiter/           # Rust rate-limit gRPC sidecar
├── log-worker/             # Go log-writer gRPC sidecar
├── mock/                   # Mock LLM provider for tests
├── infra/                  # prometheus / grafana / nginx / postgres / k8s
├── scripts/                # seed, UAT, migration helpers
├── docs/DLP_ARCHITECTURE.md      # deep DLP design (source of truth)
├── ARCHITECTURE.md         # system map (services, request flow, boundaries)
└── docker-compose*.yml     # base + prod + webchat + sandbox overlays
```

---

## License & Contributing

See repo top-level for license. PRs welcome against `main` — run `make ci`
before pushing (see [CONTRIBUTING.md](./CONTRIBUTING.md)). The
[CHANGELOG.md](./CHANGELOG.md) tracks releases; [RUNBOOK.md](./RUNBOOK.md) and
[IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) carry operational and
roadmap detail.
</content>
