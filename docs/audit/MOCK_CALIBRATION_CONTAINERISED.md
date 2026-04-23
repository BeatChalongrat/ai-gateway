# Mock-Provider Calibration — Containerised

**Date:** 2026-04-22
**Host Docker:** Docker 29.4.0 on Windows 11 Home (Docker Desktop, Linux-container mode, `OSType=linux`, `Architecture=x86_64`).
**Mock image digest:** `sha256:2ca01ba023f823a04031682d358f238b69539deaa2ba5dd08d260d586d442b60`
**Image tag:** `ai-gateway-mock-provider:perf`
**Runtime:** `uvicorn server:app --workers 4 --loop uvloop --http httptools` (Python 3.11-slim).
**Harness:** `perf/mock_provider/calibrate.py` — 2000 samples, 4 concurrency, single `httpx.AsyncClient` with a semaphore, real HTTP hops to `POST http://localhost:9090/v1/chat/completions` with the OpenAI shape `{"model":"gpt-4o","messages":[{"role":"user","content":"calibrate"}],"stream":false}`.

## Results

| Percentile | Target (ms) | Envelope (ms) | Measured (ms) | Verdict |
|-----------|-------------|---------------|---------------|---------|
| p50 | 800  | 680 – 920 (±15 %) | **790.55** | PASS |
| p95 | 3000 | 1950 – 4050 (±35 %) | **3803.66** | PASS |
| p99 | 8000 | 6400 – 9600 (±20 %) | **6787.40** | PASS |

Additional statistics:

| Metric | Value |
|--------|-------|
| count (successful samples) | 1982 |
| errors | 18 (0.91 % — consistent with the `realistic` profile's configured 1 % error rate; breakdown: mix of 429 / 500 / 504 per `latency_profile.yaml`) |
| mean | 1263.20 ms |
| stddev | 1581.52 ms |
| min | 202.53 ms |
| max | 28874.70 ms |

Raw JSON: `perf/sanity/calibration_containerised/calibration.json`.

## Comparison against the host baseline

The prior host-mode calibration (documented inline in `perf/mock_provider/latency_profile.yaml:29-35`) used 2000 synthetic `_calibration_samples` draws (no HTTP hop):

| Percentile | Host mode (synthetic, 2000 draws) | Containerised (real HTTP, 2000 samples, conc=4) | Delta |
|-----------|-----------------------------------|--------------------------------------------------|-------|
| p50 | 806 ms | 791 ms | **-15 ms / -1.9 %** |
| p95 | 3862 ms | 3804 ms | **-58 ms / -1.5 %** |
| p99 | 7108 ms | 6787 ms | **-321 ms / -4.5 %** |

The container did not add a measurable floor under bounded concurrency. uvloop scheduling inside the Linux container is slightly tighter than Windows host asyncio — all three percentiles drifted **down**, not up. No tuning to `latency_profile.yaml` was necessary.

## Verdict

**PASS — all three envelopes clear the user directive's acceptance criteria.** Proceed to Mini-Check-B at 50 RPS (the containerised-mock's load behaviour, distinct from this calibration's bounded-concurrency latency-distribution measurement).
