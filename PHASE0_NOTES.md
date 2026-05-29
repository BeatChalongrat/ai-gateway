# Phase 0 — "Turn On What's Already Built" — Execution Notes

Branch: `perf/phase-0-sidecars`. Tracks decisions, flips, validation evidence, and
deferred follow-ups. Companion to `PERFORMANCE_AUDIT_REPORT.md`.

Runtime flips are made in `.env` (gitignored) so they are instantly rollback-able
per the Phase-0 principle; the committed compose default stays `python` so fresh
checkouts / k8s opt in explicitly. This file is the tracked record of what was
flipped on the running stack.

---

## Session 0.1 — Pre-flight fixes & NER audit ✅

- **F17 (compose comment):** rewrote the stale "Batch 1 stub / UNIMPLEMENTED" DLP
  sidecar comment — RPCs are operational, 29 detectors ported. Recorded the real
  pre-flip gap (see DLP finding below). `docker-compose.yml`.
- **NER URL audit:** `NER_SERVICE_URL` already set in `docker-compose.yml:251`
  (default) + `infra/k8s/backend-deployment.yaml:49`. Added it (and `DLP_ENGINE`)
  to `scripts/uat/.env.uat.template`. Live backend env confirms
  `NER_SERVICE_URL=http://ner-service:8001`. In-process spaCy fallback intact
  (`ner_service_client.py:63` returns None when unset → in-process path).
- Commit: `20789dc`.

### 🚩 Finding (DLP) — audit under-specified, decision taken
The audit called it a "base64-provenance metadata gap." Verified reality:
1. **No one reads `DetectionMatch.metadata`** (only set at `registry.py:136`), so
   populating it in Rust would feed nothing; shadow-diff compares (detector,start,end).
2. **Real gap:** the Rust path is **NFKC-only** and does not port the Python
   encoding-bypass preprocessing — **base64 decode-and-scan**, **zero-width strip +
   offset remap**, **homoglyph mapping** (`preprocessor.py` / `registry.py:106-137`).
   In `DLP_ENGINE=rust` the orchestrator sends raw content (`rust_dlp_client.py:133`),
   so evasion-encoded sensitive data → Python detects, Rust misses. Existing parity
   fixtures cover none of these vectors.
- **Decision (Beat):** **pre-normalize in Python wrapper** — keep `normalize_for_scan`
  in Python before the gRPC call, port only detection to Rust. To be implemented in
  the DLP track (before the 0.5 flip); the 0.4 parity suite MUST include evasion
  vectors to prove it.

---

## Session 0.2 — Log-worker gap fix + flip ✅

- **R5 fix (per-RPC fallback):** in `engine=go`, a *dispatched-but-failed* gRPC call
  (sidecar reachable but erroring/timing out, or `accepted=false`) used to drop the
  audit row — `_dispatch_to_grpc` returned True on schedule and `_on_grpc_done` only
  logged. Now `_on_grpc_done` is bound to the record and re-routes any failure through
  `_enqueue_local` (running asyncio queue → batched; else inline `_sync_fallback`).
  Supersedes the pre-R5 "accepted=false drops the row" contract; tests updated + 3
  added. `request_log_queue.py`, `test_log_worker_client.py`. Commit: `2c73867`.
  Tests: 13 + 10 passed.
- **Flip:** `LOG_WORKER_ENGINE=go` set in `.env`; backend rebuilt (bakes R5 fix) +
  recreated. **Rollback:** delete the `.env` line (or set `=python`) + recreate backend.
- **Live validation (stack up, key `gw_4be18…`):**
  - 5 requests → HTTP 200; `request_logs` 1902→1907 (+5); `log_worker_flushed_total`
    0→5 — Go worker COPY-inserted exactly the new rows. ✓
  - Fallback: stopped `log-worker`, 3 requests → HTTP 200; `request_logs` 1907→1910
    (+3) via Python fallback; backend logged 3× `log_worker_grpc_fallback`
    (`reason=rpc_rejected_or_failed`) + `log_worker_rpc_failed`. No crash, no loss. ✓
  - Restarted `log-worker` → healthy. ✓

### 🚩 Follow-up TODO (Beat: accept + flip, fix later) — BEFORE reasoning goes live
**Go log-worker drops `reasoning_tokens` + `reasoning_cost`.** Both columns exist on
`request_logs` (NOT NULL default 0) and are written by the Python path
(`request_log_queue.py:88-89`), but are **absent** from the proto `LogRecord`
(fields 1-30), `record_to_proto` (`log_worker_client/client.py:76-122`), and the Go
COPY column list (`inserter.go:27-57`). Safe today (server-side defaults fill 0;
reasoning is dormant in this env), but **lossy for reasoning cost when reasoning is
provisioned**. Fix = add proto fields 31/32 → regen Go + Python stubs → update
`record_to_proto` + Go inserter columns/rowArgs → rebuild log-worker image. **Gate:
do this before any reasoning-capable model gets a valid provider key.**

---

## Pending
- Session 0.3 — Rate-limiter shadow compare + flip `RATE_LIMITER_ENGINE=rust`.
- Session 0.4 — DLP parity suite (incl. evasion vectors) + Python-wrapper preprocessing.
- Session 0.5 — DLP flip + `PHASE0_BENCHMARK.md`.
