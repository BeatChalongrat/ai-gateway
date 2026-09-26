"""Turn a mapped sheet into ledger rows with provenance.

Every row keeps (file, sheet, Excel row, amount column letters) so any total
can be cited back to its cells. Amounts are Decimal end to end — a float
never touches a money value after the cell is read.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .headers import (
    HeaderMap, decide_date_order, find_header, header_by_profile, infer_missing, map_headers, norm,
)
from .tabular import Sheet, Workbook, col_letter
from .thai import date_text_kind, find_period_in_text, parse_date

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

# Rows an ERP prints that are not postings.
_SKIP_WORDS = (
    "รวม", "ยอดรวม", "รวมทั้งสิ้น", "ยอดยกมา", "ยอดยกไป", "ยกมา", "ยกไป", "total", "subtotal",
    "grandtotal", "balancebf", "balancecf", "openingbalance", "closingbalance", "carriedforward",
    "broughtforward", "หน้า", "page",
)


def to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(CENT)
    s = str(value).strip().replace(",", "").replace("฿", "").replace(" ", "")
    if s in ("", "-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if s.endswith("-"):  # SAP trailing minus: 1,200.00-
        neg, s = True, s[:-1]
    try:
        d = Decimal(s).quantize(CENT)
    except InvalidOperation:
        return None
    return -d if neg else d


def account_digits(code) -> str:
    return re.sub(r"\D", "", str(code or ""))


@dataclass
class LedgerRow:
    file: str
    sheet: str
    row: int                 # Excel row number (1-based)
    date: _dt.date | None
    account_code: str
    account_name: str
    description: str
    debit: Decimal
    credit: Decimal
    currency: str = "THB"
    cost_center: str = ""
    doc_no: str = ""
    amount_cols: str = ""    # e.g. 'G:H' — where the money sits on this row
    raw_indicator: str = ""  # D/C indicator text, if the export used one
    # the date was typed with day and month both 1-12 in a column whose order
    # nothing decided: an answer may read it the other way round
    date_swappable: bool = False

    @property
    def period(self) -> str | None:
        return f"{self.date.year:04d}-{self.date.month:02d}" if self.date else None

    @property
    def net_debit(self) -> Decimal:
        return self.debit - self.credit

    def ref(self) -> dict:
        return {"file": self.file, "sheet": self.sheet, "row": self.row}


@dataclass
class ParsedFile:
    name: str
    sha256: str
    size: int
    kind: str
    doc_type: str                       # gl | tb | pl | budget | unknown | unreadable
    sheet: str | None = None
    header: HeaderMap | None = None
    rows: list[LedgerRow] = field(default_factory=list)
    skipped_rows: int = 0
    data_rows: int = 0
    period_hint: str | None = None      # from title rows / file name
    dc_mode: str | None = None          # columns | indicator | signed
    indicator_values: list[str] = field(default_factory=list)
    currencies: dict[str, int] = field(default_factory=dict)
    budget: dict[str, Decimal] = field(default_factory=dict)       # account → budget for period
    budget_by_month: dict[str, dict[int, Decimal]] = field(default_factory=dict)
    account_names: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    encoding: str | None = None
    raw_date_texts: list = field(default_factory=list)
    # rows with an account and money whose date could not be read: they are
    # left out of the ledger, and a check asks about them (never silent)
    unreadable_date_rows: int = 0
    unreadable_date_samples: list = field(default_factory=list)   # [row, text, account, amount]
    unreadable_date_amount: Decimal = ZERO
    # DD/MM or MM/DD of the date column (headers.DateOrder.to_dict()); 'applied'
    # is the order the rows' dates are in now
    date_order: dict | None = None

    def summary(self) -> dict:
        return {
            "name": self.name, "sha256": self.sha256, "size": self.size, "kind": self.kind,
            "doc_type": self.doc_type, "sheet": self.sheet,
            "header_row": (self.header.header_row + 1) if self.header else None,
            "columns": ({f: {"column": self.header.letter(f), "header": self.header.header_of(f),
                             "source": self.header.source.get(f)} for f in self.header.fields}
                        if self.header else {}),
            "data_rows": self.data_rows, "skipped_rows": self.skipped_rows,
            "period_hint": self.period_hint, "dc_mode": self.dc_mode,
            "indicator_values": self.indicator_values, "currencies": self.currencies,
            "encoding": self.encoding, "error": self.error,
        }


def _is_skip_row(texts: list[str]) -> bool:
    joined = norm(" ".join(texts))
    if not joined:
        return True
    first = norm(next((t for t in texts if t), ""))
    return any(first.startswith(w) for w in _SKIP_WORDS)


def _classify(hm: HeaderMap, sheet: Sheet, title: str, file_name: str) -> str:
    f = hm.fields
    title_n = norm(title + " " + file_name)
    has_amounts = ("debit" in f and "credit" in f) or "amount" in f
    if "budget" in f or (hm.month_columns and "account_code" in f and not has_amounts):
        return "budget"
    if any(w in title_n for w in ("งบประมาณ", "budget")) and "account_code" in f and (hm.month_columns or has_amounts):
        return "budget"
    if "account_code" not in f and "account_name" not in f:
        return "unknown"
    if not has_amounts:
        return "unknown"
    if "date" in f:
        return "gl"
    if any(w in title_n for w in ("งบทดลอง", "trialbalance")) or ("debit" in f and "credit" in f):
        return "tb"
    if any(w in title_n for w in ("งบกำไรขาดทุน", "กำไรขาดทุน", "profitandloss", "incomestatement", "pl")) or "amount" in f:
        return "pl"
    return "unknown"


def parse_file(path: str, display_name: str, install_map: dict | None = None,
               read=None) -> ParsedFile:
    from .tabular import read_workbook

    wb: Workbook = (read or read_workbook)(path, display_name)
    pf = ParsedFile(name=wb.file_name, sha256=wb.sha256, size=wb.size, kind=wb.kind,
                    doc_type="unreadable", encoding=wb.encoding)
    if wb.error or not wb.sheets:
        pf.error = wb.error or "empty"
        return pf
    # Pick the sheet whose header row scores best.
    best = None
    for sh in wb.sheets:
        hr = find_header(sh, install_map)
        if hr is None:
            hr = header_by_profile(sh)
            if hr is None:
                continue
        hm = map_headers(sh, hr, install_map)
        if not hm.month_columns:
            infer_missing(sh, hm)
        score = sum(1 for f in hm.fields if hm.source.get(f) != "inferred") + 0.5 * sum(
            1 for f in hm.fields if hm.source.get(f) == "inferred") + (2 if hm.month_columns else 0)
        if best is None or score > best[0]:
            best = (score, sh, hm)
    if best is None:
        pf.doc_type = "unknown"
        pf.sheet = wb.sheets[0].name
        pf.data_rows = max(0, len(wb.sheets[0].rows) - 1)
        return pf
    _, sheet, hm = best
    pf.sheet, pf.header = sheet.name, hm
    pf.doc_type = _classify(hm, sheet, hm.title_text, wb.file_name)
    pf.period_hint = find_period_in_text(hm.title_text) or find_period_in_text(wb.file_name)
    if pf.doc_type == "budget":
        _parse_budget(pf, sheet, hm)
    elif pf.doc_type in ("gl", "tb", "pl"):
        _parse_rows(pf, sheet, hm)
    else:
        pf.data_rows = max(0, len(sheet.rows) - hm.header_row - 1)
    return pf


def _cell(row: list, c: int | None):
    if c is None or c >= len(row):
        return None
    return row[c]


def _parse_rows(pf: ParsedFile, sheet: Sheet, hm: HeaderMap) -> None:
    f = hm.fields
    if "debit" in f and "credit" in f:
        pf.dc_mode = "columns"
        cols = f"{col_letter(f['debit'])}:{col_letter(f['credit'])}"
    elif "amount" in f and "dc_indicator" in f:
        pf.dc_mode = "indicator"
        cols = f"{col_letter(f['amount'])}"
    else:
        pf.dc_mode = "signed"
        cols = f"{col_letter(f['amount'])}"
    order, order_source = "dmy", "none"
    if "date" in f:
        body = sheet.rows[hm.header_row + 1:]
        ind_col = f.get("dc_indicator") if pf.dc_mode == "indicator" else None
        ind_values = sorted({str(_cell(r, ind_col)).strip() for r in body if _cell(r, ind_col) not in (None, "")}) \
            if ind_col is not None else []
        do = decide_date_order([_cell(r, f["date"]) for r in body], hm.header_of("date") or "",
                               hm.headers, pf.name, ind_values[:8])
        pf.date_order = do.to_dict()
        order, order_source = do.order or "dmy", do.source
    indicators: dict[str, int] = {}
    for i in range(hm.header_row + 1, len(sheet.rows)):
        row = sheet.rows[i]
        texts = ["" if v is None else str(v).strip() for v in row]
        if not any(texts):
            continue
        if norm(" ".join(texts)) == norm(" ".join(hm.headers)):  # repeated page header
            pf.skipped_rows += 1
            continue
        code = _cell(row, f.get("account_code"))
        code_s = str(code).strip() if code is not None else ""
        if isinstance(code, float) and code.is_integer():
            code_s = str(int(code))
        if not code_s or not account_digits(code_s) or _is_skip_row(texts[:3]) and not account_digits(code_s):
            pf.skipped_rows += 1
            continue
        if _is_skip_row([texts[f["date"]]] if "date" in f and f["date"] < len(texts) else texts[:1]) and not account_digits(code_s):
            pf.skipped_rows += 1
            continue
        raw_date = _cell(row, f.get("date"))
        d = parse_date(raw_date, order)
        kind = date_text_kind(raw_date)
        swappable = bool(kind and kind["kind"] == "ambiguous" and order_source == "undecided")
        if order_source == "conflict" and kind and kind["kind"] == "ambiguous" and not kind["buddhist"]:
            # The column proves both orders; a date whose day and month are
            # both 1-12 could be either, so it is not read — it is asked about.
            d = None
        if "date" in f and d is None:
            # A posting without a date is a total/label line in every export we
            # saw — unless it carries money: then the date is in a format the
            # parser does not know, and the row is counted for the check.
            money = [to_decimal(_cell(row, f[k])) for k in ("debit", "credit", "amount") if k in f]
            amount = sum((abs(m) for m in money if m), ZERO)
            if raw_date not in (None, "") and amount:
                pf.unreadable_date_rows += 1
                pf.unreadable_date_amount += amount
                if len(pf.unreadable_date_samples) < 20:
                    pf.unreadable_date_samples.append([i + 1, str(raw_date).strip(), code_s, str(amount)])
            pf.skipped_rows += 1
            continue
        if isinstance(raw_date, str) and len(pf.raw_date_texts) < 400:
            pf.raw_date_texts.append(raw_date)
        debit = credit = ZERO
        indicator = ""
        if pf.dc_mode == "columns":
            debit = to_decimal(_cell(row, f["debit"])) or ZERO
            credit = to_decimal(_cell(row, f["credit"])) or ZERO
        else:
            amt = to_decimal(_cell(row, f["amount"]))
            if amt is None:
                pf.skipped_rows += 1
                continue
            if pf.dc_mode == "indicator":
                indicator = str(_cell(row, f["dc_indicator"]) or "").strip()
                indicators[indicator] = indicators.get(indicator, 0) + 1
            # Direction is decided later by apply_convention(); park the
            # absolute amount in debit and the sign in the indicator text.
            if pf.dc_mode == "signed":
                indicator = "-" if amt < 0 else "+"
            debit = abs(amt)
        if debit == ZERO and credit == ZERO:
            pf.skipped_rows += 1
            continue
        cur = str(_cell(row, f.get("currency")) or "THB").strip().upper() or "THB"
        pf.currencies[cur] = pf.currencies.get(cur, 0) + 1
        name = str(_cell(row, f.get("account_name")) or "").strip()
        if name and code_s not in pf.account_names:
            pf.account_names[code_s] = name
        pf.rows.append(LedgerRow(
            file=pf.name, sheet=sheet.name, row=i + 1, date=d,
            account_code=code_s, account_name=name,
            description=str(_cell(row, f.get("description")) or "").strip(),
            debit=debit, credit=credit, currency=cur,
            cost_center=str(_cell(row, f.get("cost_center")) or "").strip(),
            doc_no=str(_cell(row, f.get("doc_no")) or "").strip(),
            amount_cols=cols, raw_indicator=indicator, date_swappable=swappable,
        ))
        pf.data_rows += 1
    pf.indicator_values = sorted(indicators, key=lambda k: -indicators[k])


def _parse_budget(pf: ParsedFile, sheet: Sheet, hm: HeaderMap) -> None:
    f = hm.fields
    for i in range(hm.header_row + 1, len(sheet.rows)):
        row = sheet.rows[i]
        code = _cell(row, f.get("account_code"))
        code_s = str(int(code)) if isinstance(code, float) and code.is_integer() else str(code or "").strip()
        if not account_digits(code_s):
            continue
        name = str(_cell(row, f.get("account_name")) or "").strip()
        if name:
            pf.account_names.setdefault(code_s, name)
        if "budget" in f:
            v = to_decimal(_cell(row, f["budget"]))
            if v is not None:
                pf.budget[code_s] = v
        for c, month in hm.month_columns.items():
            v = to_decimal(_cell(row, c))
            if v is not None:
                pf.budget_by_month.setdefault(code_s, {})[month] = v
        pf.data_rows += 1


# ── debit/credit convention ────────────────────────────────────────────────

def indicator_guess(values: list[str]) -> dict[str, str] | None:
    """The obvious reading of an indicator column, or None when there is none.

    Returned mapping: indicator text → 'debit' | 'credit'. A guess is still
    confirmed by a question unless the install already remembers it.
    """
    def key(v: str) -> str:
        return v.strip().upper().rstrip(".")

    up = [key(v) for v in values if v.strip()]
    pairs = [({"D", "DR", "DEBIT", "เดบิต", "ด"}, {"C", "CR", "CREDIT", "เครดิต", "ค"}),
             ({"S", "SOLL"}, {"H", "HABEN"}),  # SAP Soll/Haben
             ({"1"}, {"2"}), ({"+"}, {"-"})]
    for deb, cre in pairs:
        if set(up) <= (deb | cre) and set(up) & deb and set(up) & cre:
            return {v: ("debit" if key(v) in deb else "credit") for v in values if v.strip()}
    return None


def apply_date_order(pf: ParsedFile, order: str | None) -> int:
    """Read the ambiguous dates of a column whose order nothing decided as
    `order` ('dmy' | 'mdy'). Only rows typed with day and month both 1-12
    move; real date cells, 'yyyy-mm-dd' and Thai month names never do.
    Idempotent — `date_order['applied']` says which order the rows hold.
    Returns the number of rows whose date changed."""
    info = pf.date_order
    if not info or order not in ("dmy", "mdy") or info.get("source") != "undecided" \
            or info.get("applied") == order:
        return 0
    n = 0
    for r in pf.rows:
        if r.date_swappable and r.date is not None and r.date.day != r.date.month:
            r.date = _dt.date(r.date.year, r.date.day, r.date.month)
            n += 1
    info["applied"] = order
    return n


def apply_convention(pf: ParsedFile, convention: dict | str | None) -> int:
    """Resolve direction for indicator/signed rows. Returns rows left unresolved.

    convention: for 'indicator' mode a mapping {indicator: 'debit'|'credit'};
    for 'signed' mode 'positive_debit' | 'positive_credit'.
    """
    if pf.dc_mode == "columns" or not pf.rows:
        return 0
    unresolved = 0
    for r in pf.rows:
        amt = r.debit + r.credit
        if pf.dc_mode == "signed":
            pos_debit = convention != "positive_credit"
            is_debit = (r.raw_indicator == "+") == pos_debit
        else:
            side = (convention or {}).get(r.raw_indicator) if isinstance(convention, dict) else None
            if side not in ("debit", "credit"):
                unresolved += 1
                side = "debit"
            is_debit = side == "debit"
        r.debit, r.credit = (amt, ZERO) if is_debit else (ZERO, amt)
    return unresolved


def account_level_rows(pf: ParsedFile, period_end: _dt.date, income=None) -> None:
    """TB / P&L rows carry no date: they are the period's balances.

    income(code) says which accounts are income in the file's chart (the
    package's chart of accounts); without it, a code starting with 4 is.
    """
    for r in pf.rows:
        if r.date is None:
            r.date = period_end
    if pf.doc_type == "pl":
        # A P&L export lists positive amounts for both income and expense lines.
        for r in pf.rows:
            amt = r.debit + r.credit
            is_income = income(r.account_code) if income else account_digits(r.account_code).startswith("4")
            if is_income:
                r.debit, r.credit = ZERO, amt
            else:
                r.debit, r.credit = amt, ZERO
