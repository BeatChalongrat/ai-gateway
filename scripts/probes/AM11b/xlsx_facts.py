# -*- coding: utf-8 -*-
"""AM11b E — what a finished workbook holds, read from the file itself.

    docker run --rm --network none -v <dir>:/in:ro --entrypoint python3 ai-gateway-sandbox:latest /in/xlsx_facts.py /in/<file.xlsx>

(the sandbox image has openpyxl; nothing is installed on the host and nothing reaches the network.)
Prints one JSON object, ASCII-escaped: the sheets, and — from the expense audit's claims sheet — each
claim's id, its row in the source file, its amount and its result, top to bottom.
"""
import json
import sys

from openpyxl import load_workbook

path = sys.argv[1]
wb = load_workbook(path, data_only=True)
out = {"file": path.split("/")[-1], "sheets": wb.sheetnames}
if "รายการเบิก" in wb.sheetnames:
    ws = wb["รายการเบิก"]
    head_at, cols = None, {}
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=12, values_only=True), start=1):
        names = [str(v).strip() if v is not None else "" for v in row]
        if "เลขที่เบิก" in names and "จำนวนเงิน" in names:
            head_at, cols = r, {n: i for i, n in enumerate(names) if n}
            break
    # each claim's row in the file that was sent is on the companion sheet of evidence, beside its id
    source = {}
    for name in wb.sheetnames:
        if not name.startswith("รายการเบิก"):
            continue
        for r, row in enumerate(wb[name].iter_rows(min_row=1, max_row=12, values_only=True), start=1):
            names = [str(v).strip() if v is not None else "" for v in row]
            if "เลขที่เบิก" in names and "แถวในไฟล์" in names:
                a, b = names.index("เลขที่เบิก"), names.index("แถวในไฟล์")
                for line in wb[name].iter_rows(min_row=r + 1, values_only=True):
                    if line[a] not in (None, ""):
                        source[str(line[a])] = line[b]
                break
    claims = []
    if head_at:
        for row in ws.iter_rows(min_row=head_at + 1, values_only=True):
            if row[cols["เลขที่เบิก"]] in (None, ""):
                continue
            cid = str(row[cols["เลขที่เบิก"]])
            claims.append({"id": cid, "source_row": source.get(cid), "amount": row[cols["จำนวนเงิน"]],
                           "result": row[cols["ผลการตรวจ"]] if "ผลการตรวจ" in cols else None})
    out["claims"] = claims
print(json.dumps(out, ensure_ascii=True, default=str))
