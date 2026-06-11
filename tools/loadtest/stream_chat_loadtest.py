#!/usr/bin/env python3
"""Streaming chat load-test harness for the SRAN AI Gateway.

Fires concurrent streaming (or non-streaming) chat-completion requests at the
gateway exactly the way ``chat-edge`` does — shared ``GATEWAY_API_KEY`` Bearer
auth, a distinct ``X-User-ID`` per virtual user, and an optional trusted
``X-Internal-Service-Secret`` so the backend buckets each VU on its own
per-end-user rate-limit counter (``api_key.key_id:user:<x_user_id>``).

It is deliberately READ-ONLY against the system: it sends normal API traffic
and records latency / status outcomes. It changes nothing server-side.

Endpoint (confirmed from the running stack):
    POST http://localhost:8080/v1/chat/completions
    Authorization: Bearer <GATEWAY_API_KEY>
    X-User-ID: <unique per virtual user>
    X-Internal-Service-Secret: <only when --internal-service-secret is given>

Usage (see --help for everything):
    python stream_chat_loadtest.py \
        --url http://localhost:8080/v1/chat/completions \
        --api-key "$GATEWAY_API_KEY" \
        --internal-service-secret "$INTERNAL_SERVICE_SECRET" \
        --model gpt-4o-mini \
        --concurrency 50 --duration-seconds 600 --ramp-seconds 30 \
        --payload-size-kb 2 --output-json results/stage_50.json

Exit code: 0 when the stage PASSES (success-rate / 429 / 5xx within thresholds),
non-zero otherwise — so a staged runner can stop on the first failing stage.

Dependency: httpx (already in backend/requirements.txt). Run with the backend
container's interpreter or any venv that has httpx.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    import httpx
except ImportError:  # pragma: no cover - import guard  # noqa: E402
    httpx = None  # set below after the message

# Make stdout UTF-8 where the host console allows it (Windows cp1252 would
# otherwise choke on any non-ASCII); harmless elsewhere. All human output is
# ASCII regardless, so this is belt-and-suspenders.
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

if httpx is None:  # pragma: no cover
    sys.stderr.write(
        "ERROR: httpx is required. Install with `pip install httpx` or run with "
        "the backend container interpreter:\n"
        "  docker exec -i ai-gateway-backend-1 python /app/../tools/...  (see docs)\n"
    )
    sys.exit(3)


# ---------------------------------------------------------------------------
# Per-request record + classification
# ---------------------------------------------------------------------------


@dataclass
class RequestResult:
    user_id: str
    status: int                 # HTTP status, or 0 for transport-level errors
    ok: bool                    # 200 AND stream completed cleanly
    duration_s: float           # full request lifetime (incl. stream read)
    ttft_s: Optional[float]     # time to first streamed byte/token, if any
    bucket: str                 # success | rate_limited | server_error |
                                # client_timeout | connection_error | other
    bytes_read: int = 0
    error: str = ""


@dataclass
class Summary:
    config: dict = field(default_factory=dict)
    total_requests: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    wall_seconds: float = 0.0
    requests_per_sec: float = 0.0
    rate_limited_429: int = 0
    rate_limited_429_rate: float = 0.0
    server_5xx: int = 0
    server_5xx_rate: float = 0.0
    client_timeouts: int = 0
    connection_errors: int = 0
    other_errors: int = 0
    error_total: int = 0
    duration_p50: float = 0.0
    duration_p95: float = 0.0
    duration_p99: float = 0.0
    duration_max: float = 0.0
    ttft_p50: Optional[float] = None
    ttft_p95: Optional[float] = None
    ttft_p99: Optional[float] = None
    ttft_samples: int = 0
    passed: bool = False
    fail_reasons: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank-ish percentile (linear interpolation via statistics)."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    # statistics.quantiles needs n>=2; use the inclusive method for stable tails.
    try:
        # Build 100 cut points and index — robust for p50/p95/p99.
        qs = statistics.quantiles(values, n=100, method="inclusive")
        idx = min(max(int(round(q * 100)) - 1, 0), len(qs) - 1)
        return qs[idx]
    except Exception:
        s = sorted(values)
        k = min(int(q * (len(s) - 1)), len(s) - 1)
        return s[k]


def _build_prompt(payload_size_kb: float, nonce: str) -> str:
    """A chat-shaped prompt padded to roughly ``payload_size_kb`` KB.

    The nonce makes every request unique so the gateway's response cache does
    NOT serve a cached body (which would make latency unrealistically low).
    """
    base = (
        "You are a helpful assistant for a load test. Please reply with a short, "
        "friendly two-sentence answer. Unique request marker: "
        f"{nonce}. Context filler follows: "
    )
    target_bytes = max(0, int(payload_size_kb * 1024) - len(base))
    # Filler that looks like prose, not a single repeated char.
    filler = ("The quick brown fox jumps over the lazy dog. " * 64)
    while len(filler) < target_bytes:
        filler += filler
    return base + filler[:target_bytes]


def _classify_status(status: int) -> str:
    if status == 200:
        return "success"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    return "other"


# ---------------------------------------------------------------------------
# Single request
# ---------------------------------------------------------------------------


async def _one_request(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    user_id: str,
    req_seq: int,
) -> RequestResult:
    nonce = f"{user_id}-{req_seq}-{time.time_ns()}"
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
        "X-User-ID": user_id,
    }
    if args.internal_service_secret:
        headers["X-Internal-Service-Secret"] = args.internal_service_secret

    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": _build_prompt(args.payload_size_kb, nonce)}],
        "stream": args.stream,
        "max_tokens": args.max_tokens,
        # Opt into usage on the trailing chunk like chat-edge does.
        "metadata": {"loadtest": "1", "vu": user_id},
    }

    start = time.perf_counter()
    ttft: Optional[float] = None
    bytes_read = 0

    try:
        if args.stream:
            headers["Accept"] = "text/event-stream"
            async with client.stream(
                "POST", args.url, json=body, headers=headers
            ) as resp:
                status = resp.status_code
                if status != 200:
                    await resp.aread()
                    dur = time.perf_counter() - start
                    return RequestResult(
                        user_id, status, False, dur, None,
                        _classify_status(status),
                        error=f"http {status}",
                    )
                got_done = False
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if ttft is None:
                        ttft = time.perf_counter() - start
                    bytes_read += len(line)
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            got_done = True
                        elif '"error"' in payload:
                            # Mid-stream provider error frame.
                            dur = time.perf_counter() - start
                            return RequestResult(
                                user_id, status, False, dur, ttft,
                                "server_error", bytes_read,
                                error="stream_error_frame",
                            )
                dur = time.perf_counter() - start
                ok = got_done or bytes_read > 0
                return RequestResult(
                    user_id, status, ok, dur, ttft, "success",
                    bytes_read,
                )
        else:
            resp = await client.post(args.url, json=body, headers=headers)
            status = resp.status_code
            text = resp.text
            bytes_read = len(text)
            ttft = time.perf_counter() - start  # full body for non-stream
            dur = ttft
            if status != 200:
                return RequestResult(
                    user_id, status, False, dur, None,
                    _classify_status(status), bytes_read,
                    error=f"http {status}",
                )
            ok = True
            try:
                data = resp.json()
                ok = bool(data.get("choices"))
            except Exception:
                ok = False
            return RequestResult(
                user_id, status, ok, dur, ttft, "success", bytes_read,
            )

    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
            httpx.PoolTimeout) as exc:
        return RequestResult(
            user_id, 0, False, time.perf_counter() - start, None,
            "client_timeout", bytes_read, error=type(exc).__name__,
        )
    except (httpx.ConnectError, httpx.RemoteProtocolError,
            httpx.ReadError, httpx.NetworkError) as exc:
        return RequestResult(
            user_id, 0, False, time.perf_counter() - start, None,
            "connection_error", bytes_read, error=type(exc).__name__,
        )
    except Exception as exc:  # noqa: BLE001
        return RequestResult(
            user_id, 0, False, time.perf_counter() - start, None,
            "other", bytes_read, error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Virtual user loop
# ---------------------------------------------------------------------------


async def _virtual_user(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    index: int,
    deadline: float,
    results: list[RequestResult],
) -> None:
    user_id = f"{args.user_prefix}{index:06d}"
    # Ramp: stagger start so 250 VUs don't all fire on the same tick.
    if args.ramp_seconds > 0 and args.concurrency > 1:
        await asyncio.sleep((index / args.concurrency) * args.ramp_seconds)

    seq = 0
    while time.monotonic() < deadline:
        seq += 1
        res = await _one_request(client, args, user_id, seq)
        results.append(res)
        # Realistic pacing — never a tight loop. think time + jitter.
        think = args.think_time_seconds + random.uniform(0, args.jitter_seconds)
        # Don't oversleep past the deadline.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(think, remaining))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _summarize(args: argparse.Namespace, results: list[RequestResult],
               wall: float) -> Summary:
    s = Summary()
    s.config = {
        "url": args.url, "model": args.model, "stream": args.stream,
        "concurrency": args.concurrency, "duration_seconds": args.duration_seconds,
        "ramp_seconds": args.ramp_seconds, "payload_size_kb": args.payload_size_kb,
        "think_time_seconds": args.think_time_seconds, "jitter_seconds": args.jitter_seconds,
        "max_tokens": args.max_tokens, "user_prefix": args.user_prefix,
        "ner_mode": args.ner_mode, "trusted_user_keying": bool(args.internal_service_secret),
        "timeout_seconds": args.timeout_seconds,
    }
    s.total_requests = len(results)
    s.wall_seconds = round(wall, 2)
    if not results:
        return s

    durations = [r.duration_s for r in results]
    ttfts = [r.ttft_s for r in results if r.ttft_s is not None]

    s.success_count = sum(1 for r in results if r.ok)
    s.rate_limited_429 = sum(1 for r in results if r.bucket == "rate_limited")
    s.server_5xx = sum(1 for r in results if r.bucket == "server_error")
    s.client_timeouts = sum(1 for r in results if r.bucket == "client_timeout")
    s.connection_errors = sum(1 for r in results if r.bucket == "connection_error")
    s.other_errors = sum(1 for r in results if r.bucket == "other")
    s.error_total = (s.server_5xx + s.client_timeouts + s.connection_errors
                     + s.other_errors)

    n = s.total_requests
    s.success_rate = round(s.success_count / n, 4)
    s.requests_per_sec = round(n / wall, 2) if wall > 0 else 0.0
    s.rate_limited_429_rate = round(s.rate_limited_429 / n, 4)
    s.server_5xx_rate = round(s.server_5xx / n, 4)

    s.duration_p50 = round(_pct(durations, 0.50), 4)
    s.duration_p95 = round(_pct(durations, 0.95), 4)
    s.duration_p99 = round(_pct(durations, 0.99), 4)
    s.duration_max = round(max(durations), 4)
    if ttfts:
        s.ttft_samples = len(ttfts)
        s.ttft_p50 = round(_pct(ttfts, 0.50), 4)
        s.ttft_p95 = round(_pct(ttfts, 0.95), 4)
        s.ttft_p99 = round(_pct(ttfts, 0.99), 4)

    # Pass/fail.
    reasons = []
    if s.success_rate < args.min_success_rate:
        reasons.append(
            f"success_rate {s.success_rate:.4f} < {args.min_success_rate}")
    if s.rate_limited_429_rate > args.max_429_rate:
        reasons.append(
            f"429_rate {s.rate_limited_429_rate:.4f} > {args.max_429_rate}")
    if s.server_5xx_rate > args.max_5xx_rate:
        reasons.append(
            f"5xx_rate {s.server_5xx_rate:.4f} > {args.max_5xx_rate}")
    s.fail_reasons = reasons
    s.passed = not reasons
    return s


def _print_summary(s: Summary) -> None:
    c = s.config
    line = "-" * 64
    print("\n" + line)
    print("LOAD TEST SUMMARY")
    print(line)
    print(f"  url                 : {c['url']}")
    print(f"  model               : {c['model']}  (stream={c['stream']}, "
          f"max_tokens={c['max_tokens']})")
    print(f"  concurrency         : {c['concurrency']}  "
          f"(ramp {c['ramp_seconds']}s, duration {c['duration_seconds']}s)")
    print(f"  pacing              : think {c['think_time_seconds']}s "
          f"+ jitter 0..{c['jitter_seconds']}s")
    print(f"  payload             : ~{c['payload_size_kb']} KB   "
          f"ner_mode={c['ner_mode']}   trusted_user_keying={c['trusted_user_keying']}")
    print(line)
    print(f"  wall time           : {s.wall_seconds:.2f} s")
    print(f"  total requests      : {s.total_requests}")
    print(f"  throughput          : {s.requests_per_sec:.2f} req/s")
    print(f"  success             : {s.success_count}  "
          f"({s.success_rate*100:.2f}%)")
    print(f"  429 (rate limited)  : {s.rate_limited_429}  "
          f"({s.rate_limited_429_rate*100:.2f}%)")
    print(f"  5xx (server error)  : {s.server_5xx}  "
          f"({s.server_5xx_rate*100:.2f}%)")
    print(f"  client timeouts     : {s.client_timeouts}")
    print(f"  connection errors   : {s.connection_errors}")
    print(f"  other errors        : {s.other_errors}")
    print(line)
    print("  total request duration (s):")
    print(f"     p50={s.duration_p50:.3f}  p95={s.duration_p95:.3f}  "
          f"p99={s.duration_p99:.3f}  max={s.duration_max:.3f}")
    if s.ttft_samples:
        print(f"  time-to-first-token (s)  [{s.ttft_samples} samples]:")
        print(f"     p50={s.ttft_p50:.3f}  p95={s.ttft_p95:.3f}  "
              f"p99={s.ttft_p99:.3f}")
    print(line)
    verdict = "PASS" if s.passed else "FAIL"
    print(f"  VERDICT             : {verdict}")
    if s.fail_reasons:
        for r in s.fail_reasons:
            print(f"     - {r}")
    print(line + "\n")


async def _run(args: argparse.Namespace) -> Summary:
    results: list[RequestResult] = []
    # No connection cap below concurrency: each VU needs its own in-flight
    # stream. Add headroom so the pool never serializes the test itself.
    limits = httpx.Limits(
        max_connections=args.concurrency + 50,
        max_keepalive_connections=args.concurrency + 50,
    )
    # Per-request timeout. connect short; read long enough for a stream.
    timeout = httpx.Timeout(
        connect=10.0, read=args.timeout_seconds,
        write=30.0, pool=args.timeout_seconds,
    )
    test_start = time.monotonic()
    deadline = test_start + args.ramp_seconds + args.duration_seconds

    async with httpx.AsyncClient(limits=limits, timeout=timeout,
                                 http2=False) as client:
        vus = [
            asyncio.create_task(
                _virtual_user(client, args, i, deadline, results))
            for i in range(args.concurrency)
        ]
        await asyncio.gather(*vus)

    wall = time.monotonic() - test_start
    return _summarize(args, results, wall)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Streaming chat load-test for the SRAN AI Gateway.")
    p.add_argument("--url",
                   default=os.environ.get(
                       "LOADTEST_URL",
                       "http://localhost:8080/v1/chat/completions"))
    p.add_argument("--api-key", default=os.environ.get("GATEWAY_API_KEY", ""),
                   help="Shared gateway API key (Bearer).")
    p.add_argument("--internal-service-secret",
                   default=os.environ.get("INTERNAL_SERVICE_SECRET", ""),
                   help="If set, sent as X-Internal-Service-Secret so the "
                        "backend trusts X-User-ID for per-user rate limiting.")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--concurrency", type=int, default=25)
    p.add_argument("--duration-seconds", type=int, default=300)
    p.add_argument("--ramp-seconds", type=int, default=15)
    p.add_argument("--payload-size-kb", type=float, default=2.0)
    p.add_argument("--user-prefix", default="loadtest-user-")
    p.add_argument("--ner-mode", default="default",
                   choices=["default", "off", "on"],
                   help="Informational label for the run. Actual NER is a "
                        "server-side tenant/DLP-profile setting — see docs.")
    p.add_argument("--timeout-seconds", type=float, default=60.0,
                   help="Per-request read timeout.")
    p.add_argument("--think-time-seconds", type=float, default=1.0,
                   help="Base pause between a VU's requests (anti tight-loop).")
    p.add_argument("--jitter-seconds", type=float, default=1.0,
                   help="Random extra pause 0..jitter added to think time.")
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--stream", default="true",
                   choices=["true", "false"])
    p.add_argument("--output-json", default="")
    # Pass/fail thresholds.
    p.add_argument("--min-success-rate", type=float, default=0.99)
    p.add_argument("--max-429-rate", type=float, default=0.01)
    p.add_argument("--max-5xx-rate", type=float, default=0.005)
    ns = p.parse_args(argv)
    ns.stream = (ns.stream == "true")
    if not ns.api_key:
        p.error("--api-key (or GATEWAY_API_KEY env) is required.")
    return ns


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    print(f"[loadtest] {args.concurrency} VUs -> {args.url} "
          f"(model={args.model}, stream={args.stream}, "
          f"trusted_user_keying={bool(args.internal_service_secret)})")
    summary = asyncio.run(_run(args))
    _print_summary(summary)
    if args.output_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_json)),
                    exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(asdict(summary), fh, indent=2)
        print(f"[loadtest] wrote {args.output_json}")
    return 0 if summary.passed else 1


if __name__ == "__main__":
    sys.exit(main())
