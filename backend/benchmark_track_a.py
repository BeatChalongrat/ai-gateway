"""Track A.6 final benchmark — concurrency ceiling + connection-release at scale.

Runs in a throwaway container on the compose network, hitting the RUNNING
(A.5-tuned) backend at http://backend:8080. Multi-key load bypasses the
60/min per-key rate cap. Samples pg_stat_activity during each tier to prove
the async connection is released during the provider call (idle-in-txn ~0).

Writes a Markdown table to /app/FINAL_BENCHMARK_RAW.md (mounted to host).
Self-limiting: bounded tiers + per-request 30s timeout.
"""
import asyncio
import time

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.services.api_key_service import ApiKeyService

BASE = "http://backend:8080/v1/chat/completions"
TIERS = [20, 40, 50, 75, 100, 150, 200]
PAYLOAD = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "reply with exactly one short word"}]}
N_KEYS = 8  # 8 × 60/min headroom for the higher tiers within the rate window


def make_keys(n):
    s = SessionLocal()
    svc = ApiKeyService()
    keys = []
    for i in range(n):
        raw, _ = svc.create_key(
            session=s, tenant_id="acme-corp-001",
            project_id="acme-internal-assistant", name=f"bench-trackA-{i}",
        )
        keys.append(raw)
    s.commit()
    s.close()
    return keys


def sample_pg():
    with engine.connect() as c:
        r = c.execute(text(
            "SELECT count(*) FILTER (WHERE state='active'), "
            "count(*) FILTER (WHERE state='idle in transaction') "
            "FROM pg_stat_activity WHERE datname='ai_gateway' "
            "AND backend_type='client backend'"
        )).one()
    return int(r[0]), int(r[1])


async def fire(client, key):
    t = time.perf_counter()
    try:
        r = await client.post(BASE, headers={"Authorization": f"Bearer {key}"}, json=PAYLOAD, timeout=30)
        return r.status_code, (time.perf_counter() - t) * 1000
    except Exception:
        return -1, (time.perf_counter() - t) * 1000


async def run_tier(keys, n):
    peak_active = 0
    peak_idletxn = 0
    stop = False

    async def sampler():
        nonlocal peak_active, peak_idletxn
        while not stop:
            try:
                a, it = await asyncio.to_thread(sample_pg)
                peak_active = max(peak_active, a)
                peak_idletxn = max(peak_idletxn, it)
            except Exception:
                pass
            await asyncio.sleep(0.25)

    async with httpx.AsyncClient() as client:
        samp = asyncio.create_task(sampler())
        results = await asyncio.gather(*[fire(client, keys[i % len(keys)]) for i in range(n)])
        stop = True
        await asyncio.sleep(0.05)
        samp.cancel()

    ok = sum(1 for c, _ in results if c == 200)
    rl = sum(1 for c, _ in results if c == 429)
    err = sum(1 for c, _ in results if c not in (200, 429))
    lats = sorted(l for c, l in results if c == 200)

    def pct(p):
        return lats[min(len(lats) - 1, int(len(lats) * p))] if lats else 0

    return dict(n=n, ok=ok, rl=rl, err=err, p50=pct(0.5), p95=pct(0.95),
                p99=pct(0.99), pa=peak_active, pit=peak_idletxn)


async def main():
    t0 = time.perf_counter()
    keys = make_keys(N_KEYS)
    rows = []
    for n in TIERS:
        if time.perf_counter() - t0 > 600:  # 10-min wall cap
            rows.append(f"| {n}+ | SKIPPED (wall-clock cap) | | | | | | | |")
            break
        r = await run_tier(keys, n)
        rows.append(
            f"| {r['n']} | {r['ok']} | {r['rl']} | {r['err']} | {r['p50']:.0f} | "
            f"{r['p95']:.0f} | {r['p99']:.0f} | {r['pa']} | {r['pit']} |"
        )
        await asyncio.sleep(3)  # let the per-key rate window breathe between tiers

    out = ["# Track A.6 — Final Benchmark (post-flip, A.5-tuned pools)", "",
           "Live, real OpenAI upstream. Multi-key (8) load. pg_stat_activity "
           "sampled during each tier (client backends on ai_gateway).", "",
           "| concurrent | 200 | 429(rate) | err | p50 ms | p95 ms | p99 ms | peak active conns | peak idle-in-txn |",
           "|---|---|---|---|---|---|---|---|---|"]
    out.extend(rows)
    out.append("")
    out.append("idle-in-txn = connections HELD mid-transaction (the H1 signal). "
               "~0 = connection released during the provider call.")
    body = "\n".join(out)
    with open("/app/FINAL_BENCHMARK_RAW.md", "w") as f:
        f.write(body)
    print(body)
    print("BENCH_DONE")


asyncio.run(main())
