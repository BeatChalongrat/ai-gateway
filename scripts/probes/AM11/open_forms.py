"""AM11 (D) — a form and its filled copy, opened and read side by side.

For every pair <name>.docx|.xlsx (the form as the customer made it) and its filled copy, prints each
place of the form that holds a label with what is beside or under it — in the source, and in the filled
file — and what the filled file's blanks still hold. Nothing here is the filler's word: both files are
opened with the document libraries and read.

    python3 open_forms.py <source file> <filled file> [...more pairs]

Runs in the sandbox image (python-docx, openpyxl).
"""
import sys

import openpyxl
from docx import Document


def docx_rows(path):
    doc = Document(path)
    out = []
    for ti, table in enumerate(doc.tables):
        for ri, row in enumerate(table.rows):
            out.append((f"table{ti} row{ri}", [c.text for c in row.cells]))
    out.append(("paragraphs", [p.text for p in doc.paragraphs if p.text.strip()]))
    return out


def xlsx_rows(path):
    wb = openpyxl.load_workbook(path)
    out = []
    for ws in wb.worksheets:
        for r in range(1, ws.max_row + 1):
            cells = [(c.coordinate, c.value) for c in ws[r] if c.value is not None]
            out.append((f"{ws.title} row {r}", [f"{ref}={value!r}" for ref, value in cells]))
    return out


def read(path):
    return docx_rows(path) if path.lower().endswith(".docx") else xlsx_rows(path)


def main(args):
    sys.stdout.reconfigure(encoding="utf-8")
    for src, dst in zip(args[0::2], args[1::2]):
        print(f"== {src.rsplit('/', 1)[-1]}  →  {dst.rsplit('/', 1)[-1]}")
        before, after = read(src), read(dst)
        filled = dict(after)
        for where, a in before:
            b = filled.get(where, a)
            if not a and not b:
                continue
            print(f" {' ' if a == b else '*'} {where}")
            print(f"     form   : {a}")
            if a != b:
                print(f"     filled : {b}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
