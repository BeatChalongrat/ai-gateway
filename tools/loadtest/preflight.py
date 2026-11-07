#!/usr/bin/env python3
"""Preflight checks for the SRAN AI Gateway load-test baseline.

Run this BEFORE any staged load test. It verifies the environment, endpoint
reachability, a single working streaming request, and — opt-in — that the
shared-API-key per-user rate-limit fix is actually ACTIVE (two trusted
X-User-IDs get separate counters; a spoofed X-User-ID without the internal
secret falls back to the shared key).

It is read-only except for the request traffic it sends (a single stream by
default; a small burst only when --probe-rate-limit is passed). It changes
no server state.

Exit code 0 = safe to proceed. Non-zero = a hard blocker was found.

Example:
    python preflight.py \
        --url http://127.0.0.1:28080/v1/chat/completions \
        --api-key "$GATEWAY_API_KEY" \
        --internal-service-secret "$INTERNAL_SERVICE_SECRET" \
        --model gpt-4o-mini --allow-real-provider
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.stderr.write("ERROR: httpx required (pip install httpx)\n")
    sys.exit(3)


GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Check:
    def __init__(self) -> None:
        self.hard_fail = False
        self.warned = False

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  {GREEN}[ OK ]{RST} {name}" + (f"  {DIM}{detail}{RST}" if detail else ""))

    def warn(self, name: str, detail: str = "") -> None:
        self.warned = True
        print(f"  {YEL}[WARN]{RST} {name}" + (f"  {DIM}{detail}{RST}" if detail else ""))

    def fail(self, name: str, detail: str = "") -> None:
        self.hard_fail = True
        print(f"  {RED}[FAIL]{RST} {name}" + (f"  {DIM}{detail}{RST}" if detail else ""))


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def _check_reachable(c: Check, client: httpx.AsyncClient, label: str,
                           url: str, hard: bool) -> None:
    try:
        r = await client.get(url, timeout=5.0)
        if r.status_code < 500:
            c.ok(f"{label} reachable", f"{url} -> {r.status_code}")
        else:
            (c.fail if hard else c.warn)(f"{label} returned {r.status_code}", url)
    except Exception as exc:  # noqa: BLE001
        (c.fail if hard else c.warn)(f"{label} unreachable",
                                     f"{url} ({type(exc).__name__})")


async def _single_stream(c: Check, client: httpx.AsyncClient,
                         args: argparse.Namespace) -> None:
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-User-ID": f"{args.user_prefix}preflight-0001",
    }
    if args.internal_service_secret:
        headers["X-Internal-Service-Secret"] = args.internal_service_secret
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with one short sentence. preflight probe."}],
        "stream": True, "max_tokens": 16,
    }
    try:
        first = None
        got = 0
        import time
        t0 = time.perf_counter()
        async with client.stream("POST", args.url, json=body, headers=headers,
                                 timeout=30.0) as resp:
            if resp.status_code != 200:
                await resp.aread()
                c.fail("single streaming request",
                       f"status {resp.status_code}: {resp.text[:160]}")
                return
            async for line in resp.aiter_lines():
                if line:
                    if first is None:
                        first = time.perf_counter() - t0
                    got += 1
        c.ok("single streaming request",
             f"ttft={first:.3f}s, {got} SSE lines")
    except Exception as exc:  # noqa: BLE001
        c.fail("single streaming request", f"{type(exc).__name__}: {exc}")


async def _burst(client, args, user_id, secret, n, distinct_ids=False):
    """Fire n quick requests; return status_counts.

    distinct_ids=False: every request uses the SAME ``user_id``.
    distinct_ids=True:  each request uses ``user_id-<i>`` — used to prove that
    a spoofed (no-secret) X-User-ID is IGNORED: if distinct spoofed ids
    collapse onto one bare-key bucket they 429 after the limit; if the fix
    were broken each would get its own bucket and none would 429.
    """
    body = {"model": args.model,
            "messages": [{"role": "user", "content": "preflight rl probe"}],
            "stream": False, "max_tokens": 1}
    counts: dict[int, int] = {}

    async def one(i):
        headers = {
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
            "X-User-ID": f"{user_id}-{i}" if distinct_ids else user_id,
        }
        if secret:
            headers["X-Internal-Service-Secret"] = secret
        try:
            r = await client.post(args.url, json=body, headers=headers, timeout=20.0)
            counts[r.status_code] = counts.get(r.status_code, 0) + 1
        except Exception:
            counts[0] = counts.get(0, 0) + 1

    await asyncio.gather(*[one(i) for i in range(n)])
    return counts


async def _probe_rate_limit(c: Check, client: httpx.AsyncClient,
                            args: argparse.Namespace) -> None:
    """Verify the per-user keying fix is ACTIVE.

    Strategy: exhaust user A's per-key window (probe_key_limit + buffer), then
    check:
      * a DIFFERENT trusted X-User-ID (B) is still admitted  -> per-user split.
      * a SPOOFED X-User-ID (no internal secret) shares A's exhausted bucket
        -> 429  -> spoof correctly ignored.

    Requires the per-key limit to be small for a cheap probe — set
    RATE_LIMIT_KEY_PER_MINUTE low via the loadtest override (see docs) and pass
    --probe-key-limit to match.
    """
    if not args.internal_service_secret:
        c.warn("rate-limit per-user keying probe SKIPPED",
               "no --internal-service-secret given; keying cannot be trusted")
        return

    burst = args.probe_key_limit + 5
    import time
    tag = f"rlprobe-{int(time.time())}"
    secret = args.internal_service_secret

    # --- Property 1: spoofed X-User-ID is IGNORED (falls back to bare key) ---
    # Fire `burst` requests with DISTINCT spoofed ids and NO secret. If the fix
    # works they all hit the single bare ``api_key.key_id`` bucket and 429 once
    # past the per-key limit. If it were broken (X-User-ID honoured without the
    # secret) each distinct id would get its own bucket and NONE would 429.
    spoof_counts = await _burst(
        client, args, f"{args.user_prefix}{tag}-spoof", "", burst,
        distinct_ids=True)
    spoof_429 = spoof_counts.get(429, 0)
    spoof_200 = spoof_counts.get(200, 0)
    if spoof_429 > 0:
        c.ok("spoofed X-User-ID correctly ignored",
             f"{burst} distinct spoofed ids (no secret) collapsed to ONE "
             f"shared bucket -> {spoof_200}x200 + {spoof_429}x429")
    else:
        c.fail("spoofed X-User-ID NOT ignored",
               f"distinct spoofed ids did not 429 (counts={spoof_counts}) — "
               f"is --probe-key-limit ({args.probe_key_limit}) >= server "
               f"RATE_LIMIT_KEY_PER_MINUTE, or is keying leaking to untrusted?")

    # --- Property 2: trusted distinct users get SEPARATE buckets -------------
    # The bare key bucket is now exhausted by the spoof burst. A TRUSTED user
    # (secret + fresh id) must still be admitted from its own per-user bucket.
    trusted_counts = await _burst(
        client, args, f"{args.user_prefix}{tag}-trusted-A", secret, 3)
    trusted_ok = trusted_counts.get(200, 0)
    if trusted_ok > 0:
        c.ok("trusted per-user keying ACTIVE",
             f"trusted user admitted ({trusted_ok}x200) despite the shared "
             f"bucket being exhausted -> separate per-user counter")
    else:
        c.fail("trusted per-user keying NOT active",
               f"trusted user was NOT admitted (counts={trusted_counts}) — "
               f"shared-key collision; check backend INTERNAL_SERVICE_SECRET")


async def run(args: argparse.Namespace) -> int:
    c = Check()
    origin = _origin(args.url)

    print("\n=== SRAN Gateway load-test PREFLIGHT ===\n")

    # 1. Env / args
    print("[1] Environment")
    if args.api_key:
        c.ok("GATEWAY_API_KEY present", f"{args.api_key[:6]}…")
    else:
        c.fail("GATEWAY_API_KEY missing", "pass --api-key or set env")
    if args.internal_service_secret:
        c.ok("INTERNAL_SERVICE_SECRET provided to harness",
             "trusted X-User-ID per-user keying will be exercised")
    else:
        c.warn("INTERNAL_SERVICE_SECRET not provided",
               "VUs will share ONE key bucket; per-user keying NOT exercised")

    # 2. Reachability
    print("\n[2] Endpoint reachability")
    await _check_reachable(c, httpx.AsyncClient(), "gateway health",
                           f"{origin}/__gw/health", hard=True)
    if args.metrics_url:
        await _check_reachable(c, httpx.AsyncClient(), "backend /metrics",
                               args.metrics_url, hard=False)
    if args.gateway_metrics_url:
        await _check_reachable(c, httpx.AsyncClient(), "gateway /__gw/metrics",
                               args.gateway_metrics_url, hard=False)
    if args.prometheus_url:
        await _check_reachable(c, httpx.AsyncClient(), "prometheus",
                               f"{args.prometheus_url}/-/healthy", hard=False)
    if args.ner_health_url:
        await _check_reachable(c, httpx.AsyncClient(), "ner-service health",
                               args.ner_health_url, hard=False)

    # 3. Provider cost warning
    print("\n[3] Provider")
    mockish = ("mock" in args.model.lower()) or args.assume_mock
    if mockish:
        c.ok("provider", f"model '{args.model}' treated as mock/no-cost")
    elif args.allow_real_provider:
        c.warn("REAL provider in use",
               f"model '{args.model}' will incur real API cost; "
               f"--allow-real-provider acknowledged")
    else:
        c.fail("real provider not acknowledged",
               f"model '{args.model}' is a real provider. Re-run with "
               f"--allow-real-provider, or wire the mock provider (see docs)")

    # 4. Single streaming request
    print("\n[4] Single streaming request")
    if args.api_key and (mockish or args.allow_real_provider):
        await _single_stream(c, httpx.AsyncClient(), args)
    else:
        c.warn("single streaming request skipped",
               "blocked by earlier failure (api key / provider ack)")

    # 5. Rate-limit per-user keying (opt-in burst)
    print("\n[5] Per-user rate-limit fix")
    if args.probe_rate_limit and args.api_key and (mockish or args.allow_real_provider):
        await _probe_rate_limit(c, httpx.AsyncClient(), args)
    else:
        c.warn("rate-limit probe skipped",
               "pass --probe-rate-limit (and set a low RATE_LIMIT_KEY_PER_MINUTE) "
               "to actively verify per-user split + spoof rejection")

    # 6. NER note
    print("\n[6] NER")
    c.warn("NER state not auto-detected",
           "NER on/off is a server-side tenant/DLP-profile setting. First "
           "baseline should run NER off/default — confirm via docs/runbook.")

    print("\n=== RESULT ===")
    if c.hard_fail:
        print(f"{RED}PREFLIGHT FAILED — do not start the load test.{RST}\n")
        return 1
    if c.warned:
        print(f"{YEL}PREFLIGHT PASSED WITH WARNINGS — review above.{RST}\n")
        return 0
    print(f"{GREEN}PREFLIGHT PASSED.{RST}\n")
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(description="Load-test preflight checks.")
    p.add_argument("--url",
                   default=os.environ.get(
                       "LOADTEST_URL",
                       "http://127.0.0.1:28080/v1/chat/completions"))
    p.add_argument("--api-key", default=os.environ.get("GATEWAY_API_KEY", ""))
    p.add_argument("--internal-service-secret",
                   default=os.environ.get("INTERNAL_SERVICE_SECRET", ""))
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--user-prefix", default="loadtest-user-")
    p.add_argument("--metrics-url",
                   default=os.environ.get("BACKEND_METRICS_URL",
                                          "http://localhost:8081/metrics"))
    p.add_argument("--gateway-metrics-url",
                   default=os.environ.get("GATEWAY_METRICS_URL",
                                          "http://127.0.0.1:28080/__gw/metrics"))
    p.add_argument("--prometheus-url",
                   default=os.environ.get("PROMETHEUS_URL",
                                          "http://localhost:9091"))
    p.add_argument("--ner-health-url",
                   default=os.environ.get("NER_HEALTH_URL",
                                          "http://localhost:8001/health"))
    p.add_argument("--allow-real-provider", action="store_true")
    p.add_argument("--assume-mock", action="store_true",
                   help="Treat the model as mock/no-cost (e.g. provider "
                        "overrides point at the mock service).")
    p.add_argument("--probe-rate-limit", action="store_true")
    p.add_argument("--probe-key-limit", type=int, default=60,
                   help="Server's current RATE_LIMIT_KEY_PER_MINUTE.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args(sys.argv[1:]))))
