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

from .headers import HeaderMap, find_header, header_by_profile, infer_missing, map_headers, norm
from .tabular import Sheet, Workbook, col_letter
from .thai import find_period_in_text, parse_date

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
        d = parse_date(raw_date)
        if "date" in f and d is None:
            # A posting without a date is a total/label line in every export we saw.
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
            amount_cols=cols, raw_indicator=indicator,
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
    up = [v.strip().upper() for v in values if v.strip()]
    pairs = [({"D", "DR", "DEBIT", "เดบิต", "ด"}, {"C", "CR", "CREDIT", "เครดิต", "ค"}),
             ({"S"}, {"H"}),  # SAP Soll/Haben
             ({"1"}, {"2"}), ({"+"}, {"-"})]
    for deb, cre in pairs:
        if set(up) <= (deb | cre) and set(up) & deb and set(up) & cre:
            return {v: ("debit" if v.strip().upper() in deb else "credit") for v in values if v.strip()}
    return None


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


def account_level_rows(pf: ParsedFile, period_end: _dt.date) -> None:
    """TB / P&L rows carry no date: they are the period's balances."""
    for r in pf.rows:
        if r.date is None:
            r.date = period_end
    if pf.doc_type == "pl":
        # A P&L export lists positive amounts for both income and expense lines.
        for r in pf.rows:
            amt = r.debit + r.credit
            d = account_digits(r.account_code)
            if d.startswith("4"):
                r.debit, r.credit = ZERO, amt
            else:
                r.debit, r.credit = amt, ZERO
