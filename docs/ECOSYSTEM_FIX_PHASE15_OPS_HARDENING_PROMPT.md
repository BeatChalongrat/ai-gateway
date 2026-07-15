# ECOSYSTEM FIX — PHASE 15: Ops hardening (deploy, CI, docs, workspace migration)

Make the system easy to stand up, keep CI honest across every stack, and bring the docs back to reality after 14 phases. Not feature work — reliability/onboarding. Do not regress Phases 1–14. Do NOT `git push` (leave publishing to the user).

Four independent PARTs (deploy / CI / README / workspace migration) — do/commit separately.

## Ground rules

- Read every file you touch IN FULL; do each PART's R0 first and answer it in the summary.
- Do NOT invent a single mega-compose that merges the dev/prod/webchat/sandbox files — they intentionally differ (webchat has its OWN postgres/redis; prod binds localhost; sandbox is an overlay). The goal is a clear, correct, documented path per deployment shape, not forced unification.
- Secrets: never hardcode or commit a real secret; generate/placeholder only. Keep `.env` git-ignored.
- Test what you can; a compose/CI change you can't fully execute here must be reasoned through (state that).

---

## PART A — One-command first-run + full sandbox enablement

**Verified pain (recon):** the closest thing to deploy is the `Makefile`; a fresh operator must manually: `cp .env.example .env` + set the now-mandatory `REDIS_PASSWORD` (compose uses `${REDIS_PASSWORD:?}` — refuses to start without it), pick the right invocation (`up` vs `prod-desktop` vs webchat — chat-edge-api/mcp-gateway are behind `--profile desktop`), run `make seed`, run `make chat-edge-migrate-main` (chat-edge does NOT auto-apply migrations on an existing volume — the Go binary only *verifies*), and do the sandbox entirely by hand. `make fresh` uses a bare `sleep 10` instead of health-gating.

R0: read `Makefile` fully, all `docker-compose*.yml`, `.env.example` (+ `backend/.env.example`, `infra/production/.env.production.template`), `backend/Dockerfile` (auto-alembic), `scripts/apply_chat_edge_migrations_main.sh`, `docker-compose.sandbox.yml`. Confirm the required-secret list and the profile story.

1. **`make bootstrap` (first-run, idempotent)** — one target that gets a dev stack correct from nothing:
   - If `.env` is absent, copy `.env.example` → `.env` and **generate** strong random values for the mandatory secrets (`REDIS_PASSWORD`, and any `${VAR:?}` the chosen shape needs) in place of the `changeme-*` placeholders — using `openssl rand`/similar; print which were generated so the operator can record them. Never overwrite an existing `.env`.
   - Bring up the stack, then **health-gate** (poll each critical service's `/health` or a `pg_isready`/`redis-cli ping`) instead of `sleep 10` — proceed only when ready, fail with a clear message on timeout.
   - Apply migrations deterministically: backend Alembic self-applies (confirm), and **run the chat-edge migration step as part of bootstrap** (safe/idempotent — `apply_chat_edge_migrations_main.sh` loops `IF NOT EXISTS`-style SQL) so an existing-volume operator isn't left with a fail-closed chat-edge.
   - Seed (admin + portal) if not already seeded (idempotent check, not blind re-seed).
   - Print a clear "stack is up at: gateway :8080, admin :3000, portal :3001, …" summary + what's NOT started (desktop profile, webchat, sandbox) and the command to add each.
2. **`make sandbox-up`** — replace the fully-manual sandbox steps with one target: build `ai-gateway-sandbox` image, ensure the chat-edge image has `docker-cli` + seccomp (R0: currently a manual Dockerfile edit — make it built-in or a documented one-liner), layer `docker-compose.sandbox.yml`, set `SANDBOX_ENABLED=1`, create the host bind dir. Health-check that `run_python` responds (not 503) after. Document the docker-socket privilege in the target's echo (it's host-root-equivalent — the operator should consciously opt in).
3. **Profile clarity** — a `make help`/target list (or README section, coordinate with PART C) that states the three shapes plainly: dev (`make up`), prod-desktop (`make prod-desktop`, adds chat-edge+mcp-gateway+nginx), webchat (separate stack + DB), and the sandbox overlay. No more guessing which command.
4. Keep existing targets working; don't break `up`/`prod`/`fresh`. Tests: bootstrap on a clean checkout reaches a healthy stack (or, if you can't run docker here, dry-run/lint the compose + Makefile and reason through it — state that).

