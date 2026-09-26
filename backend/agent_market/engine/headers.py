"""Find the header row of an export and map its columns to ledger fields.

Different ERPs name the same column differently ('วันที่ลงบัญชี', 'Posting
Date', 'Trans Dt'). The mapping comes from, in order:

1. the install's remembered mapping (header text → field) — the user set it
   once, it wins;
2. the synonym table below;
3. nothing — the field stays unmapped and the check turns that into a
   question with candidate columns and sample values as evidence.

No model is involved in any of this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tabular import Sheet, col_letter
from .thai import date_order_evidence, looks_like_date, month_from_word

FIELDS = (
    "date", "doc_no", "account_code", "account_name", "description",
    "debit", "credit", "amount", "dc_indicator", "currency", "cost_center", "budget",
)

FIELD_LABEL_TH = {
    "date": "วันที่",
    "doc_no": "เลขที่เอกสาร",
    "account_code": "รหัสบัญชี",
    "account_name": "ชื่อบัญชี",
    "description": "คำอธิบายรายการ",
    "debit": "เดบิต",
    "credit": "เครดิต",
    "amount": "จำนวนเงิน",
    "dc_indicator": "เดบิต/เครดิต",
    "currency": "สกุลเงิน",
    "cost_center": "ศูนย์ต้นทุน",
    "budget": "งบประมาณ",
}


def norm(text) -> str:
    """Header text → comparable key: lower case, no spaces/punctuation."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"\((thb|บาท|lc|฿)\)", "", s)
    s = re.sub(r"[\s_\-.:()#*/\\]+", "", s)
    return s


SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("วันที่", "วันที่ลงบัญชี", "วันที่เอกสาร", "วันเดือนปี", "date", "postingdate",
             "docdate", "documentdate", "transactiondate", "transdate", "entrydate", "วันที่รายการ"),
    "doc_no": ("เลขที่เอกสาร", "เลขที่", "เลขที่ใบสำคัญ", "เลขที่อ้างอิง", "voucher", "voucherno",
               "docno", "documentno", "documentnumber", "reference", "ref", "jvno", "เลขที่jv", "entryno"),
    "account_code": ("รหัสบัญชี", "เลขที่บัญชี", "รหัส", "accountcode", "glaccount", "g/laccount",
                     "acct", "acctcode", "accountno", "accountnumber", "glcode", "account"),
    "account_name": ("ชื่อบัญชี", "accountname", "glaccountname", "g/laccountname", "ชื่อบัญชีแยกประเภท",
                     "accountdescription", "รายการ"),
    "description": ("คำอธิบาย", "รายละเอียด", "คำอธิบายรายการ", "description", "memo", "narration",
                    "text", "particulars", "หมายเหตุ", "details"),
    "debit": ("เดบิต", "debit", "dr", "debitamount", "ยอดเดบิต", "debitthb", "debitlc"),
    "credit": ("เครดิต", "credit", "cr", "creditamount", "ยอดเครดิต", "creditthb", "creditlc"),
    "amount": ("จำนวนเงิน", "ยอดเงิน", "amount", "amountinlc", "amountlc", "localamount", "value",
               "จำนวน", "ยอด", "amountinlocalcurrency"),
    "dc_indicator": ("dr/cr", "d/c", "drcr", "dc", "dcind", "dcindicator", "debitcreditindicator",
                     "เดบิต/เครดิต", "ด/ค", "ดค", "ประเภทรายการ", "sign",
                     # SAP: 'Debit/Credit Ind.' (FBL3N), the S/H (Soll/Haben) indicator
                     "debitcreditind", "drcrind", "drcrindicator", "s/h", "shind", "shindicator",
                     "soll/haben", "debitcreditcode"),
    "currency": ("สกุลเงิน", "currency", "curr", "ccy", "currencycode", "localcurrency", "lcurr"),
    "cost_center": ("ศูนย์ต้นทุน", "แผนก", "costcenter", "costcentre", "cc", "dept", "department",
                    "หน่วยงาน", "profitcenter"),
    "budget": ("งบประมาณ", "budget", "budgetamount", "ยอดงบประมาณ"),
}

# 'account' alone is a code in some exports and a name in others (Xero:
# 'Account' = name next to 'Account Code'). Resolved after the first pass.
_AMBIGUOUS_ACCOUNT = "account"

_SYN_INDEX: dict[str, str] = {}
for _f, _words in SYNONYMS.items():
    for _w in _words:
        _SYN_INDEX.setdefault(norm(_w), _f)


