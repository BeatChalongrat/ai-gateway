# -*- coding: utf-8 -*-
"""AM11b E — what a finished file holds, read from the file itself (standard library only).

    python docx_facts.py <file.docx|file.pptx>      → one JSON object on stdout (ASCII-escaped)

.docx: its top headings in order, how many pictures it holds, each table's header row, and — for the
account detail table — the "ต่างจากงบ" column as numbers, top to bottom.
.pptx: how many slides, how many charts.

The walk of the built app (walk.js `asked`) downloads what the agent made and reads it with this: an
instruction counts as honoured when the file says so, not when the thread does.
"""
import json
import re
import sys
import zipfile

path = sys.argv[1]
out = {"file": path.replace("\\", "/").split("/")[-1]}
with zipfile.ZipFile(path) as z:
    names = z.namelist()
    if path.lower().endswith(".pptx"):
        out["slides"] = len([n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)])
        out["charts"] = len([n for n in names if re.match(r"ppt/charts/chart\d+\.xml$", n)])
    else:
        xml = z.read("word/document.xml").decode("utf-8")
        out["pictures"] = len([n for n in names if n.startswith("word/media/")])

        def text(part):
            return "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", part)).strip()

        body = re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S)
        out["headings"] = [text(p) for p in re.findall(r"<w:p[ >].*?</w:p>", body, flags=re.S)
                           if re.search(r'<w:pStyle w:val="Heading1"', p)]
        tables = []
        for t in re.findall(r"<w:tbl>.*?</w:tbl>", xml, flags=re.S):
            rows = [[text(c) for c in re.findall(r"<w:tc>.*?</w:tc>", r, flags=re.S)]
                    for r in re.findall(r"<w:tr[ >].*?</w:tr>", t, flags=re.S)]
            if rows:
                tables.append(rows)
        out["table_heads"] = [t[0] for t in tables]
        out["paragraphs"] = len(re.findall(r"<w:p[ >]", xml))

        def money(cell):
            c = re.sub(r"\s+", "", cell)
            neg = c.startswith("(") and c.endswith(")")
            c = c.strip("()").replace(",", "")
            try:
                v = float(c)
            except ValueError:
                return None
            return -v if neg else v

        for t in tables:
            head = t[0]
            if head[:2] == ["รหัส", "ชื่อบัญชี"] and "ต่างจากงบ" in head:
                col = head.index("ต่างจากงบ")
                out["accounts"] = {"rows": len(t) - 1, "codes": [r[0] for r in t[1:]],
                                   "var_budget": [money(r[col]) for r in t[1:]]}
print(json.dumps(out, ensure_ascii=True))