## PART B — GitLab CI parity

**Verified:** `.gitlab-ci.yml` runs **3 of the 10** lanes in `.github/workflows/ci.yml`. Missing: web-chat vitest, portal+admin-ui vitest (`web-unit-test`), `gateway-test` (Go gateway), `mcp-gateway-test` (node), `desktop-test` (SranDesktop, **Windows**), `web-e2e` (Playwright), `secret-leak-guard`, `security-audit`, and the standalone DLP egress-invariant gate step. So the Phase 11–13 work isn't exercised on GitLab (which is now the primary remote).

R0: read both CI files FULLY; list the exact job delta; check for any `tags:` or runner config that reveals what GitLab runners exist.

1. **Port every Linux-runnable lane** to `.gitlab-ci.yml` (GitLab syntax: `stages`, `image`, `cache`, `rules`), mirroring the GitHub jobs' commands exactly (they're proven green): backend pytest **+ the explicit DLP egress-invariant gate step** (Phase 9 drift guard — it must run on the primary remote), web-chat vitest, portal+admin-ui vitest, gateway-test, mcp-gateway-test (node), secret-leak-guard, security-audit. Cache deps per ecosystem.
2. **Desktop (Windows) job** — `SranDesktop` needs a Windows runner (net8.0-windows/WPF can't build on Linux). GitLab runner availability is an **infra unknown** (no `tags:` in the repo). Add the job **tagged for a Windows runner** and gate it so a missing runner does NOT red the whole pipeline (e.g. `rules:` + `allow_failure: true`, or a manual/opt-in job), with a comment stating a Windows GitLab runner must be registered for it to run. Do NOT let desktop coverage silently vanish AND do NOT let its absence block everything — make the requirement explicit.
3. **Playwright** — port as a non-blocking job (`allow_failure`), matching GitHub's `continue-on-error`.
4. **No secrets in CI** — same as GitHub: test defaults/service-containers only; the chat-edge import e2e self-skips without a DB.
5. Validate the YAML (GitLab CI lint rules / careful review — a workflow that never triggers is worse than none). State how you validated.

## PART C — README + architecture docs currency

**Verified:** `README.md` (394 lines, tagged `v0.1.0-uat`) is materially stale — it predates the Go gateway strangler (still shows clients hitting FastAPI directly), omits the desktop app, code interpreter/sandbox, OAuth connectors, chat-edge, mcp-gateway, web-chat; the service list misses `services/gateway|chat-edge-api|mcp-gateway`, `SranDesktop/`, `apps/web-chat`; Alembic range says "001→041" (actual **064**); setup steps omit the mandatory `REDIS_PASSWORD`, the `--profile desktop`, chat-edge migration, and sandbox; test/detector counts are stale point-in-time numbers.

1. **Rewrite the README to describe the system as it actually is:**
   - **Architecture:** the Go **gateway on :8080 is the public entrypoint**; FastAPI backend behind it (:8081); the full service list (gateway, backend, chat-edge-api, mcp-gateway, dlp-sidecar, ner-service, rate-limiter, log-worker, prometheus/grafana), the **three** Next.js apps (admin-ui, portal, web-chat), and the **SranDesktop** WPF client. Update or replace the ASCII diagram so it shows the real request path (client → Go gateway → DLP pipeline → providers).
   - **Feature list:** DLP masking gateway (the core), desktop agentic app, code interpreter/sandbox, OAuth connectors (GitHub/Slack/Google), MCP per-user workspaces, chat/KB-RAG, web search, image/video/audio gen, DLP egress policy (per-tenant). Keep it honest — note what's off-by-default (sandbox).
   - **Setup:** point at `make bootstrap` (PART A) as the happy path; state `REDIS_PASSWORD` is mandatory, explain the three deployment shapes + sandbox overlay, and the migration story (backend auto, chat-edge via bootstrap/target).
   - **Fix the stale specifics:** Alembic latest (064), remove or clearly date the point-in-time "N passing / M failing" and detector counts (link to CI instead of hardcoding a number that rots).
2. **Architecture doc:** there is no `ARCHITECTURE.md` (README §1 is the only overview). R0: decide — either extract a proper `ARCHITECTURE.md` (services, request flow, the DLP masking invariant + egress policy, the trust boundaries incl. the sandbox) and keep README as quick-start + pointers, OR keep it in README. Recommend the split for a system this size; either way the DLP masking invariant and the intentional-raw-egress (search/media) disclosure should be documented in one findable place (coordinate with the Phase 9 `docs/DLP_ARCHITECTURE.md`).
3. Don't touch the many historical phase-prompt docs under `docs/` (they're a record); just make the TOP-LEVEL entry docs (README, and ARCHITECTURE if created) accurate. Note in the summary which overlapping docs (`RUNBOOK.md`, `IMPLEMENTATION_ROADMAP.md`, etc.) are now partially stale, as a tracked cleanup — don't rewrite them all this phase.

