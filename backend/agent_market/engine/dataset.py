"""(De)serialise parsed files so a parse is done once per file per run.

The runner keeps each file's parse as its own step output; a resumed run, a
late upload or a re-render reads these instead of parsing again.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

from .headers import HeaderMap
from .ledger import LedgerRow, ParsedFile

FORMAT = "am.parsed.v1"


def dump(pf: ParsedFile) -> dict:
    hm = pf.header
    return {
        "format": FORMAT,
        "name": pf.name, "sha256": pf.sha256, "size": pf.size, "kind": pf.kind,
        "doc_type": pf.doc_type, "sheet": pf.sheet, "skipped_rows": pf.skipped_rows,
        "data_rows": pf.data_rows, "period_hint": pf.period_hint, "dc_mode": pf.dc_mode,
        "indicator_values": pf.indicator_values, "currencies": pf.currencies,
        "account_names": pf.account_names, "error": pf.error, "encoding": pf.encoding,
        "raw_date_texts": pf.raw_date_texts[:400],
        "budget": {k: str(v) for k, v in pf.budget.items()},
        "budget_by_month": {k: {str(m): str(v) for m, v in by.items()} for k, by in pf.budget_by_month.items()},
        "header": None if hm is None else {
            "sheet": hm.sheet, "header_row": hm.header_row, "headers": hm.headers,
            "fields": hm.fields, "source": hm.source,
            "month_columns": {str(k): v for k, v in hm.month_columns.items()},
            "title_text": hm.title_text,
        },
        "rows": [[r.row, r.date.isoformat() if r.date else None, r.account_code, r.account_name,
                  r.description, str(r.debit), str(r.credit), r.currency, r.cost_center, r.doc_no,
                  r.amount_cols, r.raw_indicator] for r in pf.rows],
    }


def load(d: dict) -> ParsedFile:
    if d.get("format") != FORMAT:
        raise ValueError(f"unknown parsed format {d.get('format')!r}")
    h = d.get("header")
    hm = None
    if h:
        hm = HeaderMap(sheet=h["sheet"], header_row=h["header_row"], headers=h["headers"],
                       fields={k: int(v) for k, v in h["fields"].items()}, source=h["source"],
                       month_columns={int(k): v for k, v in h["month_columns"].items()},
                       title_text=h.get("title_text", ""))
    pf = ParsedFile(name=d["name"], sha256=d["sha256"], size=d["size"], kind=d["kind"],
                    doc_type=d["doc_type"], sheet=d["sheet"], header=hm,
                    skipped_rows=d["skipped_rows"], data_rows=d["data_rows"],
                    period_hint=d["period_hint"], dc_mode=d["dc_mode"],
                    indicator_values=d["indicator_values"], currencies=d["currencies"],
                    account_names=d["account_names"], error=d["error"], encoding=d.get("encoding"),
                    raw_date_texts=d.get("raw_date_texts", []))
    pf.budget = {k: Decimal(v) for k, v in d.get("budget", {}).items()}
    pf.budget_by_month = {k: {int(m): Decimal(v) for m, v in by.items()} for k, by in d.get("budget_by_month", {}).items()}
    for (row, date, code, name, desc, dr, cr, cur, cc, doc, cols, ind) in d["rows"]:
        pf.rows.append(LedgerRow(file=pf.name, sheet=pf.sheet or "", row=row,
                                 date=_dt.date.fromisoformat(date) if date else None,
                                 account_code=code, account_name=name, description=desc,
                                 debit=Decimal(dr), credit=Decimal(cr), currency=cur, cost_center=cc,
                                 doc_no=doc, amount_cols=cols, raw_indicator=ind))
    return pf


def dumps(pf: ParsedFile) -> str:
    return json.dumps(dump(pf), ensure_ascii=False, separators=(",", ":"))
