# When the DLP sidecar is down or wedged

FIX103. Measured on the running stack on 2026-09-10; re-verify with the
commands at the end whenever the DLP path changes.

## What happens to a request — fail-over, not fail-open

With `DLP_ENGINE=rust`, the backend scans input through the Rust
`dlp-sidecar` over gRPC (1.0 s timeout). On **any** failure — refused
connection, timeout, RPC error — `RustDlpClient` returns `None` and
`orchestrator._run_dlp_scan` rescans the whole request on the **Python
registry** (log event `dlp_rust_fallback_to_python`, metric
`sidecar_fallback_total{engine="dlp"}`).

The Python registry has 31 detectors: the sidecar's 30 plus `thai_ner`. So
the fallback is not a weaker mode. Responses and tool arguments are always
scanned in Python and never touch the sidecar.

Proven live with synthetic PII (`081-234-5678`, `fix103.probe@example.com`)
sent through `:8080` with the test key:

| Sidecar | Input action | Findings | DLP time |
|---|---|---|---|
| up | mask (Rust) | 2 | 608 ms |
| stopped | fallback → mask (Python) | 2 | 622 ms |
| paused (a wedge: accepts, never answers) | 1 s timeout → fallback → mask | 2 | 1,278 ms |

With the sidecar stopped, the provider was asked whether the email ended in
`@example.com` and whether the phone started with `081`. It answered
*cannot see / cannot see*, and that it saw placeholders.

**This is a decision, and it is the owner's.** The alternative is fail-closed:
refuse the request when the sidecar cannot answer. It is safer only if the
Python registry is ever weaker than the sidecar, which today it is not. And it
turns a wedged sidecar into an outage of the whole product. Nothing in FIX103
changes the behaviour.

## What a person sees now

| Where | Sidecar healthy | Sidecar down or wedged |
|---|---|---|
| `docker ps` — `dlp-sidecar` | `(healthy)` | `(unhealthy)` after ~40 s; `docker inspect` Health log says `healthz FAILED … no answer within 3 s` |
| backend `:8081/health` and `:8080/health` | 200 `ok` | 200 `degraded`, `sidecars.dlp_sidecar.status=unreachable` |
| backend `:8081/ready` and `:8080/ready` | 200 `ready` | 200 `ready`, `degraded: ["dlp_engine"]`, `dlp_engine: "degraded: rust sidecar unreachable — masking on the python fallback"` |
| gateway `:8080/__gw/ready` | 200 `ready` | 200 `ready`, `degraded: ["dlp_engine"]` |
| gateway `:8080/__gw/ready`, backend down | — | **503** (before FIX103: 200, it only pinged Postgres) |
| Prometheus | — | `DlpSidecarFallingBack` (warning) after 2 m; not for `reason="refused"` |
| backend log | — | `dlp_rust_rpc_failed` / `dlp_rust_rpc_timeout`, then `dlp_rust_fallback_to_python`, per request |

`:8080/__gw/health` is liveness only and stays 200.

`/ready` goes 503 on DLP only when **no** engine can scan the planted PII.

## Why it wedged

It was not a slow regex; it was an endless loop.

- **The limit.** `fancy-regex` gives up at its backtrack limit and returns an
  error.
- **The iterators.** Its match iterators return that same error again on every
  `next()`, without advancing.
- **The detectors.** Every detector consumed them with `.flatten()`, which
  throws errors away.
- **The loop.** So once an input crossed the limit, one scan spun at 100 % CPU
  forever. The runtime serving `/healthz` starved, and later scans queued
  behind it.

Measured on a private sidecar instance:
- The real FIX102 extraction prompt scans in 130 ms at 32,000 characters and
  never finishes at 40,000.
- Its second half, scanned alone, takes 390 ms.
- `religion_belief` alone takes 37 ms at 36,000 ASCII characters and never
  finishes at 40,000. The same at 40,000 Thai characters takes 41 ms.
- The Python registry scans the full 63,357-character prompt in 0.305 s.

**Fix, not a bound.** Every engine error now ends that iteration and is
recorded; the sidecar refuses the whole scan (`RESOURCE_EXHAUSTED`, naming the
detector). The backend then falls back to Python for that request. Nothing is
chunked or skipped, so mask quality is unchanged. A panicking detector is
refused the same way (it used to be skipped). Guarded by
`dlp-sidecar/tests/fix103_large_input.rs`.

**What that means in practice.** The limit is reached far earlier than the
wedge suggested:
- `thai_name_no_prefix` refuses English text from about **8,000 characters**
  (measured at 8k, 16k, 24k, 28k, 31k and on plain word salad at 20k and 30k).
- Thai prose at 20k and 40k is scanned normally.

So most large English requests — every long compaction fold — now mask on the
Python registry, after a Rust refusal that returns in 0.06–0.4 s. Before this
change the same limit made that detector silently stop; the sidecar reported
a clean scan.

**Open question: did the silent stop ever lose a mask?** Not proven. In two
probes, a Thai name at the end of 32,000 characters of English, the old
sidecar still found it through another pass.

Refusals are counted apart from outages:
- metric `sidecar_fallback_total{engine="dlp",reason="refused"}`
- log `dlp_rust_scan_refused`, then `dlp_rust_fallback_to_python reason=refused`,
  at info level

`DlpSidecarFallingBack` excludes them, so it keeps meaning "not answering".

Raising fancy-regex's backtrack limit would keep more scans on Rust. It would
not change what gets masked. It is not done here.

## Re-verify

- **Sidecar tests.**
  `docker build --target builder -t dlp-sidecar-builder ./dlp-sidecar`, then
  `docker run --rm -v ./dlp-sidecar/tests:/src/tests:ro dlp-sidecar-builder cargo test --release`.
- **Fail-over.** Stop the sidecar, send a request with synthetic PII, and look
  for `dlp_rust_fallback_to_python` followed by `dlp_scan_complete … action=mask`
  in the backend log.
- **Healthcheck.** `docker inspect ai-gateway-dlp-sidecar-1 --format '{{json .State.Health}}'`
  now carries a reason in every log entry.