@dataclass
class HeaderMap:
    sheet: str
    header_row: int                     # 0-based index into Sheet.rows
    headers: list[str]                  # raw header texts
    fields: dict[str, int] = field(default_factory=dict)       # field → column index
    source: dict[str, str] = field(default_factory=dict)       # field → 'install' | 'synonym'
    month_columns: dict[int, int] = field(default_factory=dict)  # column → month (budget files)
    title_text: str = ""

    def col(self, f: str) -> int | None:
        return self.fields.get(f)

    def letter(self, f: str) -> str | None:
        c = self.fields.get(f)
        return col_letter(c) if c is not None else None

    def header_of(self, f: str) -> str | None:
        c = self.fields.get(f)
        return self.headers[c] if c is not None and c < len(self.headers) else None


def _row_texts(row: list) -> list[str]:
    return ["" if v is None else str(v).strip() for v in row]


def _month_column(text: str) -> int | None:
    t = text.strip().lower()
    t = re.sub(r"\s*(\d{2}|\d{4})$", "", t)  # 'ม.ค. 69' → 'ม.ค.'
    return month_from_word(t)


def score_row(row: list, install_map: dict[str, str] | None = None) -> int:
    install_keys = {norm(v) for v in (install_map or {}).values()}
    hits = set()
    for t in _row_texts(row):
        k = norm(t)
        if not k:
            continue
        if k in install_keys:
            hits.add("install:" + k)
        elif k in _SYN_INDEX:
            hits.add(_SYN_INDEX[k])
        elif _month_column(t):
            hits.add("month")
    return len(hits)


def find_header(sheet: Sheet, install_map: dict[str, str] | None = None, scan: int = 30) -> int | None:
    best, best_score = None, 0
    for i, row in enumerate(sheet.rows[:scan]):
        s = score_row(row, install_map)
        if s > best_score:
            best, best_score = i, s
        if s >= 4:
            return i
    return best if best_score >= 2 else None


def _looks_like_data(texts: list[str]) -> bool:
    return any(looks_like_date(t) or _CODE_RE.match(t) for t in texts if t)


def _with_subheader(sheet: Sheet, header_row: int) -> tuple[int, list[str]]:
    """A header printed over two rows ('จำนวนเงิน' above 'เดบิต | เครดิต', or
    merged cells whose lower half holds the real names) becomes one row.

    The row below counts as a sub-header only when it holds no data (no date,
    no account code) and names a field in a column whose upper cell is empty
    (a merged range) or only a group label such as 'จำนวนเงิน'. Returns the
    last header row and the combined texts.
    """
    headers = _row_texts(sheet.rows[header_row])
    if header_row + 1 >= len(sheet.rows):
        return header_row, headers
    sub = _row_texts(sheet.rows[header_row + 1])
    if _looks_like_data(sub):
        return header_row, headers
    combined = list(headers) + [""] * max(0, len(sub) - len(headers))
    used = False
    for c, t in enumerate(sub):
        k = norm(t)
        if not k or k not in _SYN_INDEX:
            continue
        above = norm(combined[c])
        if not above or _SYN_INDEX.get(above) == "amount":
            combined[c] = t
            used = True
    return (header_row + 1, combined) if used else (header_row, headers)


def map_headers(sheet: Sheet, header_row: int, install_map: dict[str, str] | None = None) -> HeaderMap:
    header_row, headers = _with_subheader(sheet, header_row)
    hm = HeaderMap(sheet=sheet.name, header_row=header_row, headers=headers)
    title_rows = [" ".join(t for t in _row_texts(r) if t) for r in sheet.rows[:header_row]]
    hm.title_text = " ".join(t for t in title_rows if t)[:600]

    install_by_header = {norm(h): f for f, h in (install_map or {}).items() if h}
    ambiguous_account_col = None
    for c, h in enumerate(headers):
        k = norm(h)
        if not k:
            continue
        if k in install_by_header and install_by_header[k] not in hm.fields:
            hm.fields[install_by_header[k]] = c
            hm.source[install_by_header[k]] = "install"
            continue
        if k == _AMBIGUOUS_ACCOUNT:
            ambiguous_account_col = c
            continue
        f = _SYN_INDEX.get(k)
        if f and f not in hm.fields:
            hm.fields[f] = c
            hm.source[f] = "synonym"
            continue
        mon = _month_column(h)
        if mon:
            hm.month_columns[c] = mon
    if ambiguous_account_col is not None:
        target = "account_name" if "account_code" in hm.fields else "account_code"
        if target not in hm.fields:
            hm.fields[target] = ambiguous_account_col
            hm.source[target] = "synonym"
    # 'รายการ' is a line label in a P&L but an account name in a GL; only keep
    # it as account_name when there is no better candidate — already handled
    # by first-wins ordering above.
    return hm


# ── column profiling (used for candidates in 'unmapped_column' questions) ──

_CODE_RE = re.compile(r"^\d{3,6}([-.]\d{1,4})?$")


