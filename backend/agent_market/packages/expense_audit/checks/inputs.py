"""What a run's uploads are: the claims sheet, the receipts, and a KB policy document (context only).

The parse step reads each upload once and stores what it read (parsed.json, format
``am.expense.v1``); preflight and compute judge the stored reading. The runner tells the parse
step nothing about a file's role, so the role comes from the file itself:

  * .xlsx/.xlsm/.csv with a claims header (claim id, employee, date, category, amount…) → ``claims``;
    a policy sheet → ``other`` (a policy sheet is read into the install's table at install time,
    never in a run); anything else → ``other``;
  * .pdf/.jpg/.jpeg/.png → ``receipt``, read by the E2 engine (engine/e2) exactly as Agent 1 reads a
    purchase document: e-Tax XML → PDF text → OCR; a vision model may later propose values for a
    scan or photo, never decide one;
  * .docx/.txt/.md → ``policy_document``: paragraphs kept verbatim, for context only.

A claims row keeps every cell as the sheet had it (``*_raw``) next to the parsed value, so an
exception can point at the sheet, the row and what was written there.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ....engine import thai as TH
from ....engine.e2 import thai_text as T
from ....engine.tabular import col_letter, read_workbook, sha256_file

FORMAT = "am.expense.v1"
RECEIPT_EXT = (".pdf", ".jpg", ".jpeg", ".png")
SHEET_EXT = (".xlsx", ".xlsm", ".csv")
TEXT_EXT = (".docx", ".txt", ".md")
Q2 = Decimal("0.01")

# claims-sheet columns: a header cell (spaces removed, lower case) → the field it holds
HEADERS = {
    "id": ("เลขที่เบิก", "เลขที่ใบเบิก", "เลขที่รายการ", "รหัสรายการ", "เลขที่", "claimid", "claimno", "claimnumber", "claim"),
    "employee_id": ("รหัสพนักงาน", "เลขพนักงาน", "employeeid", "empid", "staffid"),
    "employee_name": ("ชื่อพนักงาน", "ชื่อ-สกุล", "ชื่อสกุล", "ชื่อ-นามสกุล", "พนักงาน", "ผู้เบิก", "employee", "employeename",
                      "staff", "name"),
    "date": ("วันที่", "วันที่จ่าย", "วันที่ใช้จ่าย", "วันที่เกิดค่าใช้จ่าย", "date", "expensedate"),
    "category": ("หมวด", "หมวดค่าใช้จ่าย", "ประเภท", "ประเภทค่าใช้จ่าย", "category", "type", "expensetype"),
    "merchant": ("ร้านค้า/ผู้รับเงิน", "ร้านค้า", "ผู้รับเงิน", "ผู้ขาย", "ร้าน", "merchant", "vendor", "payee", "supplier"),
    "amount": ("จำนวนเงิน", "ยอดเงิน", "ยอดเบิก", "จำนวนเงินที่เบิก", "amount", "total"),
    "receipt": ("ใบเสร็จ", "ไฟล์ใบเสร็จ", "ไฟล์แนบ", "receipt", "receiptfile", "attachment"),
    "vat_claim": ("ขอคืนภาษีซื้อ", "ขอคืนvat", "ขอคืนภาษี", "vatclaim", "claimvat", "inputvat"),
    "wht_deducted": ("หักณที่จ่าย", "ภาษีหักณที่จ่าย", "หักภาษีณที่จ่าย", "wht", "whtdeducted", "withholding", "withholdingtax"),
    "description": ("รายละเอียด", "วัตถุประสงค์", "คำอธิบาย", "รายการ", "description", "purpose", "memo", "details"),
}
NEEDED = ("date", "category", "amount")
POLICY_SHEET_MARKS = ("วงเงินต่อครั้ง", "วงเงินต่อคนต่อเดือน", "per_claim", "per_person_month")
YES = ("ใช่", "yes", "y", "true", "1", "x", "✓", "✔", "/", "มี", "ขอ", "ขอคืน")


@dataclass
class Upload:
    """One upload as the parse step read it."""
    kind: str                 # claims | receipt | policy_document | other
    key: str
    name: str
    sha256: str
    data: dict

    @property
    def tier(self) -> str | None:
        return self.data["extraction"]["document"]["tier"] if self.kind == "receipt" else None

    @property
    def extraction(self) -> dict | None:
        return self.data.get("extraction") if self.kind == "receipt" else None

    def summary(self) -> dict:
        s = {"key": self.key, "doc_key": self.key, "name": self.name, "kind": self.kind}
        if self.kind == "claims":
            s.update({"sheet": self.data["sheet"], "header_row": self.data["header_row"], "claims": len(self.data["rows"]),
                      "columns": self.data["columns"]})
        elif self.kind == "receipt":
            x = self.data["extraction"]
            s.update({"tier": x["document"]["tier"], "method": x["method"], "pages": x["document"]["pages"],
                      "document_type": x["document_type"]["normalized"], "triage_only": x["document"]["triage_only"]})
        elif self.kind == "policy_document":
            s["paragraphs"] = len(self.data["paragraphs"])
        else:
            s["reason"] = self.data.get("reason")
        return s


def dump(u: Upload) -> dict:
    return {"format": FORMAT, "kind": u.kind, "key": u.key, "name": u.name, "sha256": u.sha256, "data": u.data}


def load(d: dict) -> Upload:
    if d.get("format") != FORMAT:
        raise ValueError(f"unknown parsed format {d.get('format')!r}")
    return Upload(kind=d["kind"], key=d["key"], name=d["name"], sha256=d["sha256"], data=d["data"])


def parse(path: str, name: str, mapping: dict | None = None) -> Upload:
    ext = os.path.splitext(name)[1].lower()
    sha = sha256_file(path)
    key = "d" + sha[:10]
    if ext in RECEIPT_EXT:
        from ....engine.e2.extract import extract

        out = extract(path, name)
        return Upload("receipt", key, name, sha, {"extraction": out})
    if ext in SHEET_EXT:
        return _sheet(path, name, key, sha, (mapping or {}).get("claims") or {})
    if ext in TEXT_EXT:
        return Upload("policy_document", key, name, sha, {"paragraphs": _paragraphs(path, ext)})
    return Upload("other", key, name, sha, {"reason": "unsupported_type", "detail": ext})


# ── the claims sheet ───────────────────────────────────────────────────────────

def _h(v) -> str:
    return "".join(T.arabic(str(v or "")).split()).lower().replace(".", "")


def _header(row: list, mapping: dict) -> dict:
    """{field: column}: a column the install mapped by name first, then the known header words."""
    cols: dict[str, int] = {}
    heads = [_h(c) for c in row]
    for f, header in mapping.items():
        if f in HEADERS and _h(header) in heads:
            cols[f] = heads.index(_h(header))
    for j, h in enumerate(heads):
        if not h or j in cols.values():
            continue
        for f, names in HEADERS.items():
            if f not in cols and h in names:
                cols[f] = j
                break
    return cols


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()[:10]
    return str(v).strip()


def _amount(v) -> str | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return f"{Decimal(repr(v)).quantize(Q2, rounding=ROUND_HALF_UP):.2f}"
    t = T.arabic(str(v)).replace("บาท", "").replace("฿", "").replace(" ", "").strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", t):
        return f"{Decimal(t.replace(',', '')).quantize(Q2, rounding=ROUND_HALF_UP):.2f}"
    return None


def _date(v) -> str | None:
    if isinstance(v, _dt.datetime):
        return v.date().isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if v is None or v == "":
        return None
    d = TH.parse_date(v)
    if d is None:
        hit = T.parse_date(T.arabic(str(v)))
        d = hit[0] if hit else None
    return d.isoformat() if d else None


def _yes(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return _h(v) in YES


def _sheet(path: str, name: str, key: str, sha: str, mapping: dict) -> Upload:
    wb = read_workbook(path, name)
    if wb.error:
        return Upload("other", key, name, sha, {"reason": "unreadable", "detail": wb.error})
    policy_like = False
    for sheet in wb.sheets:
        for i, row in enumerate(sheet.rows[:15]):
            cols = _header(row, mapping)
            if all(f in cols for f in NEEDED) and ("employee_id" in cols or "employee_name" in cols):
                return Upload("claims", key, name, sha, _claims(sheet, i, cols))
            if any(_h(c) in (_h(m) for m in POLICY_SHEET_MARKS) for c in row):
                policy_like = True
    if policy_like:
        return Upload("other", key, name, sha, {"reason": "policy_sheet",
                                               "detail": "ตารางนโยบายอ่านเข้า agent ตอนติดตั้งหรือในหน้าตั้งค่า รอบนี้ใช้ตารางที่ตั้งไว้"})
    return Upload("other", key, name, sha, {"reason": "unrecognised_sheet",
                                           "detail": "ไม่พบหัวคอลัมน์ของรายการเบิก (วันที่ หมวด จำนวนเงิน พนักงาน)"})


def _claims(sheet, header_at: int, cols: dict) -> dict:
    rows, skipped = [], []
    for i in range(header_at + 1, len(sheet.rows)):
        r = sheet.rows[i]

        def cell(f):
            j = cols.get(f)
            return r[j] if j is not None and j < len(r) else None
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
            continue
        amount_raw, date_raw = cell("amount"), cell("date")
        first = _text(r[0] if r else None)
        if date_raw in (None, "") and ("รวม" in first or "total" in first.lower()):
            skipped.append({"row": i + 1, "why": "total_row"})
            continue
        receipt = _text(cell("receipt")) or None
        rows.append({
            "row": i + 1, "id": _text(cell("id")) or None,
            "employee_id": _text(cell("employee_id")) or None, "employee_name": _text(cell("employee_name")) or None,
            "date": _date(date_raw), "date_raw": _text(date_raw),
            "category": _text(cell("category")), "merchant": _text(cell("merchant")),
            "amount": _amount(amount_raw), "amount_raw": _text(amount_raw),
            "receipt": os.path.basename(receipt.replace("\\", "/")) if receipt else None,
            "vat_claim": _yes(cell("vat_claim")), "vat_claim_raw": _text(cell("vat_claim")),
            "wht_deducted": _amount(cell("wht_deducted")), "wht_raw": _text(cell("wht_deducted")),
            "description": _text(cell("description")),
            "cells": {f: f"{col_letter(j)}{i + 1}" for f, j in cols.items()},
        })
    return {"sheet": sheet.name, "header_row": header_at + 1, "columns": {f: col_letter(j) for f, j in cols.items()},
            "rows": rows, "skipped": skipped}


# ── the KB policy document ───────────────────────────────────────────────────────

def _paragraphs(path: str, ext: str) -> list[dict]:
    texts: list[str] = []
    if ext == ".docx":
        from docx import Document

        doc = Document(path)
        texts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                texts.append(" | ".join(c.text for c in row.cells))
    else:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "cp874"):
            try:
                texts = raw.decode(enc).splitlines()
                break
            except UnicodeDecodeError:
                continue
    out = []
    for t in texts:
        t = " ".join(t.split())
        if t:
            out.append({"n": len(out) + 1, "text": t[:2000]})
    return out[:400]
