"""What a run's uploads are: the bank's statement, the company's ledger of that account, and the
items still open from the month before.

The parse step reads each upload once and stores what it read (parsed.json, format
``am.bankrec.v1``); preflight and compute judge the stored reading. The runner tells the parse step
nothing about a file's role, so the role comes from the file itself:

  * ``bank``     a statement: ถอน/ฝาก (withdrawal/deposit) columns, a running balance, a bank's
                 name or "รายการเดินบัญชี" / "Statement" above the table;
  * ``book``     the ledger or cash book of the bank account: เลขที่เอกสาร / voucher numbers,
                 เดบิต/เครดิต, "บัญชีแยกประเภท" / "สมุดเงินฝากธนาคาร" above the table;
  * ``carried``  the list of items still open at the end of the month before — the sheet
                 "รายการค้างยกไป" of this agent's own reconciliation, or a sheet with that heading;
  * ``other``    anything else, with the reason, so that the run can say which file it did not use.

"Debit" means opposite things on the two sides: the bank's debit takes money out of the account,
the company's debit to its bank account puts money in. A file whose columns are only เดบิต/เครดิต
and whose side nothing else decides is not guessed: ``side`` stays empty and preflight asks.

Every row keeps the cells it was read from and both money columns as written; the signed amount
(money in is positive, from the company's point of view) is worked out once the side is known.
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

FORMAT = "am.bankrec.v1"
SHEET_EXT = (".xlsx", ".xlsm", ".csv", ".txt")
Q2 = Decimal("0.01")
ZERO = Decimal("0.00")
CARRIED_SHEET = "รายการค้างยกไป"
HEADER_SCAN = 40

# a header cell (spaces and punctuation removed, lower case) → the field it holds
HEADERS = {
    "date": ("วันที่", "วันที่ทำรายการ", "วันที่รายการ", "วันที่ลงบัญชี", "วันที่เอกสาร", "วันเดือนปี", "วันที่มีผล", "date",
             "transactiondate", "txndate", "trandate", "postingdate", "valuedate", "effectivedate", "docdate"),
    "description": ("รายการ", "รายละเอียด", "คำอธิบาย", "คำอธิบายรายการ", "รายละเอียดรายการ", "description", "descriptions",
                    "details", "detail", "particulars", "transaction", "transactiondescription", "narrative", "memo",
                    "หมายเหตุ", "note", "remark", "remarks"),
    "cheque": ("เลขที่เช็ค", "เช็คเลขที่", "หมายเลขเช็ค", "เลขเช็ค", "chequeno", "chequenumber", "chqno", "checkno",
               "checknumber", "cheque", "chq"),
    "doc_no": ("เลขที่เอกสาร", "เลขที่ใบสำคัญ", "เลขที่", "เลขที่อ้างอิง", "อ้างอิง", "เลขอ้างอิง", "voucher", "voucherno", "docno",
               "documentno", "documentnumber", "reference", "referenceno", "ref", "refno", "jvno", "entryno"),
    "out": ("ถอน", "ถอนเงิน", "เงินถอน", "จำนวนเงินถอน", "รายการถอน", "ถอนออก", "จ่าย", "เงินจ่าย", "จ่ายเงิน", "withdrawal",
            "withdrawals", "withdraw", "withdrawn", "moneyout", "paidout", "payments"),
    "in": ("ฝาก", "ฝากเงิน", "เงินฝาก", "จำนวนเงินฝาก", "รายการฝาก", "ฝากเข้า", "รับ", "เงินรับ", "รับเงิน", "deposit", "deposits",
           "moneyin", "paidin", "receipts"),
    "debit": ("เดบิต", "เดบิท", "debit", "dr", "debitamount", "ยอดเดบิต"),
    "credit": ("เครดิต", "credit", "cr", "creditamount", "ยอดเครดิต"),
    "amount": ("จำนวนเงิน", "ยอดเงิน", "amount", "จำนวน"),
    "balance": ("คงเหลือ", "ยอดคงเหลือ", "ยอดเงินคงเหลือ", "ยอดคงเหลือในบัญชี", "balance", "outstandingbalance", "ledgerbalance",
                "runningbalance", "ยอดยกไป"),
    "account": ("รหัสบัญชี", "เลขที่บัญชีแยกประเภท", "accountcode", "glaccount", "glcode", "acctcode", "account"),
    "account_name": ("ชื่อบัญชี", "accountname", "glname"),
    "channel": ("ช่องทาง", "channel", "สาขา", "branch", "teller", "via", "terminal"),
    "kind": ("ประเภท", "ประเภทรายการ", "type", "code", "trancode", "รหัสรายการ"),
}
ONLY_BANK = ("out", "in", "channel")
ONLY_BOOK = ("account", "account_name")
BANK_WORDS = ("รายการเดินบัญชี", "statement", "ธนาคาร", "bank", "kasikorn", "กสิกร", "ไทยพาณิชย์", "siamcommercial", "scb", "กรุงเทพ",
              "bangkokbank", "กรุงไทย", "krungthai", "กรุงศรี", "krungsri", "ทหารไทยธนชาต", "ttb", "ออมสิน", "uob", "cimb",
              "เกียรตินาคิน", "ทิสโก้", "แลนด์แอนด์เฮ้าส์")
BOOK_WORDS = ("บัญชีแยกประเภท", "สมุดบัญชี", "สมุดเงินฝาก", "สมุดเงินสด", "สมุดรายวัน", "generalledger", "ledger", "cashbook",
              "สมุดบัญชีเงินฝากธนาคาร", "รายงานบัญชี")
STATEMENT_WORDS = ("รายการเดินบัญชี", "statement")
OPENING_WORDS = ("ยอดยกมา", "ยกมา", "ยอดยกมาต้นงวด", "balanceb/f", "balancebf", "b/f", "broughtforward", "balancebroughtforward",
                 "openingbalance", "beginningbalance")
CLOSING_WORDS = ("ยอดยกไป", "ยกไป", "balancec/f", "balancecf", "c/f", "carriedforward", "balancecarriedforward",
                 "closingbalance", "endingbalance")
TOTAL_WORDS = ("รวม", "ยอดรวม", "รวมทั้งสิ้น", "total", "grandtotal", "subtotal")
ACCOUNT_RE = re.compile(r"(?<!\d)(\d{3}[- ]?\d[- ]?\d{5}[- ]?\d|\d{3}[- ]?\d{6}[- ]?\d|\d{10,12})(?!\d)")
CHEQUE_RE = re.compile(r"(?:เช็ค(?:เลขที่)?|chq|cheque|check|cq)\s*(?:no\.?|เลขที่|#|:)?\s*(\d{5,10})", re.I)
CARRIED_KINDS = {"เช็คค้างจ่าย": "outstanding_cheque", "เช็คค้าง": "outstanding_cheque", "รายการจ่ายค้าง": "outstanding_payment",
                 "เงินฝากระหว่างทาง": "deposit_in_transit", "เงินฝากระหว่างทางยังไม่เข้าบัญชี": "deposit_in_transit",
                 "outstandingcheque": "outstanding_cheque", "outstandingcheck": "outstanding_cheque",
                 "outstandingpayment": "outstanding_payment", "depositintransit": "deposit_in_transit"}


@dataclass
class Upload:
    """One upload as the parse step read it."""
    kind: str                 # table | carried | other
    key: str
    name: str
    sha256: str
    data: dict

    @property
    def side(self) -> str | None:
        """bank | book when the file itself says which; None when a person has to."""
        return self.data.get("side") if self.kind == "table" else None

    def summary(self) -> dict:
        s = {"key": self.key, "doc_key": self.key, "name": self.name, "kind": self.kind}
        if self.kind == "table":
            d = self.data
            s.update({"side": d["side"], "sheet": d["sheet"], "header_row": d["header_row"], "rows": len(d["rows"]),
                      "columns": d["columns"], "layout": d["layout"]})
        elif self.kind == "carried":
            s["rows"] = len(self.data["rows"])
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
    if ext not in SHEET_EXT:
        return Upload("other", key, name, sha, {"reason": "unsupported_type", "detail": ext,
                                                "say": "อ่านได้เฉพาะไฟล์ Excel หรือ CSV ที่ส่งออกจากธนาคารหรือโปรแกรมบัญชี"})
    wb = read_workbook(path, name)
    if wb.error:
        return Upload("other", key, name, sha, {"reason": "unreadable", "detail": wb.error, "say": "เปิดไฟล์นี้ไม่ได้"})
    for i, sheet in enumerate(wb.sheets):
        if _h(sheet.name) == _h(CARRIED_SHEET) or any(_h(c) == _h(CARRIED_SHEET) for r in sheet.rows[:6] for c in r):
            got = _carried(sheet, i)
            if got is not None:
                return Upload("carried", key, name, sha, got)
    best = None
    for i, sheet in enumerate(wb.sheets):
        got = _table(sheet, i, (mapping or {}).get("bank_reconciliation") or {})
        if got is not None and (best is None or len(got["rows"]) > len(best["rows"])):
            best = got
    if best is None:
        return Upload("other", key, name, sha, {"reason": "unrecognised_sheet",
                                                "say": "ไม่พบหัวคอลัมน์ของรายการเดินบัญชีหรือสมุดบัญชี (วันที่ รายการ และจำนวนเงิน)"})
    return Upload("table", key, name, sha, best)


# ── cells ────────────────────────────────────────────────────────────────

def _h(v) -> str:
    s = "".join(T.arabic(str(v or "")).split()).lower()
    s = re.sub(r"\((thb|บาท|baht|฿)\)", "", s)
    return re.sub(r"[.\-_:()#*]", "", s)


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()[:10]
    return " ".join(str(v).split())


def money(v) -> str | None:
    """A money cell → "1234.50" (its sign kept), or None when the cell holds no amount."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return f"{Decimal(repr(v)).quantize(Q2, rounding=ROUND_HALF_UP):.2f}"
    t = T.arabic(str(v)).replace("บาท", "").replace("฿", "").replace("THB", "").replace(" ", "").strip()
    if t in ("", "-", "—", "–"):
        return None
    neg = (t.startswith("(") and t.endswith(")")) or t.startswith("-") or t.endswith("-")
    t = t.strip("()-")
    if t.upper().endswith(("DR", "CR")):
        t = t[:-2]
    if not re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", t):
        return None
    d = Decimal(t.replace(",", "")).quantize(Q2, rounding=ROUND_HALF_UP)
    return f"{-d if neg else d:.2f}"


