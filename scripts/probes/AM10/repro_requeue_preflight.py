"""AM10 — what the report agent asks about the reproduction's ledger, before any live round is spent.

Runs the agent's own parse and preflight steps on /in/<file> (in the sandbox image, no network, with
the backend of whichever checkout is mounted at /src — the commit live runs, for the reproduction)
and prints each question with its severity. The reproduction needs exactly one question, and it must
be one that makes nobody wait ("cosmetic"); anything else is exit 1.

    docker run --rm --network none -v <checkout>/backend:/src:ro -v <checkout>/services/chat-edge-api/scripts:/sandbox/scripts:ro \
      -v <dir with the file>:/in:ro -v scripts/probes/AM10:/am10:ro --entrypoint python3 ai-gateway-sandbox:latest \
      /am10/repro_requeue_preflight.py <file name> [more files…]
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path[0:0] = ["/src", "/sandbox/scripts"]

from agent_market.tests.invoicekit import Job  # noqa: E402


def main(names: list[str]) -> int:
    job = Job()
    try:
        parsed = []
        for i, name in enumerate(names):
            inp = f"in_{i:02d}{os.path.splitext(name)[1]}"
            shutil.copy(os.path.join("/in", name), os.path.join(job.inp, inp))
            job.run({"op": "parse", "package": "financial_report", "file": {"name": name, "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
        pre = job.run({"op": "preflight", "package": "financial_report", "parsed": parsed, "defaults": {}, "answers": {},
                       "message": "รายงานผลประกอบการเดือนกันยายน 2569", "today": "2026-10-05"})
    finally:
        job.close()
    findings = pre.get("findings") or []
    for f in findings:
        print(f"{f['severity']}\t{f['code']}\t{f['header']}\t{f['question'][:110]}")
    waits = [f for f in findings if f["severity"] != "cosmetic"]
    print(f"questions {len(findings)} · that make the round wait {len(waits)} · period {pre.get('period')}")
    return 0 if len(findings) == 1 and not waits else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
