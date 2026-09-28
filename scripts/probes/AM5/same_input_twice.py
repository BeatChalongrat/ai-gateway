"""AM5 gate — the same input twice gives the same output, byte for byte.

Run inside the sandbox image by same_input_twice.sh, once per pass, each pass
a process of its own with a hash seed of its own:

    python same_input_twice.py <out dir>

For every case of the bank reconciliation's eval set that reaches a statement
it writes, under <out dir>:

    <case>.computed.json   the computed table exactly as the compute step wrote it
    <case>.groups.json     its four matching groups (matched · missing · extra · suspect)
    <case>.cards.txt       the key of every card that was asked, in order
    <case>.cells.json      every cell of the working paper

The shell script then compares the two passes with cmp and diff.
"""

import json
import os
import sys

from agent_market.tests import bankkit as K


def main(out: str) -> int:
    os.makedirs(out, exist_ok=True)
    n = 0
    for case in K.load_cases()["cases"]:
        res = K.run_case(case)
        with open(os.path.join(out, case["id"] + ".cards.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(c["key"] for c in res["asked"]) + "\n")
        if res.get("stops_at"):
            continue
        with open(os.path.join(out, case["id"] + ".computed.json"), "wb") as f:
            f.write(res["computed_bytes"])
        with open(os.path.join(out, case["id"] + ".groups.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(res["computed"]["match"]["groups"], f, ensure_ascii=False, indent=1)
        with open(os.path.join(out, case["id"] + ".cells.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(res["sheets"], f, ensure_ascii=False, indent=1, default=str)
        n += 1
    sys.stdout.write(f"{n} cases written (PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
