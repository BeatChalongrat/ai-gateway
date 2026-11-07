#!/usr/bin/env python3
"""Basic load test — concurrent requests to measure stability and latency."""

import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import json

# Read admin key (set ADMIN_API_KEY in the env, or fall back to a local file)
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
if not ADMIN_KEY:
    env_path = os.path.join(os.path.dirname(__file__), "..", "admin-ui", ".env.local")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("ADMIN_API_KEY="):
                ADMIN_KEY = line.split("=", 1)[1].strip()

HOST = "127.0.0.1"
PORT = 8080
ENDPOINTS = ["/health", "/admin/tenants", "/admin/models", "/admin/providers", "/admin/policies"]
CONCURRENT = 4
ROUNDS = 5


def fetch(path):
    """Single synchronous request."""
    t0 = time.perf_counter()
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
        headers = {"Connection": "close"}
        if ADMIN_KEY and "/admin/" in path:
            headers["Authorization"] = f"Bearer {ADMIN_KEY}"
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        resp.read()
        ms = round((time.perf_counter() - t0) * 1000)
        status = resp.status
        conn.close()
        return {"path": path, "status": status, "ms": ms}
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"path": path, "status": 0, "ms": ms, "err": str(e)[:80]}


def main():
    total = CONCURRENT * ROUNDS
    print(f"Load test: {CONCURRENT} concurrent x {ROUNDS} rounds = {total} requests")
    print(f"Endpoints: {', '.join(ENDPOINTS)}")
    print(f"Admin key: {'set' if ADMIN_KEY else 'NOT SET'}")
    print()

    results = []

    for round_num in range(ROUNDS):
        # Build work items for this round
        work = []
        for ep in ENDPOINTS:
            count = max(1, CONCURRENT // len(ENDPOINTS))
            work.extend([ep] * count)

        with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
            futures = [pool.submit(fetch, path) for path in work]
            for f in as_completed(futures):
                results.append(f.result())

        ok_so_far = sum(1 for r in results if r["status"] == 200)
        print(f"  Round {round_num + 1}/{ROUNDS}: {ok_so_far}/{len(results)} OK")

    ok = [r for r in results if r["status"] == 200]
    err = [r for r in results if r["status"] != 200]
    times = [r["ms"] for r in ok]

    print()
    print("=" * 50)

    if times:
        times_sorted = sorted(times)
        avg = round(sum(times) / len(times))
        p50 = times_sorted[len(times) // 2]
        p95 = times_sorted[int(len(times) * 0.95)]
        p99 = times_sorted[min(int(len(times) * 0.99), len(times) - 1)]
        print(f"  OK: {len(ok)} / {len(results)}  ({len(ok) * 100 // len(results)}%)")
        print(f"  Errors: {len(err)}")
        print(f"  Avg: {avg}ms | P50: {p50}ms | P95: {p95}ms | P99: {p99}ms")
    else:
        print(f"  OK: 0 / {len(results)}  (0%)")

    if err:
        samples = []
        for r in err[:3]:
            if "err" in r:
                samples.append(r["err"])
            else:
                samples.append(f"HTTP {r['status']}")
        print(f"  Sample errors: {samples}")

    error_rate = len(err) / len(results) if results else 1
    if error_rate > 0.1:
        print(f"\n  RESULT: FAIL (error rate {error_rate:.0%})")
        sys.exit(1)
    elif times and p95 > 5000:
        print("\n  RESULT: WARN (P95 > 5s)")
    else:
        print("\n  RESULT: PASS")

    print("=" * 50)


if __name__ == "__main__":
    main()
