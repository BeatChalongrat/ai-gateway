# DLP Rust Remediation — Benchmark & Flip Report

Generated: 2026-05-30, branch `perf/dlp-rust-remediation`. `DLP_ENGINE=rust` flipped live
after the parity gate reached 100%.

## Parity gate — PASS ✅ (100%)

| | Before (Phase 0) | After remediation |
|---|---|---|
| Full parity | 188/197 (95.4%) | **211/211 (100%)** |
| Rust false-negatives | 9 | **0** |
| Rust false-positives | 3 | **0** |

`backend/tests/dlp_parity_runner.py` (192 fixtures + 5 evasion + 14 gap-coverage). Every
detector group ✅; evasion 5/5 ✅; gap 14/14 ✅. The 4 blocking + 7 minor detector gaps and
all 5 encoding-bypass evasion vectors are closed.

## Live verification (DLP_ENGINE=rust)
- **Masking:** `email alice@example.com … card 4111111111111111` → 2 findings; `input_masked`
  contains `<<EMAIL_1>>` / `<<CARD_1>>`, raw values absent. ✅
- **Evasion (wrapper):** base64-embedded email → 1 finding (decoded + Rust-detected + mapped to
  the encoded span). ✅
- **Fail-open:** `dlp-sidecar` stopped → request HTTP 200, email still masked (1 finding via the
  Python registry); logs `dlp_rust_rpc_failed` → `dlp_rust_fallback_to_python`. Sidecar restart →
  healthy. ✅
- NER still routes to the `ner-service` (Rust does not do NER; `thai_name_no_prefix` is its
  bare-name floor, now registered always).

## Latency — honest trade-off

Per-scan p50 (parity corpus, small inputs): **Python 0.39 ms · Rust+wrapper 2.69 ms.**

Rust is **slower per scan on small inputs** because the gRPC round-trip (~2 ms local) dominates
sub-ms regex work — same shape seen in earlier phases. The value of `DLP_ENGINE=rust` is **not**
small-input latency; it is **moving the regex CPU off the Python process entirely** (to the Rust
sidecar), freeing the gateway's worker threadpool + GIL under heavy concurrent DLP load, and
faster scaling on large payloads. Note Phase 1 already moved the Python DLP scan *off the event
loop* (`to_thread`), so for low/moderate load the Python engine is competitive and avoids the
gRPC hop.

**Recommendation:** the flip is now **safe** (100% parity, fail-open). Whether to keep it on is a
workload call — it pays off under high concurrent DLP volume / large messages, and costs ~2 ms/scan
on small inputs. **Rollback is instant: `DLP_ENGINE=python` + recreate backend.** The Python path
remains the fail-open fallback regardless.

## Definition of Done — all met
✅ 4 blocking + 7 minor detector gaps fixed & tested · ✅ Python preprocessing wrapper (zero-width /
homoglyph / base64) with correct offset mapping · ✅ Parity 100% (zero mismatches) · ✅
`DLP_ENGINE=rust` live · ✅ Fail-open verified · ✅ Benchmark recorded · ✅ Parity suite expanded
(+14 gap cases) · ✅ DLP_REMEDIATION_PLAN.md (local) documents every fix.
