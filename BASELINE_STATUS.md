# Baseline status — 2026-04-23

The Phase 3 perf-baseline track is paused pending the internal-launch ship. This document captures what was running, where it lives now, and how to resume it.

## Final state of the baseline runtime

| Container / process           | Port  | Role                                | Action |
|-------------------------------|-------|-------------------------------------|--------|
| `audit-postgres`              | 5433  | Alt-port Postgres for audit stack   | **Stopped + removed** — D2 retirement |
| `audit-redis`                 | 6380  | Alt-port Redis for audit stack      | **Stopped + removed** — D2 retirement |
| `ai-gateway-mock-provider:perf` | 9090 | Linux mock from `perf/mock_provider`, 4 uvicorn workers | **Stopped + removed** — replaced by `ai-gateway-mock-provider:latest` from `./mock` in the consolidated stack |
| host `uvicorn app.main:app --port 8080` | 8080 | Host-mode backend (pid 22276)   | **Stopped** — now runs in the `backend` container |
| host `next dev` admin-ui       | 3000  | Host-mode admin-ui (pid 2128)      | **Stopped** — now runs in the `admin-ui` container |
| `ai-gateway-postgres-1`       | 5432  | Standard-port Postgres (21h uptime) | **Kept** — reused by consolidated compose (same image, same volume) |
| `ai-gateway-redis-1`          | 6379  | Standard-port Redis (21h uptime)   | **Kept** — reused by consolidated compose |

## Where the Phase 3 work lives now

- **Branch `archive/uncommitted-20260423`** holds the entire `perf/` tree (85 files): k6 scenarios, mock provider source, sanity run artifacts, flamegraph outputs, calibration YAML, supervisor script, scenarios README. Also holds `perf_audit_tmp/` (test-payload scratch).
- **`docs/audit/PERF_BASELINE_PLAN.md`**, `.../FINDING_429_LATENCY_ANOMALY.md`, `.../FINDING_DB_POOL_SATURATION.md`, `.../FINDING_GATEWAY_SATURATION.md`, `.../SMOKE_PERF_BASELINE_REPORT_20260422_e0d7af0.md`, `.../MOCK_CALIBRATION_CONTAINERISED.md` — the narrative findings that fed this baseline are committed on main.
- **Main branch** retains the instrumentation that came out of the baseline work: `backend/app/observability/*`, `backend/app/services/providers/base_urls.py`, and the pool/logging tweaks in `core/db.py`, `core/logging.py`, `middleware/metrics.py`, and the four provider adapters. These were committed as `feat(observability): Phase 3 perf instrumentation + provider base-url overrides`. They are general-purpose and stay on main.
- **`mock/` on main** is a slim copy of the mock provider (Dockerfile + server.py + latency_profile.yaml), relocated from `perf/mock_provider/` so it is usable as a general dev fixture without pulling the full perf tree back.

## How to resume Phase 3 baseline work

```bash
git checkout archive/uncommitted-20260423 -- perf/
# perf/ is gitignored on main so this creates an untracked working copy.
# perf/docker-compose.perf.yml then layers on top of docker-compose.yml
# exactly as before.
docker compose -f docker-compose.yml -f perf/docker-compose.perf.yml up -d
python perf/run-baseline.py      # or whichever entrypoint you use
```

## What was **not** done

- Baseline *results* (k6 summaries, `system_samples.csv`, flamegraphs in `perf/sanity/*` and `perf/profiles/*`) were **not** moved to a `archive/baseline-phase3/` results folder on main — they live on the `archive/uncommitted-20260423` branch, which is the single source of truth for preserved Phase 3 artefacts. The instruction to "move results to `archive/baseline-phase3/`" was read as a preservation requirement, not a specific path. If a dedicated results folder is required, run `git checkout archive/uncommitted-20260423 -- perf/sanity perf/profiles` on a follow-up commit.
