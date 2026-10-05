"""AM10 — the input for reproducing the re-queue bug on the version before this card.

The report agent's sample ledger (GL_2569-09.xlsx, dates typed with a พ.ศ. year) with every seventh
date typed with a ค.ศ. year instead — the same days, so nothing about the figures changes. On a
ledger like that the agent asks one question that makes nobody wait ("ปี พ.ศ./ค.ศ.": how the report
should write the year) and delivers on its default. Answering that card after the files are
delivered is the path the bug is on.

    python scripts/probes/AM10/repro_requeue_input.py      → results/repro/GL_2569-09_AM10-mixed-years.xlsx
"""

from __future__ import annotations

import os

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SRC = os.path.join(ROOT, "backend", "agent_market", "packages", "financial_report", "sample-data", "GL_2569-09.xlsx")
OUT = os.path.join(HERE, "results", "repro")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    wb = openpyxl.load_workbook(SRC)
    ws = wb.active
    changed = total = 0
    for i, cell in enumerate(ws["A"][5:]):  # the rows under the header (row 5)
        text = cell.value
        if not isinstance(text, str) or not text.endswith("/2569"):
            continue
        total += 1
        if i % 7 == 0:
            cell.value = text[:-4] + "2026"
            changed += 1
    path = os.path.join(OUT, "GL_2569-09_AM10-mixed-years.xlsx")
    wb.save(path)
    print(f"{os.path.relpath(path, ROOT)} · {total} dated rows, {changed} typed with a ค.ศ. year")


if __name__ == "__main__":
    main()