def date_of(v) -> str | None:
    """A date cell → ISO. A Thai file writes the day first and the year in the Buddhist era
    (01/09/2569, 01-09-69, 1 ก.ย. 69); a real date cell and yyyy-mm-dd are read as they are."""
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
    if d is None or not (1990 <= d.year <= 2100):
        return None
    return d.isoformat()


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


def _money_layout(cols: dict) -> str | None:
    if "out" in cols and "in" in cols:
        return "out_in"
    if "debit" in cols and "credit" in cols:
        return "debit_credit"
    if "amount" in cols:
        return "signed"
    return None


# ── a statement or a ledger ──────────────────────────────────────────────

def _table(sheet, index: int, mapping: dict) -> dict | None:
    for i, row in enumerate(sheet.rows[:HEADER_SCAN]):
        cols = _header(row, mapping)
        layout = _money_layout(cols)
        if "date" in cols and layout and ("description" in cols or "doc_no" in cols or "cheque" in cols):
            return _read(sheet, index, i, cols, layout)
    return None


def _above(sheet, header_at: int) -> list[str]:
    out = []
    for r in sheet.rows[:header_at]:
        t = " ".join(text(c) for c in r if text(c))
        if t:
            out.append(t[:200])
    return out


def _meta(lines: list[str], sheet_name: str) -> dict:
    whole = " ".join(lines)
    low = _h(whole)
    account = None
    for ln in lines:
        if re.search(r"เลขที่บัญชี|เลขบัญชี|บัญชีเลขที่|account\s*(?:no|number)|a/c", ln, re.I):
            m = ACCOUNT_RE.search(T.arabic(ln))
            if m:
                account = m.group(1)
                break
    name = None
    for ln in lines:
        m = re.search(r"(?:ชื่อบัญชี|account\s*name)\s*[:：]?\s*(.+)", ln, re.I)
        if m:
            name = re.split(r"\s{2,}|เลขที่บัญชี|account\s*no", m.group(1), flags=re.I)[0].strip()[:120]
            break
    bank = None
    for ln in lines:
        m = re.search(r"(ธนาคาร[^\s,;:()]+(?:\s*จำกัด\s*\(มหาชน\))?)", ln)
        if m:
            bank = m.group(1).strip()[:80]
            break
    if bank is None:
        m = re.search(r"([A-Za-z][A-Za-z ]{2,40}\bbank\b)", whole, re.I)
        bank = m.group(1).strip() if m else None
    span = None
    dates = [d for d in (T.parse_date(x) for x in re.split(r"ถึง|[-–—]\s|\bto\b", T.arabic(whole))) if d]
    found = sorted({d[0].isoformat() for d in dates})
    if len(found) >= 2:
        span = [found[0], found[-1]]
    return {"lines": lines[:12], "account_number": account, "account_name": name, "bank": bank, "period": span,
            "bank_words": sorted(w for w in BANK_WORDS if w in low), "book_words": sorted(w for w in BOOK_WORDS if w in low),
            "statement": any(w in low for w in STATEMENT_WORDS), "sheet": sheet_name}


