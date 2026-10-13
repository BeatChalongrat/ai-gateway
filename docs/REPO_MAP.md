# Repository map

What is tracked is what a person who cloned this repository needs to build, run, deploy, operate or
understand the system. Working notes, per-round probes, result folders and reports stay on the
author's machine: `.gitignore` names those kinds, and `scripts/gate/tracks.py` (the gate's `tracks`
lane, the `drill` phase, CI) fails by name if one is tracked anyway. Nothing is untracked while
something still points at it: the same check fails on any tracked line that names a `docs/` or
`scripts/` file that is not tracked, so a record that stays outside the repository is described in
words where it is cited, never by a path a clone cannot follow.

## Root

- `backend/` — the gateway API (FastAPI): routing, providers, DLP, usage, portal, jobs; `agent_market/`
  is the agents' engine and packages; `evals/` the detection-quality harnesses; `migrations/` Alembic.
- `services/` — the Go and Node services: `chat-edge-api` (the chat control plane and the agent
  runner), `gateway` (the Go front of the model path), `mcp-gateway`, `web-fetcher`, `doc-converter`,
  `agent-sandbox`, `kb-models`.
- `apps/web-chat/` — the chat app (Next.js); `apps/desktop/` — the Electron desktop app that hosts it.
- `admin-ui/` — the admin console (Next.js); `portal/` — the user portal (Next.js).
- `SranDesktop/` — the earlier .NET 8 WPF desktop client (not shipped; CI no longer builds it).
- `dlp-sidecar/`, `rate-limiter/` (Rust), `log-worker/` (Go) — sidecars of the gateway.
- `infra/` — Caddy, Postgres, pgbouncer, Prometheus, Grafana, backup, nginx, k8s, Terraform.
- `scripts/` — see below. `tools/loadtest/` — the load-test kit. `mock/` — the mock provider.
- `examples/` — sample requests and policies. `tests/fixtures/` — a shared corpus. `releases/` — the
  desktop update manifest (`latest.json`, live).
- `docker-compose*.yml` — the stacks: live (`docker-compose.yml` + `webchat.yml` + `agent-market.yml`),
  dev, prod, sandbox, test stack. `Makefile`, `.env.example`, `.gitlab-ci.yml`, `.github/workflows/ci.yml`.
- `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `LICENSE`.

## docs/

- Architecture and design: `ARCHITECTURE_OVERVIEW.md`, `SYSTEM_DESIGN.md`, `API_DESIGN.md`,
  `PUBLIC_API.md`, `ADMIN_API.md`, `ROUTING_ARCHITECTURE.md`, `ROUTING_STRATEGY.md`,
  `PROVIDER_ARCHITECTURE.md`, `DLP_ARCHITECTURE.md`, `DATABASE_SCHEMA.md`, `STREAM_CONTRACT.md`,
  `DESKTOP_APP_ARCHITECTURE.md`, `api-media-generation.md`, `contracts/`.
- Security and data: `SECURITY_MODEL.md`, `ENTERPRISE_CONTROLS.md`, `SCALE_COMPLIANCE.md`,
  `DATA_RETENTION_PLAN.md`, `ops/user-data.md`, `ops/README-secrets.md`.
- Running it: `ops/` (deploy-backend, backups, monitoring, tls, scaling, knowledge-base),
  `SCALING_ACTIVATION_RUNBOOK.md`, `DEPLOY_SANDBOX.md`, `DLP_SIDECAR_FAILURE.md`, `HOST_MEMORY.md`,
  `SSO-GO-LIVE.md`, `CONNECTOR_SETUP.md`, `CONNECTOR_OAUTH.md`, `CI_RUNNER_SETUP.md`,
  `testing/test-stack.md`.
- Agent Market: `agent-market/ARCHITECTURE.md` (how it is built, by card), `OPERATIONS.md` (how it is
  run), `REMOVAL.md` (how it is taken out), `GLOSSARY.md` (its words, Thai and English).
- What the system does not do yet: `known-issues/`, `DEFERRED_WORK.md`, `OPEN_DECISIONS.md`;
  the design behind the admin auth: `AUTH_REDESIGN_DESIGN.md`; the quarantined tests:
  `backend/tests/QUARANTINE.md`.

## scripts/

- `gate.sh` — the gate, one heavy thing at a time (`preflight`, `lanes`, `it`, `build`, `stack`,
  `smoke`, `drill`, `backup`). `gate/` holds its pieces: `tracks.py` (nothing tracked that `.gitignore`
  excludes), `marked.py` + `backend_marked_failing.txt` (the backend tests marked `integration`, held
  by name), `go_test.sh` (the Go lane against the test database), `it/` (the integration lane in
  chunks: `am10.sh` … `am18.sh`, and `chunk.sh` to run one chunk alone).
- `smoke.sh` + `smoke/` — the permanent smoke suite, every entry point; `smoke-daily.ps1` registers it.
- `ops/` — the host's own tools: `power.sh` (nothing heavy on battery), `envsafe.sh` (settings without
  printing them), `live_memory.py`, `swap.sh` (one live service to its new image, measured),
  `backup_drill_check.sh`, `offsite_e2e.sh`, `log_secret_scan.py`, `rotate_dlp_audit_key.py`,
  `create_walk_account.py`.
- `agent-market/` — the agents' build and test tooling: `build*.sh` (runner, converter, fetcher
  images), `python-lane.sh` (the engine's tests in the sandbox image), `generic-check.sh`,
  `test_stack.sh` / `test_stack_web.sh` / `test-web.yml` / `test-stack.search.yml` / `seed_admins.py`
  (the test stack with the agents on it, and its stand-in web), `test_clients.sh` (the two web clients
  as production builds), `removal_drill.sh` (the code can be taken out: 0 lines of diff),
  `openrouter_pin.py` / `recipients_probe.py` (who really answers a request), `words/` (the tools that
  write the Go tables of what each package hands a thread, in both languages).
- `tests/` — the host's own tests (`python -m unittest discover -s scripts/tests`).
- `backend-test-lane.sh`, `test-stack.sh`, `test_stack_gen.py`, `test_stack_seed.py` — the backend lane
  and the core test stack; `deploy-backend.sh`, `build-images.sh`, `backup-db.sh`, `migrate.*`,
  `seed.*`, `start.*`, `dev.ps1`, `preflight.sh`, `wait_for_health.sh`; `uat/` — the UAT kit.
- `probes/` — **not tracked.** Each card's probes, walks and results stay on the machine that ran them.

## backend/app

- `api/` public and admin routes · `core/` config, logging, security · `domain/` entities ·
  `schemas/` pydantic models · `services/` orchestration, providers, routing, DLP, usage, portal ·
  `repositories/` data access · `jobs/`, `workers/` background work · `middleware/` auth, tracing,
  request context · `ops/` the watcher.

## admin-ui

- `app/` route-based pages · `components/` shared pieces (`ui/Icon.tsx` is the one icon set) ·
  `lib/` API client, auth, i18n.
