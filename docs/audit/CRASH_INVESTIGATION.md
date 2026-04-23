# Perf Baseline Run — Crash Investigation

**Run ID:** `20260422-162948_e0d7af0`
**Failure window:** 16:33:33 → 16:34:xx (stall; no true crash)
**Completed:** 5/24 runs (smoke × 3 + steady_run0 + steady_run1). `A_B_steady_run2` started but never emitted a post-run summary; `A_B_ramp` never started.
**State at investigation time:** backend, mock provider, and k6 processes all gone; no `Traceback`, no OOM trace, no Windows Event error.

---

## TL;DR — Not a crash. A project-level rate limit cascade.

The "24 runs" (8 scenarios × 3) refers to **per-variant** work. Matrix total remains 72 = 3 variants × 24. Builder's earlier 54-vs-72 framing was counting remaining vs. total; there is no matrix size error.

The stall root cause is simple and entirely in our test setup, not in the gateway: the `steady.js` scenario drives **50 iters/sec** (3000 req/min) through a single **project** whose rate limit is **300 req/min**. 90% of requests were rejected with **429 Too Many Requests**, but the 429 path itself spent **4.7 s of event-loop queue time per request** (see §3 below), which back-pressured k6's VU pool until it saturated at its 200 ceiling, after which k6 made no forward progress and eventually exited when Docker reaped it.

There is **no evidence of any resource-side crash**. No OOM, no FD exhaustion, no memory pressure, no port conflict, no uvicorn reload storm.

---

## 1. Evidence gathered

| Source | Path | Verdict |
|---|---|---|
| Runner command log | `logs/run-variant-A.log` | Kicks off 3 smoke + 3 steady via `docker run grafana/k6`. No error line. Last entry: `[A/B/steady] run 3/3` at ~16:33. |
| Runner progress tracker | `tasks/bn9gqf8x1.output` | `[16:30:31] 2/24 · [16:31:32] 4/24 · [16:32:33] 5/24 · [16:33:33] 5/24` — **progress flatlines at 5/24**. |
| Backend stdout | `logs/gateway.log` (40 k lines, 7.8 MB) | No exceptions, no tracebacks. **5 356 `Rate limit exceeded for project (300 req/min)` events. 10 712 total 429 log lines** (each 429 logged twice — gateway_error + request_phases). |
| Mock provider | `logs/mock-provider.log` | Healthy throughout — steady 200 OKs, no errors. |
| k6 stdout (last scenario) | `perf/results/20260422-162948_e0d7af0/A_B_steady_run2/k6_stdout_steady.log` | Scenario started at 50 iters/s target. At 0m21s: `Insufficient VUs, reached 200 active VUs and cannot initialize more`. Log truncates mid-progress at 0m43s/1m0s (146 lines, 6 378 bytes) — summary never written. |
| System samples (during steady_run2) | `.../system_samples.csv` | `proc_cpu_pct = 0.0` on every row (meta-bug — wrong PID, see §4). `proc_rss_mb ≈ 220 MB stable`, `sys_mem_avail ≈ 13 GB`, `proc_fds_or_handles grew 875 → 1 452 in 47 s` (~12 new handles/s — consistent with per-call httpx.AsyncClient preservation, not a new leak). |
| Windows Event Log | scanned via `Get-WinEvent` window 16:30 – 16:35 | Nothing relevant. No process-died entry. |

## 2. Hypotheses — disposition

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| 1 | Docker Desktop WSL2 memory pressure | **Rejected** | 13 GB free throughout; no Docker daemon restart line |
| 2 | Port conflict (9090 = Prometheus default, 8080 = common) | **Rejected** | Mock served requests continuously until stall |
| 3 | FD exhaustion | **Rejected** | 1 452 handles < Windows default (~ 16 k). Growth explained by known httpx-per-call behaviour, not a new leak |
| 4 | uvicorn `--reload` storm | **Rejected** | Builder ran without `--reload` per Step 1 of approved plan (confirmed in `backend-override.log`: single PID 26544, no reload messages) |
| 5 | k6 OOM | **Rejected** | k6 runs in its own short-lived container per run; no Docker OOM event |
| 6 | Mock provider async queue backpressure | **Rejected** | Mock log shows steady 200 OKs throughout; no error-rate spike |
| 7 | **Project-level rate limit saturation** | **✅ CONFIRMED** | 5 356 `Rate limit exceeded for project (300 req/min)` events in gateway.log |
| 8 | **Slow-429 back-pressure on k6 VU pool** | **✅ CONFIRMED** | `total_ms` on 429 responses is **4 700 – 4 860 ms** despite `rate_limit_ms: 20 – 28 ms`. That's **~4.7 s of event-loop queue time before the rate-limit check fires.** k6 VU pool saturated at 200 (log line: `Insufficient VUs, reached 200 active VUs`). |

## 3. The 4.7 s anomaly — separate finding worth documenting

On rejected (429) requests:

```
auth_ms=5, rate_limit_ms=24, routing_ms=0, dlp_*=0, guardrails_ms=0,
cache_ms=0, provider_ms=0, response_assembly_ms=0, total_ms=4 800
```