def _side(cols: dict, layout: str, meta: dict) -> tuple[str | None, dict]:
    """bank / book from what the file itself says; None when it does not say."""
    bank, book = [], []
    if layout == "out_in":
        bank.append("คอลัมน์ถอน/ฝาก")
    if "channel" in cols:
        bank.append("คอลัมน์ช่องทาง")
    if meta["statement"]:
        bank.append("หัวกระดาษเขียนว่ารายการเดินบัญชี")
    if meta["account_number"] and not meta["book_words"]:
        bank.append("หัวกระดาษมีเลขที่บัญชีธนาคาร")
    if "account" in cols or "account_name" in cols:
        book.append("คอลัมน์รหัสบัญชี")
    if meta["book_words"]:
        book.append("หัวกระดาษเขียนว่าสมุดบัญชีหรือบัญชีแยกประเภท")
    if "doc_no" in cols and layout == "debit_credit" and not meta["statement"]:
        book.append("คอลัมน์เลขที่เอกสารกับเดบิต/เครดิต")
    why = {"bank": bank, "book": book}
    if bank and not book:
        return "bank", why
    if book and not bank:
        return "book", why
    if len(book) - len(bank) >= 2:
        return "book", why
    if len(bank) - len(book) >= 2:
        return "bank", why
    return None, why


