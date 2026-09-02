#!/usr/bin/env python3
"""Prove supports_tools / supports_vision instead of trusting them (FIX47.6 Part B).

Host-side wrapper around ``backend/app/services/capability_probe.py``,
which runs INSIDE the backend container — the only place provider keys
resolve, and a path that bypasses request_logs / billing / router
priors, so probe traffic can never pollute the auto-router's ranking
data (the FIX35 trap).

    python scripts/probe_model_capabilities.py --dry-run
    python scripts/probe_model_capabilities.py
    python scripts/probe_model_capabilities.py --provider deepseek --no-vision

Output: scripts/probes/capability-flags/results/<UTC timestamp>.json
(raw per-pair outcomes — gitignored) plus the mismatch table on stdout.
The probe REPORTS; it writes nothing to the database. A human reads the
table and decides what to fix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "scripts" / "probes" / "capability-flags" / "results"
CONTAINER = "ai-gateway-backend-1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", action="append")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--max-requests", type=int)
    ap.add_argument("--container", default=CONTAINER)
    args = ap.parse_args()

    cmd = ["docker", "exec", args.container, "python", "-m", "app.services.capability_probe"]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.no_vision:
        cmd.append("--no-vision")
    for p in args.provider or []:
        cmd += ["--provider", p]
    if args.max_requests:
        cmd += ["--max-requests", str(args.max_requests)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    report = json.loads(proc.stdout)

    if args.dry_run:
        print(f"providers with valid keys: {', '.join(report['providers_with_valid_keys'])}")
        print(f"pairs: {report['pair_count']} ({report['chat_pair_count']} chat)")
        print(f"estimated max requests: {report['estimated_max_requests']} (cap {report['request_cap']})")
        for p in report["pairs"]:
            kind = "media" if p["media"] else "chat"
            print(f"  {p['provider']:12} {p['model']:44} [{kind}] stored: tools={p['stored_supports_tools']} vision={p['stored_supports_vision']}")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = OUT_DIR / f"{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"requests sent: {report['requests_sent']} / cap {report['request_cap']}")
    print(f"mismatches (stored flag vs provider behaviour): {report['mismatch_count']}")
    if report["mismatches"]:
        print(f"\n| provider | model | flag | stored | observed | outcome |")
        print(f"|---|---|---|---|---|---|")
        for m in report["mismatches"]:
            print(f"| {m['provider']} | {m['model']} | {m['flag']} | {m['stored']} | {m['observed']} | {m['outcome']} |")
    print(f"\nraw results: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
