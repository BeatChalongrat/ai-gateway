"""What a run's uploads are: the bank's statement, the company's ledger of that account, and the
items still open from the month before.

A statement comes as a spreadsheet (Excel, CSV) or as the PDF the bank issued (AM6). A PDF's table
is rebuilt from where its words stand (engine/e2/grid.py) and then read exactly as a sheet is: the
same headers, the same rows, the same running balance. A PDF's text layer often loses Thai marks
("รายการเดนบญช"), so a PDF's headers and words are compared without them. A PDF without a text
layer — a scan, a photograph — is not read: the round says so, and nothing is taken from it.

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
from ..words import W

FORMAT = "am.bankrec.v1"
SHEET_EXT = (".xlsx", ".xlsm", ".csv", ".txt")
PDF_EXT = (".pdf",)
Q2 = Decimal("0.01")
ZERO = Decimal("0.00")
CARRIED_SHEET = "doc.sheet.carried_out"   # the sheet of last month's paper this agent reads back (its key; W says it)


def _carried_names() -> set:
    return {_h(x) for x in W.both(CARRIED_SHEET).values()}
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
# the marks are optional: a PDF's text layer may have lost them ("เชค 10001201")
CHEQUE_RE = re.compile(r"(?:เช็?ค(?:เลขที่?)?|chq|cheque|check|cq)\s*(?:no\.?|เลขที่?|#|:)?\s*(\d{5,10})", re.I)
CARRIED_KINDS = {"เช็คค้างจ่าย": "outstanding_cheque", "เช็คค้าง": "outstanding_cheque", "รายการจ่ายค้าง": "outstanding_payment",
                 "เงินฝากระหว่างทาง": "deposit_in_transit", "เงินฝากระหว่างทางยังไม่เข้าบัญชี": "deposit_in_transit",
                 "outstandingcheque": "outstanding_cheque", "outstandingcheck": "outstanding_cheque",
                 "outstandingpayment": "outstanding_payment", "depositintransit": "deposit_in_transit"}
# AM18: and the words this agent's own paper prints for those kinds (its "Items carried forward" sheet comes back
# next month), in whichever language the paper was written in
CARRIED_KINDS.update({"".join(v.split()).lower(): k for k in ("outstanding_cheque", "outstanding_payment", "deposit_in_transit")
                      for v in W.both(f"type.{k}").values()})


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


def said(u: Upload) -> str:
    """Why a file was not used, in the language of the run. A parsed file keeps the key of the sentence, not
    its words: it is read by every later step of the run."""
    key = u.data.get("say") or ""
    return W(key) if key in W.strings else key


def parse(path: str, name: str, mapping: dict | None = None) -> Upload:
    ext = os.path.splitext(name)[1].lower()
    sha = sha256_file(path)
    key = "d" + sha[:10]
    if ext in PDF_EXT:
        return _pdf(path, name, sha, key, (mapping or {}).get("bank_reconciliation") or {})
    if ext not in SHEET_EXT:
        return Upload("other", key, name, sha, {"reason": "unsupported_type", "detail": ext,
                                                "say": "say.unsupported_type"})
    wb = read_workbook(path, name)
    if wb.error:
        return Upload("other", key, name, sha, {"reason": "unreadable", "detail": wb.error, "say": "say.unreadable"})
    for i, sheet in enumerate(wb.sheets):
        # AM18: the sheet is written in the language of the run that made it, and read back by either name
        if _h(sheet.name) in _carried_names() or any(_h(c) in _carried_names() for r in sheet.rows[:6] for c in r):
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
                                                "say": "say.unrecognised_sheet"})
    return Upload("table", key, name, sha, best)


# ── a statement as a PDF ─────────────────────────────────────────────────

def plain(s) -> str:
    """A text without the Thai marks a PDF's text layer loses, its spaces kept."""
    return "".join("า" if ch == "ำ" else ch for ch in str(s or "") if ch not in T._MARKS and ch not in "\u200b\ufeff")


def _known_header(text) -> bool:
    h = plain(_h(text))
    return bool(h) and any(h == plain(n) for names in HEADERS.values() for n in names)


def _pdf(path: str, name: str, sha: str, key: str, mapping: dict) -> Upload:
    from ....engine.e2 import grid
    from ....engine.tabular import Sheet

    try:
        g = grid.read(path, _known_header)
    except Exception as exc:  # noqa: BLE001 — a PDF that cannot be opened is a file that was not used, with its reason
        return Upload("other", key, name, sha, {"reason": "unreadable", "detail": type(exc).__name__, "say": "say.unreadable_pdf"})
    if g is not None and g["scanned"]:
        return Upload("other", key, name, sha, {
            "reason": "scanned_pdf", "detail": f"{len(g['scanned'])}/{g['pages']}",
            "say": "say.scanned_pdf"})
    got = None if g is None else _table(Sheet("PDF", g["rows"]), 0, mapping, loose=True, where=g["where"])
    if got is None:
        return Upload("other", key, name, sha, {"reason": "unrecognised_sheet",
                                                "say": "say.no_table_pdf"})
    got["pdf"] = {"pages": g["pages"]}
    return Upload("table", key, name, sha, got)


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