def profile_column(sheet: Sheet, header_row: int, col: int, limit: int = 400) -> dict:
    vals = [r[col] if col < len(r) else None for r in sheet.rows[header_row + 1: header_row + 1 + limit]]
    vals = [v for v in vals if v is not None and str(v).strip() != ""]
    n = len(vals) or 1
    dates = sum(1 for v in vals if looks_like_date(v) and not (isinstance(v, (int, float)) and v < 20000))
    nums = sum(1 for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
    codes = sum(1 for v in vals if _CODE_RE.match(str(v).strip()))
    short = sum(1 for v in vals if isinstance(v, str) and len(v.strip()) <= 2)
    distinct = len({str(v).strip() for v in vals})
    return {
        "date_ratio": dates / n,
        "number_ratio": nums / n,
        "code_ratio": codes / n,
        "short_text_ratio": short / n,
        "distinct": distinct,
        "count": len(vals),
        "samples": [str(v) for v in vals[:5]],
    }


def header_by_profile(sheet: Sheet, scan: int = 30) -> int | None:
    """A header row found by shape when no header text is recognised.

    The row has ≥3 text cells and the rows under it carry numbers or dates
    in those columns — the shape of every tabular export.
    """
    for i, row in enumerate(sheet.rows[:scan]):
        texts = [v for v in row if isinstance(v, str) and v.strip()]
        if len(texts) < 3 or any(isinstance(v, (int, float)) for v in row if v is not None):
            continue
        below = sheet.rows[i + 1: i + 11]
        if len(below) < 3:
            continue
        typed = 0
        for r in below:
            if any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in r) or \
               any(looks_like_date(v) for v in r if isinstance(v, str)):
                typed += 1
        if typed >= max(3, len(below) // 2):
            return i
    return None


def infer_missing(sheet: Sheet, hm: HeaderMap) -> None:
    """Fill fields the header text did not name, from what the column holds.

    Every field set here is marked source='inferred': a check must confirm it
    with the user (or the install mapping) before a number depends on it.
    """
    profiles = {c: profile_column(sheet, hm.header_row, c) for c in range(len(hm.headers)) if hm.headers[c]}
    used = set(hm.fields.values()) | set(hm.month_columns)

    def take(f, c):
        hm.fields[f] = c
        hm.source[f] = "inferred"
        used.add(c)

    if "date" not in hm.fields:
        for c, p in profiles.items():
            if c not in used and p["count"] and p["date_ratio"] >= 0.8:
                take("date", c)
                break
    if "account_code" not in hm.fields:
        for c, p in profiles.items():
            if c not in used and p["count"] and p["code_ratio"] >= 0.8:
                take("account_code", c)
                break
    have_amounts = ("debit" in hm.fields and "credit" in hm.fields) or "amount" in hm.fields
    if not have_amounts:
        numeric = [c for c, p in profiles.items() if c not in used and p["count"] and p["number_ratio"] >= 0.8]
        if len(numeric) >= 2:
            # Debit/credit columns are filled alternately; an amount column is always filled.
            a, b = numeric[0], numeric[1]
            rows = sheet.rows[hm.header_row + 1: hm.header_row + 200]
            both = sum(1 for r in rows if (a < len(r) and r[a] not in (None, "", 0)) and (b < len(r) and r[b] not in (None, "", 0)))
            if both <= len(rows) * 0.05:
                take("debit", a)
                take("credit", b)
            else:
                take("amount", a)
        elif len(numeric) == 1:
            take("amount", numeric[0])
    if "account_name" not in hm.fields and "account_code" in hm.fields:
        nxt = hm.fields["account_code"] + 1
        p = profiles.get(nxt)
        if p and nxt not in used and p["number_ratio"] < 0.2 and p["distinct"] > 1:
            take("account_name", nxt)
    if "description" not in hm.fields:
        best = None
        for c, p in profiles.items():
            if c in used or p["number_ratio"] > 0.2 or not p["count"]:
                continue
            avg = sum(len(s) for s in p["samples"]) / max(1, len(p["samples"]))
            if avg >= 8 and (best is None or avg > best[0]):
                best = (avg, c)
        if best:
            take("description", best[1])


# ── the order of a date column: DD/MM or MM/DD ─────────────────────────────

_THAI_RE = re.compile(f"[{chr(0x0E00)}-{chr(0x0E7F)}]")  # the Thai Unicode block
# English date headers of ledger exports.
_EN_DATE_HEADERS = frozenset(norm(h) for h in (
    "Date", "Posting Date", "Document Date", "Doc. Date", "Pstng Date", "Entry Date", "Transaction Date",
    "Trans Date", "Value Date", "Baseline Date"))
# Header texts that SAP line-item reports (FBL3N, FAGLL03, FBL1N/5N) print.
_SAP_HEADERS = frozenset(norm(h) for h in (
    "Company Code", "CoCd", "Debit/Credit Ind.", "D/C Ind.", "S/H Ind.", "Amount in Local Currency",
    "Amount in LC", "Amount in Doc. Curr.", "LCurr", "Posting Key", "Pstng Date"))
_SAP_FILE_RE = re.compile(r"(?<![a-z])(sap|fbl3n|fagll03|fbl1n|fbl5n|s4hana)(?![a-z])")


@dataclass
class DateOrder:
    """How one date column writes its dates.

    order: 'dmy' | 'mdy', or None when nothing decides it (the check asks).
    source: 'values' — the column's own dates decide (a first number over 12
    is a day, a second number over 12 is a day, a Buddhist-era year is Thai
    and day-first); 'none' — no text date needs an order; 'conflict' — the
    column proves both orders (rows that fit neither reading are counted as
    unreadable, never guessed); 'thai' / 'sap' — every date is ambiguous and
    the export decides (Thai headers are day-first; SAP writes MM/DD/YYYY
    with '/' or '-' and DD.MM.YYYY with '.' — it has no DD/MM/YYYY format);
    'undecided' — ambiguous and nothing decides.
    """
    order: str | None
    source: str
    evidence: dict
    column: str = ""

    def to_dict(self) -> dict:
        return {"column": self.column, "order": self.order, "source": self.source,
                "applied": self.order or "dmy",
                "evidence": {k: self.evidence[k] for k in ("day_first", "month_first", "ambiguous", "buddhist",
                                                            "invalid", "separators", "samples")}}


def is_sap_export(headers: list[str], file_name: str = "", indicator_values: list[str] | None = None) -> bool:
    keys = {norm(h) for h in headers if h}
    ind = {str(v).strip().upper() for v in (indicator_values or []) if str(v).strip()}
    return bool(keys & _SAP_HEADERS) or bool(_SAP_FILE_RE.search((file_name or "").lower())) \
        or bool(ind and ind <= {"S", "H"})


def decide_date_order(values, header: str, headers: list[str], file_name: str = "",
                      indicator_values: list[str] | None = None) -> DateOrder:
    """DD/MM or MM/DD for one column, from its own values first.

    Any first number over 12 makes MM/DD impossible; any second number over
    12 makes DD/MM impossible. Only when every date is ambiguous (day and
    month both 1-12) do the headers break the tie: Thai headers → DD/MM; an
    English date header in an SAP export → SAP's own format for the
    separator. Anything else stays undecided, and the check asks.
    """
    ev = date_order_evidence(values)
    day_first = ev["day_first"] + ev["buddhist"]
    month_first = ev["month_first"]
    if not (day_first or month_first or ev["ambiguous"]):
        return DateOrder("dmy", "none", ev, header)
    if day_first and month_first:
        return DateOrder("dmy", "conflict", ev, header)
    if month_first:
        return DateOrder("mdy", "values", ev, header)
    if day_first:
        return DateOrder("dmy", "values", ev, header)
    if _THAI_RE.search(header or "") or any(_THAI_RE.search(h or "") for h in headers):
        return DateOrder("dmy", "thai", ev, header)
    seps = ev["separators"]
    if norm(header) in _EN_DATE_HEADERS and len(seps) == 1 and is_sap_export(headers, file_name, indicator_values):
        return DateOrder("dmy" if "." in seps else "mdy", "sap", ev, header)
    return DateOrder(None, "undecided", ev, header)


def candidates_for(field_name: str, sheet: Sheet, hm: HeaderMap, max_n: int = 4) -> list[dict]:
    """Columns that could be `field_name`, best first, with sample values."""
    used = set(hm.fields.values())
    scored = []
    for c, h in enumerate(hm.headers):
        if c in used or not h:
            continue
        p = profile_column(sheet, hm.header_row, c)
        if p["count"] == 0:
            continue
        if field_name == "date":
            s = p["date_ratio"]
        elif field_name in ("debit", "credit", "amount", "budget"):
            s = p["number_ratio"]
        elif field_name == "account_code":
            s = p["code_ratio"]
        elif field_name == "dc_indicator":
            s = p["short_text_ratio"] if p["distinct"] <= 4 else 0.0
        else:
            s = 0.3 if p["number_ratio"] < 0.5 else 0.0
        if s > 0.2:
            scored.append((s, c, h, p))
    scored.sort(key=lambda t: -t[0])
    return [
        {"column": col_letter(c), "index": c, "header": h, "samples": p["samples"], "score": round(s, 2)}
        for s, c, h, p in scored[:max_n]
    ]