## PART D — `/data/workspace` legacy migration (carried-over)

**Verified:** before Phase 10 C1, the built-in MCP filesystem/memory servers were a single **shared, unauthenticated** child rooted at `/data/workspace`; C1 moved to per-user roots `/data/workspace/{sanitizeSubDir(sub)}/files` + `/memory.json` (`services/mcp-gateway/lib.js:19,172,183`). Legacy files sit directly under the volume root with **no ownership metadata**, so they can't be safely auto-attributed to a user (moving one user's file into another's root would be a data leak).

R0: read `services/mcp-gateway/lib.js` (`WORKSPACE_BASE`, `sanitizeSubDir`, `userChildConfig`) and confirm there's no ownership record for pre-C1 files.

Because auto-attribution is unsafe, deliver the SAFE version:
1. A migration/inventory script (`services/mcp-gateway/scripts/migrate_legacy_workspace.*` or a documented `make` target) that: lists any files/dirs directly under `/data/workspace` that are NOT a valid per-user root (i.e. legacy shared content), and **moves them into a quarantine dir** (e.g. `/data/workspace/_legacy_pre_c1/`) rather than deleting or guessing an owner — so nothing is lost and nothing leaks into a user root. Idempotent; dry-run by default with a `--apply` flag.
2. Document (in RUNBOOK or the migration script header): an admin who KNOWS a legacy file's owner can place it under the correct `{sub}` dir manually (using the `sanitizeSubDir` mapping); otherwise it stays quarantined. State plainly that pre-C1 shared files were in an insecure location and are not auto-restored into user roots by design.
3. Test the inventory logic (given a fake workspace tree with a mix of valid per-user roots and legacy top-level files, it quarantines only the legacy ones, leaves valid roots untouched, and is idempotent).

---

## Final verification checklist

1. Per PART: what you ran/validated, green. (Compose/CI you can't execute here → dry-run/lint + reasoned walk, stated as such.)
2. **Operator walk (written):** the exact commands a fresh operator now runs to get (a) a dev stack, (b) prod-desktop, (c) sandbox enabled — confirm each is now one or two documented commands, not the manual multi-step from the recon.
3. **CI parity statement:** GitLab now runs the same lanes as GitHub except the Windows desktop job (gated on a Windows runner) — list any remaining gap and why.
4. **Docs accuracy:** the README now matches the real service list + entrypoint + setup; the DLP masking invariant + intentional-raw-egress are documented in one findable place.
5. Summarize changes file by file per PART. Note tracked follow-ups (stale `RUNBOOK.md`/roadmap docs; Windows GitLab runner registration; the manual owner-attribution of quarantined legacy files). List Phase 16 (deferred features: pptx/pdf local gen, sub-agent progress UI, client-side Pyodide, portal CSRF) WITHOUT implementing.