def _read(sheet, index: int, header_at: int, cols: dict, layout: str) -> dict:
    meta = _meta(_above(sheet, header_at), sheet.name)
    side, why = _side(cols, layout, meta)
    a_col, b_col = {"out_in": ("out", "in"), "debit_credit": ("debit", "credit"), "signed": ("amount", None)}[layout]
    rows, skipped, marks = [], [], []
    for i in range(header_at + 1, len(sheet.rows)):
        r = sheet.rows[i]

        def cell(f):
            j = cols.get(f)
            return r[j] if j is not None and j < len(r) else None
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
            continue
        desc = text(cell("description"))
        a, b = money(cell(a_col)), (money(cell(b_col)) if b_col else None)
        bal = money(cell("balance")) if "balance" in cols else None
        date = date_of(cell("date"))
        first = _h(" ".join(text(c) for c in r[:3]))
        low = _h(desc) or first
        cells = {f: f"{col_letter(j)}{i + 1}" for f, j in cols.items() if j < len(r) and r[j] not in (None, "")}
        if any(w in low for w in OPENING_WORDS) or any(w in first for w in OPENING_WORDS):
            marks.append({"row": i + 1, "what": "opening", "balance": bal, "a": a, "b": b, "date": date, "cells": cells,
                          "account": text(cell("account"))})
            continue
        if any(w in low for w in CLOSING_WORDS) or any(w in first for w in CLOSING_WORDS):
            marks.append({"row": i + 1, "what": "closing", "balance": bal, "a": a, "b": b, "date": date, "cells": cells,
                          "account": text(cell("account"))})
            continue
        if date is None and any(low.startswith(w) or first.startswith(w) for w in TOTAL_WORDS):
            skipped.append({"row": i + 1, "why": "total_row"})
            continue
        if a is None and b is None:
            skipped.append({"row": i + 1, "why": "no_amount"})
            continue
        if date is None:
            skipped.append({"row": i + 1, "why": "no_date", "text": text(cell("date"))[:40], "a": a, "b": b})
            continue
        cheque = text(cell("cheque"))
        if not cheque:
            m = CHEQUE_RE.search(T.arabic(desc))
            cheque = m.group(1) if m else ""
        rows.append({
            "row": i + 1, "date": date, "date_raw": text(cell("date")), "description": desc,
            "cheque": cheque, "doc_no": text(cell("doc_no")), "a": a, "b": b, "balance": bal,
            "account": text(cell("account")), "account_name": text(cell("account_name")),
            "channel": text(cell("channel")), "kind": text(cell("kind")), "cells": cells,
        })
    accounts: dict[str, dict] = {}
    for r in rows:
        if r["account"]:
            acc = accounts.setdefault(r["account"], {"code": r["account"], "name": r["account_name"], "rows": 0})
            acc["rows"] += 1
    return {"sheet": sheet.name, "sheet_index": index, "header_row": header_at + 1,
            "columns": {f: col_letter(j) for f, j in cols.items()}, "layout": layout, "side": side, "side_evidence": why,
            "meta": meta, "rows": rows, "skipped": skipped, "marks": marks,
            "accounts": sorted(accounts.values(), key=lambda a: (-a["rows"], a["code"]))}


