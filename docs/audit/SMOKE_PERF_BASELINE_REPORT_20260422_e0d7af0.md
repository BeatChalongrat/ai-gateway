
# AI Gateway — Performance Baseline Report

> **SMOKE-SCALE VALIDATION RUN — full-duration pending.**
> Durations were cut 10× from the spec. Numbers are directionally correct
> but must NOT be used for Go-migration decisions. Re-run with `--full`
> for the binding baseline.

- Run id: `20260422-153956_e0d7af0`
- Generated: `2026-04-22T08:40:41.007781Z`
- Git SHA: `e0d7af0689c31633dc28baf45710b02bffd93998` (dirty)
- Branch: `main`
- OS: `Windows-10-10.0.26200-SP0` (Windows)
- Python: `3.11.8`
- CPU cores (logical / physical): 12 / 6
- RAM: 31.2 GB
- Docker: `Docker version 29.4.0, build 9d7ad9f` — images: postgres: postgres:16 sha256:5a65324fe84dc41709ff914e90b07f3e2f577073ed27bf917d4873aca0c9ec51 ; redis: redis:7 sha256:24ea35ee039d7345bef2377611e3b1a615231746de4cfea74c1414575ee56167
- spaCy state: `spacy installed, no en_core_web_sm`
- sentence-transformers: `not installed`
- Presidio: `2.2.354`
- Run scale: **smoke-scale** (smoke_factor=0.1)

## Environment config (redacted)

* `APP_ENV=`
* `APP_NAME=`
* `APP_PORT=`
* `LOG_LEVEL=`
* `POSTGRES_HOST=`
* `POSTGRES_PORT=`
* `POSTGRES_DB=`
* `POSTGRES_USER=`
* `REDIS_HOST=`
* `REDIS_PORT=`
* `DLP_ENABLED=`
* `DLP_DEFAULT_PROFILE=`
* `DLP_SCAN_RESPONSES=`
* `SEMANTIC_ROUTER_ENABLED=`
* `PROVIDER_TIMEOUT_CONNECT=`
* `PROVIDER_TIMEOUT_READ=`
* `GATEWAY_URL=`
* `GATEWAY_MODEL=`
* `RPS=`
* `PEAK_RPS=`
* `SPIKE_RPS=`


## Scenario summary

| Variant | Round | Scenario | Runs | RPS (mean) | p50 (ms) | p95 (ms) | p99 (ms) | Errors % | CV % |
|---------|-------|----------|------|-----------|---------|---------|---------|---------|------|
| A | A | smoke | 1 | 0.2 | 12413 | 17453 | 17453 | 0.00 | 0.0 |


## Per-phase latency breakdown

Each sub-section below is one (variant, round, scenario) combination.
Phase label names come from the directive's allowed-fixes list and feed
directly from the `request_phases` structured log line emitted by the
Prometheus + phase-timer middleware.


### A / A / smoke

Runs aggregated: 1. Sample size (requests): 0.

| Request phase         | p50    | p95    | p99    | % of total p95 |
|-----------------------|--------|--------|--------|----------------|
| auth                  | N/A | N/A | N/A | N/A |
| rate_limit (12x Redis)| N/A | N/A | N/A | N/A |
| routing               | N/A | N/A | N/A | N/A |
| dlp_regex             | N/A | N/A | N/A | N/A |
| dlp_ner (spaCy)       | N/A | N/A | N/A | N/A |
| guardrails            | N/A | N/A | N/A | N/A |
| cache_check           | N/A | N/A | N/A | N/A |
| provider_call         | N/A | N/A | N/A | N/A |
| response_assembly     | N/A | N/A | N/A | N/A |
| TOTAL                 | N/A | N/A | N/A | 100% |

Notes: phase log is best-effort — set GATEWAY_LOG_PATH env to the JSON log file


## System sampler extracts

| Scenario | Peak CPU % (proc) | Peak RSS (MB) | Peak threads | Peak FDs/handles |
|----------|------------------|---------------|--------------|------------------|
| smoke (A/A) | 0.0 | 8 | 1 | 128 |


## Mock-provider calibration

| Target percentile | Target (ms) | Measured (ms) | Delta |
|-------------------|-------------|---------------|-------|
| p50 | 800 | 797 | -3ms |
| p95 | 3000 | 4000 | +1000ms |
| p99 | 8000 | 7048 | -952ms |

## Proposed post-optimization targets (derived, not SLOs)

Per the user directive, SLOs aren't agreed yet. The values below are
half of the measured p95 per scenario — a reasonable ambition for the
Go / cache / pool optimization phase.

| Variant | Round | Scenario | Current p95 (ms) | Post-opt target p95 (ms) |
|---------|-------|----------|------------------|--------------------------|
| A | A | smoke | 17453 | 8726 |


## Known caveats

* Streaming path does incremental emission but post-stream DLP — mid-stream PII from the provider is NOT masked on the wire.
* Variant B requires `python -m spacy download en_core_web_sm`.
* Variant C enables semantic router — install `requirements-semantic.txt` OR replace the embedding call with a mock in SemanticRouter.
* Round A = before streaming fix baseline; Round B = after streaming fix baseline. The user directive made the streaming fix a prerequisite — Round A is reconstructable only via `git checkout <pre-fix>` and re-running.
* `dlp_ner` latency is not cleanly separable from `dlp_regex` in Variant A (NER not registered). Variant B isolates it.
* Rate-limit phase still uses 12 sync Redis calls per request — preserved per directive as meta-validation target.
* httpx.AsyncClient is still created per provider call — preserved per directive.
* Baselines marked '{variant}_{round}_{scenario}' with N/A cells mean the scenario produced zero ``request_phases`` log lines (the runner couldn't find a writable GATEWAY_LOG_PATH). Re-run with `GATEWAY_LOG_PATH=...` pointed at the gateway's JSON log to populate the phase tables.

## How to reproduce

```
# Seed keys and confirm mock provider is running:
python perf/seed_keys.py --count 100
uvicorn perf.mock_provider.server:app --port 9090 &

# Full duration run (overnight). Omit --full for smoke-scale.
python perf/run-baseline.py --full

# Regenerate the report from a saved results directory:
python perf/run-baseline.py --report-from perf/results/20260422-153956_e0d7af0
```

---
Run manifest appended in `manifest.json`. Raw k6 summary JSONs + system
samples live under `perf/results/20260422-153956_e0d7af0/`.