def _header(row: list, mapping: dict, loose: bool = False) -> dict:
    """{field: column}: a column the install mapped by name first, then the known header words.
    ``loose`` (a PDF): the words are compared without their Thai marks."""
    cols: dict[str, int] = {}
    norm = (lambda v: plain(_h(v))) if loose else _h
    heads = [norm(c) for c in row]
    for f, header in mapping.items():
        if f in HEADERS and norm(header) in heads:
            cols[f] = heads.index(norm(header))
    for j, h in enumerate(heads):
        if not h or j in cols.values():
            continue
        for f, names in HEADERS.items():
            if f not in cols and h in (tuple(plain(n) for n in names) if loose else names):
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

def _table(sheet, index: int, mapping: dict, loose: bool = False, where: list | None = None) -> dict | None:
    for i, row in enumerate(sheet.rows[:HEADER_SCAN]):
        cols = _header(row, mapping, loose)
        layout = _money_layout(cols)
        if "date" in cols and layout and ("description" in cols or "doc_no" in cols or "cheque" in cols):
            return _read(sheet, index, i, cols, layout, loose=loose, where=where)
    return None


def _above(sheet, header_at: int) -> list[str]:
    out = []
    for r in sheet.rows[:header_at]:
        t = " ".join(text(c) for c in r if text(c))
        if t:
            out.append(t[:200])
    return out


def _meta(lines: list[str], sheet_name: str, loose: bool = False) -> dict:
    if loose:
        # a PDF: what stands above the table is searched without its marks (and named without them,
        # unless the install's settings name the account)
        return _meta_plain([plain(ln) for ln in lines], sheet_name)
    return _meta_of(lines, sheet_name, lambda s: s)


def _meta_plain(lines: list[str], sheet_name: str) -> dict:
    return _meta_of(lines, sheet_name, plain)


def _meta_of(lines: list[str], sheet_name: str, p) -> dict:
    whole = " ".join(lines)
    low = _h(whole)
    account = None
    for ln in lines:
        if re.search(p(r"เลขที่บัญชี|เลขบัญชี|บัญชีเลขที่") + r"|account\s*(?:no|number)|a/c", ln, re.I):
            m = ACCOUNT_RE.search(T.arabic(ln))
            if m:
                account = m.group(1)
                break
    name = None
    for ln in lines:
        m = re.search(r"(?:" + p("ชื่อบัญชี") + r"|account\s*name)\s*[:：]?\s*(.+)", ln, re.I)
        if m:
            name = re.split(r"\s{2,}|" + p("เลขที่บัญชี") + r"|account\s*no", m.group(1), flags=re.I)[0].strip()[:120]
            break
    bank = None
    for ln in lines:
        m = re.search(r"(ธนาคาร[^\s,;:()]+(?:\s*" + p("จำกัด") + r"\s*\(มหาชน\))?)", ln)
        if m:
            bank = m.group(1).strip()[:80]
            break
    if bank is None:
        m = re.search(r"([A-Za-z][A-Za-z ]{2,40}\bbank\b)", whole, re.I)
        bank = m.group(1).strip() if m else None
    span = None
    dates = [d for d in (T.parse_date(x) for x in re.split(p("ถึง") + r"|[-–—]\s|\bto\b", T.arabic(whole))) if d]
    found = sorted({d[0].isoformat() for d in dates})
    if len(found) >= 2:
        span = [found[0], found[-1]]
    return {"lines": lines[:12], "account_number": account, "account_name": name, "bank": bank, "period": span,
            "bank_words": sorted(w for w in BANK_WORDS if p(w) in low), "book_words": sorted(w for w in BOOK_WORDS if p(w) in low),
            "statement": any(p(w) in low for w in STATEMENT_WORDS), "sheet": sheet_name}


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


def _read(sheet, index: int, header_at: int, cols: dict, layout: str, *, loose: bool = False, where: list | None = None) -> dict:
    """``loose`` and ``where``: a PDF's grid — its words are compared without their marks, and a
    row is cited by the page and the line it stands on."""
    meta = _meta(_above(sheet, header_at), sheet.name, loose)
    p = plain if loose else (lambda s: s)
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
        if loose:
            first, low = plain(first), plain(low)
        if where is not None:
            at = W("where.pdf", page=where[i][0], line=where[i][1])
            cells = {f: at for f, j in cols.items() if j < len(r) and r[j] not in (None, "")}
        else:
            cells = {f: f"{col_letter(j)}{i + 1}" for f, j in cols.items() if j < len(r) and r[j] not in (None, "")}
        if any(p(w) in low for w in OPENING_WORDS) or any(p(w) in first for w in OPENING_WORDS):
            marks.append({"row": i + 1, "what": "opening", "balance": bal, "a": a, "b": b, "date": date, "cells": cells,
                          "account": text(cell("account"))})
            continue
        if any(p(w) in low for w in CLOSING_WORDS) or any(p(w) in first for w in CLOSING_WORDS):
            marks.append({"row": i + 1, "what": "closing", "balance": bal, "a": a, "b": b, "date": date, "cells": cells,
                          "account": text(cell("account"))})
            continue
        if date is None and any(low.startswith(p(w)) or first.startswith(p(w)) for w in TOTAL_WORDS):
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
            **({"from_pdf": True} if loose else {}),
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
