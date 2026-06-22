#!/usr/bin/env python3
"""
Live smoke test for Replicate video adapter.

⚠️ THIS SCRIPT SPENDS REAL MONEY (~$0.35 per run with default settings).

USAGE:
    python scripts/replicate_live_smoke.py --confirm-spend

REQUIRES:
    - REPLICATE_API_TOKEN env var set
    - Backend running on localhost:8081 with REPLICATE_API_TOKEN configured
    - DEV_AUTH_TOKEN env var (a bearer token for a dev tenant with sufficient balance)
    - Sprint 2B migrations applied (058 + 059)

EXIT CODES:
    0 = success, MP4 saved + verified
    1 = generic error / aborted
    2 = REPLICATE_API_TOKEN missing or invalid (provider returned 401)
    3 = content policy block (provider returned 422)
    4 = timeout (>300s without completion)
    5 = budget cap hit (L1 or L2 guard blocked)
    6 = output validation failed (size or magic bytes)
"""

import argparse
import asyncio
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8081")
MODEL_ID = os.environ.get("SMOKE_MODEL", "replicate-kling-v1.5-pro")
PROMPT = os.environ.get("SMOKE_PROMPT", "a cat playing piano")
DURATION_SEC = int(os.environ.get("SMOKE_DURATION", "5"))
OUTPUT_PATH = Path(os.environ.get("SMOKE_OUTPUT", "/tmp/smoke_output.mp4"))
POLL_INTERVAL_SEC = 10
MAX_POLL_SEC = 300
MIN_FILE_SIZE = 100 * 1024     # 100 KB
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


def fail(code: int, msg: str) -> None:
    print(f"\n❌ FAIL ({code}): {msg}", file=sys.stderr)
    sys.exit(code)


def preflight() -> str:
    """Returns dev auth token or aborts."""
    if not os.environ.get("REPLICATE_API_TOKEN"):
        fail(2, "REPLICATE_API_TOKEN not set")

    token = os.environ.get("DEV_AUTH_TOKEN")
    if not token:
        fail(1, "DEV_AUTH_TOKEN not set (need a bearer for dev tenant)")

    return token


def confirm_spend(estimated_usd: Decimal) -> None:
    """Block-and-wait stdin confirmation. Case-sensitive 'yes'."""
    print(f"\n⚠️  This will spend approximately ${estimated_usd} on Replicate.")
    print(f"   Model: {MODEL_ID}")
    print(f"   Prompt: {PROMPT}")
    print(f"   Duration: {DURATION_SEC}s")
    print(f"\nType 'yes' (lowercase) to proceed: ", end="", flush=True)
    answer = sys.stdin.readline().strip()
    if answer != "yes":
        fail(1, f"Confirmation rejected (got: {answer!r})")


async def submit(token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{BACKEND_URL}/v1/videos/generations",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "prompt": PROMPT,
                "model": MODEL_ID,
                "duration_sec": DURATION_SEC,
            },
        )
        if r.status_code == 402:
            fail(5, f"Budget guard rejected: {r.text}")
        if r.status_code != 200:
            fail(1, f"Submit failed {r.status_code}: {r.text}")
        return r.json()


async def poll_until_done(token: str, job_id: str) -> dict:
    deadline = time.time() + MAX_POLL_SEC
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() < deadline:
            r = await client.get(
                f"{BACKEND_URL}/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            pct = data.get("progress_pct")
            print(f"  [{int(time.time() - (deadline - MAX_POLL_SEC)):>3}s] status={status} progress={pct}")

            if status == "done":
                return data
            if status == "failed":
                err = data.get("error_message", "unknown")
                if "401" in err or "auth" in err.lower():
                    fail(2, f"Provider auth failed: {err}")
                if "422" in err or "policy" in err.lower():
                    fail(3, f"Content policy block: {err}")
                fail(1, f"Job failed: {err}")

            await asyncio.sleep(POLL_INTERVAL_SEC)
    fail(4, f"Timeout after {MAX_POLL_SEC}s")


async def download_and_verify(token: str, output_url: str) -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(
            output_url if output_url.startswith("http") else f"{BACKEND_URL}{output_url}",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.content

    size = len(data)
    if size < MIN_FILE_SIZE:
        fail(6, f"Output too small: {size} bytes")
    if size > MAX_FILE_SIZE:
        fail(6, f"Output too large: {size} bytes")

    # MP4 magic bytes: '....ftyp' at offset 4 (ISO Base Media File Format)
    if len(data) < 12 or data[4:8] != b"ftyp":
        fail(6, f"Not a valid MP4 (magic bytes: {data[:12].hex()})")

    OUTPUT_PATH.write_bytes(data)
    return size


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-spend", action="store_true",
                        help="Required flag to acknowledge real money will be spent")
    args = parser.parse_args()

    if not args.confirm_spend:
        print("⚠️  This script spends real money. Pass --confirm-spend to proceed.", file=sys.stderr)
        return 1

    token = preflight()

    # Pre-estimate (approximate — actual computed server-side)
    estimated = Decimal("0.07") * DURATION_SEC  # Kling default
    confirm_spend(estimated)

    print("\n🚀 Submitting...")
    submit_response = await submit(token)
    job_id = submit_response["job_id"]
    actual_estimate_usd = submit_response.get("estimated_cost_usd", "?")
    print(f"   Job ID: {job_id}")
    print(f"   Server estimate: {actual_estimate_usd}")

    print("\n⏳ Polling...")
    done = await poll_until_done(token, job_id)

    output_url = done.get("output_url")
    if not output_url:
        fail(1, "Job done but no output_url returned")

    print(f"\n⬇️  Downloading {output_url}")
    size = await download_and_verify(token, output_url)

    actual_cost = done.get("actual_cost_usd", "?")
    print(f"\n✅ SUCCESS")
    print(f"   File: {OUTPUT_PATH} ({size:,} bytes)")
    print(f"   Actual cost: {actual_cost}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
