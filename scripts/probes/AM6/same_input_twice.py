"""AM6 gate — the same input and context twice give the same figures, the same matches and the same
questions. (AM8: what is compared is below — not the Office files' bytes, whose packaging carries
times and metadata; the earlier "byte-identical files" claim was wrong.)

Run inside the sandbox image by same_input_twice.sh, once per pass, each pass
a process of its own with a hash seed of its own:

    python same_input_twice.py <out dir>

For every case of the invoice ↔ PO agent's eval set and of the bank
reconciliation's that reaches a result it writes, under <out dir>:

    <agent>.<case>.computed.json   the computed table exactly as the compute step wrote it
    <agent>.<case>.matching.json   the matching results: every link (its pass, its tier, who
                                   confirmed it), every invoice's status and reason codes, every
                                   line's match depth and cumulative figures — or, for the bank,
                                   its four matching groups
    <agent>.<case>.cards.txt       the key of every card that was asked, in order
    <agent>.<case>.cells.json      every cell of the working paper

The shell script then compares the two passes with cmp and diff.
"""

import json
import os
import sys

from agent_market.tests import bankkit, pokit


def matching_of(agent: str, computed: dict):
    if agent == "bank":
        return computed["match"]["groups"]
    return {
        "links": computed["match"]["links"], "tiers": computed["match"]["tiers"], "passes": computed["match"]["passes"],
        "invoices": [{k: i[k] for k in ("key", "number", "status", "reasons", "depths", "duplicate_of", "corrects", "counted")}
                     for i in computed["invoices"]],
        "lines": [{k: x.get(k) for k in ("id", "invoice", "status", "reasons", "depth", "depth_why", "link", "cumulative", "checks",
                                         "tolerances", "price_variance", "unplanned", "counted")} for x in computed["lines"]],
        "po_lines": computed["po_lines"], "grir": computed["grir"], "index_entries": computed["index_entries"],
    }


def main(out: str) -> int:
    os.makedirs(out, exist_ok=True)
    n = 0
    for agent, kit in (("po", pokit), ("bank", bankkit)):
        for case in kit.load_cases()["cases"]:
            res = kit.run_case(case)
            base = os.path.join(out, f"{agent}.{case['id']}")
            with open(base + ".cards.txt", "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(c["key"] for c in res["asked"]) + "\n")
            if res.get("stops_at"):
                continue
            with open(base + ".computed.json", "wb") as f:
                f.write(res["computed_bytes"])
            with open(base + ".matching.json", "w", encoding="utf-8", newline="\n") as f:
                json.dump(matching_of(agent, res["computed"]), f, ensure_ascii=False, indent=1)
            with open(base + ".cells.json", "w", encoding="utf-8", newline="\n") as f:
                json.dump(res["sheets"], f, ensure_ascii=False, indent=1, default=str)
            n += 1
    sys.stdout.write(f"{n} cases written (PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