# ── the items carried from the month before ──────────────────────────────

CARRIED_HEADERS = {
    "date": HEADERS["date"], "description": HEADERS["description"],
    "ref": HEADERS["cheque"] + HEADERS["doc_no"],
    "amount": HEADERS["amount"],
    "kind": ("ประเภท", "ประเภทรายการ", "ชนิด", "type", "kind"),
}


def _carried(sheet, index: int) -> dict | None:
    for i, row in enumerate(sheet.rows[:HEADER_SCAN]):
        heads = [_h(c) for c in row]
        cols = {}
        for j, h in enumerate(heads):
            for f, names in CARRIED_HEADERS.items():
                if h and f not in cols and h in names:
                    cols[f] = j
                    break
        if not all(f in cols for f in ("date", "amount", "kind")):
            continue
        rows, skipped = [], []
        for k in range(i + 1, len(sheet.rows)):
            r = sheet.rows[k]

            def cell(f):
                j = cols.get(f)
                return r[j] if j is not None and j < len(r) else None
            if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
                continue
            kind = CARRIED_KINDS.get(_h(cell("kind")))
            amount, date = money(cell("amount")), date_of(cell("date"))
            if kind is None or amount is None or date is None:
                if any(text(c) for c in r):
                    skipped.append({"row": k + 1, "why": "not_an_item"})
                continue
            rows.append({"row": k + 1, "date": date, "date_raw": text(cell("date")), "description": text(cell("description")),
                         "ref": text(cell("ref")), "amount": amount, "kind": kind,
                         "cells": {f: f"{col_letter(j)}{k + 1}" for f, j in cols.items()}})
        return {"sheet": sheet.name, "sheet_index": index, "header_row": i + 1,
                "columns": {f: col_letter(j) for f, j in cols.items()}, "rows": rows, "skipped": skipped}
    return None