Sum of instrumented phases ≈ 29 ms. Remaining **~4 770 ms is unaccounted** — this is time the request spent waiting to start (asyncio queue / starlette request-queue) before `auth` began. Under 50 iters/s load + project limit set to 300/min, this queuing time becomes the dominant cost.

**This is not a bug the baseline should hide — it is a real finding about gateway behaviour at > cap load.** Flag for Auditor. Do NOT "fix" by raising the project limit for production; only fix the test config (see §5).

## 4. Meta-bug found during investigation

`perf/collect_system.py` is reporting `proc_cpu_pct = 0.0` on every sample. It's likely reading from the wrong PID (the runner's, not the backend's). Must fix in Phase 2 — otherwise the Auditor has no CPU-per-phase signal to rank Go candidates.

## 5. Fix required before re-run

Two failures stacked: one test-config, one diagnostic-tool.

### A. Test-config (primary)
`perf/seed_keys.py` seeds 100 keys **all under a single project** (`perf-test-project`). Project rate limit is `300 req/min`. Load test drives 50 – 500 RPS = 3 000 – 30 000 req/min. Must choose one of:

1. **Raise the project limit for `perf-test-project`** to a per-test-scenario-safe value (e.g. 120 000 req/min = 2 000 RPS). Least invasive. Requires tagging the project as "perf-test" so the change is obviously bounded.
2. **Spread 100 keys across 100 projects** (each with default 300/min). Most realistic but changes the semantics — the load distribution is now per-project, which is not what we want to measure. Not recommended.
3. **Disable project limits** for this project via DB setting. Simplest. Requires confirmation there's no test-isolation side-effect.

**Recommendation: option 1.** Also raise the tenant limit if one exists — grep shows `per-tenant`, `per-project`, `per-key` all in play. The test config should be: per-key = production default (60/min, covered by 100-key pool), per-project = ∞ (or 500 × 60 × safety_margin), per-tenant = ∞.

The `rate-limiter-stress.js` scenario is explicitly designed to hit these limits — it hammers a SINGLE key past 60/min. That scenario should keep default limits. All other scenarios need the raised cap.

### B. Diagnostic-tool (secondary)
Fix `perf/collect_system.py` to read the backend PID explicitly (pass via `--backend-pid` or discover by scanning `:8080` listeners). Verify with a smoke run that `proc_cpu_pct > 0` during steady load.

## 6. Confidence

- **Stall cause: ~95%** — the project-rate-limit cascade explanation is strongly supported by direct evidence (5 356 log events, 10 712 429 responses, k6 VU saturation, timeline match). Residual 5% is for unexplained `total_ms=4 800` on 429 — I suspect starlette request queue + asyncio scheduling, not a separate bug, but pure inference.
- **Full re-run success after the fix: ~85%** — the rate-limit fix resolves the stall mechanism. Remaining risk is the secondary FD-growth-under-load behaviour over a multi-hour soak (not observed fatal in 47 s, but untested at scale). Recommend soak 30 min × 3 × 3 as the user already proposed; if FD climbs linearly at the observed rate, that's ~12 /s × 30 min × 180 = ~130 000 handles — would exceed Windows default. **Must mitigate or run soak variants in Linux Docker.**

## 7. Additional note about "24 vs 72"

Builder counted 24 = 8 scenarios × 3 runs **per variant**. User counted 72 = same × 3 variants. Both correct, different scopes. Plan should state "24 runs per variant; 72 across the matrix". No discrepancy to resolve — just notation.

---

## Phase 2 hardening (from user directive) — reconciled with findings above

Pre-existing user asks, now with crash-investigation context:

1. Disable uvicorn `--reload` — **already done by Builder in Step 1** (confirmed via `backend-override.log`). Keep.
2. Supervisor wrapper — **RECOMMEND ADOPTING.** The current stall mode (slow-429 back-pressure → k6 VU starvation → docker reaps) would have been caught by a heartbeat gap detector. Supervisor should kill + fail the scenario if no summary JSON appears within scenario_duration + 30 s grace.
3. `df -h` ≥ 20 GB — trivial check, include.
4. Windows FD ulimit — **must address.** Either raise Windows handle limit (possible via `SetProcessWorkingSetSize` programmatically / registry) or run soak scenarios on Linux Docker. **Strong recommendation: soak scenarios run only on Linux Docker.**
5. Detached background execution — good practice, adopt.
6. `heartbeat.json` every 60 s — adopt, and have supervisor check it.
7. **New: collect_system.py PID fix (my §4).** Prerequisite — blocker for Auditor.
8. **New: test-config rate-limit raise (my §5A).** Prerequisite — blocker for completing even one scenario.

---

## Checkpoint

STOP here per directive. Fix items §5A + §5B + Phase-2 items 2/4/6/7 before any re-run. Do not re-launch the 4-6 h full matrix until all three checkpoints are clean:

1. Single-request sanity: 1 key, 1 project, low RPS → 200 OK + correct per-phase metrics
2. Mid-RPS sanity: steady.js at 50 RPS for **60 s** → 0 × 429 (project limit raised), k6 exits with valid summary JSON
3. `collect_system.py` reports `proc_cpu_pct > 0` during the mid-RPS run
