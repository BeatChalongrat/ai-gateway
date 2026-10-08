"""The run judged: which file is which side, the period and its cut-off, the balances, the match
(engine/e3), and what every row without a partner is.

preflight and compute both run this review — preflight to ask what a person must decide, compute
to build the statement from the same judgement with the answers in it. Nothing here reads a clock
(the run's date is handed in) and no model is asked anything.

The two tables handed to the E3 engine:

    left   the company's ledger of the account, this period — and the items carried in from the
           month before (they are ledger rows of earlier periods the bank had not shown yet)
    right  the bank's statement, this period

A row dated outside the period is matched with nothing (cut-off): it is listed, and when a row of
the period has its counterpart on the other side of the cut-off, that is an exception for a person.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from ....engine import e3
from ....engine import thai as TH
from ....engine import words
from ....engine.e3 import cards as E3C
from ....engine.e3.match import SETTING_TH, Tolerance, tolerance_from
from ....engine.e3.rows import Row
from ..words import W
from . import classify as C
from .inputs import Upload

ZERO = Decimal("0.00")
NAMES = W.table({"left": "names.left", "right": "names.right"})
# the install's settings are flat, one value each, so that the settings page can show and edit them:
# match_<the engine's setting>, and the account the statement is headed with
MATCH_PREFIX = "match_"
ACCOUNT_KEYS = {"bank": "bank_name", "number": "bank_account_number", "name": "bank_account_name", "gl_code": "bank_gl_code"}


def matching_settings(defaults: dict) -> dict:
    from ....engine.e3.match import SETTINGS

    return {k: defaults[MATCH_PREFIX + k] for k in SETTINGS if defaults.get(MATCH_PREFIX + k) not in (None, "")}


def account_settings(defaults: dict) -> dict:
    return {k: " ".join(str(defaults.get(key) or "").split()) for k, key in ACCOUNT_KEYS.items()}
SIDE_TH = W.table({"bank": "side.bank", "book": "side.book"})
CUTOFF_PASSES = ("ref_amount_date", "ref_amount", "date_amount")


def D(v) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else ZERO


@dataclass
class Line:
    """A row of a statement or a ledger, signed from the company's point of view (money in > 0)."""
    id: str
    side: str                 # bank | book | carried
    upload: Upload
    raw: dict
    amount: Decimal
    date: _dt.date
    ref: str
    text: str
    balance: Decimal | None = None
    carried_kind: str | None = None

    @property
    def cheque(self) -> str:
        return self.raw.get("cheque") or ""

    @property
    def pdf(self) -> bool:
        """The row was read from a PDF: its words may have lost their Thai marks."""
        return bool(self.raw.get("from_pdf"))

    def source(self) -> dict:
        d = self.upload.data
        return {"file": self.upload.name, "sheet": d.get("sheet"), "row": self.raw["row"], "cells": self.raw.get("cells") or {}}

    def to_row(self, table: str) -> Row:
        tags = tuple(t for t in (("carried",) if self.side == "carried" else ()) + (("cheque",) if self.cheque else ()))
        return Row(id=self.id, side=table, amount=self.amount, date=self.date, ref=self.ref,
                   name=C.who(self.text, pdf=self.pdf) or self.text, source=self.source(), tags=tags)

    def out(self) -> dict:
        return {"id": self.id, "side": self.side, "date": self.date.isoformat(), "amount": f"{self.amount:.2f}", "ref": self.ref,
                "description": self.text, "cheque": self.cheque, **self.source()}


@dataclass
class Balance:
    side: str
    opening: Decimal | None = None
    closing: Decimal | None = None
    opening_from: str = ""        # row | balance_column | typed | zero | movement | unknown
    closing_from: str = ""
    opening_cell: dict | None = None
    closing_cell: dict | None = None
    movement: Decimal = ZERO
    gaps: list = field(default_factory=list)

    def out(self) -> dict:
        f = lambda v: None if v is None else f"{v:.2f}"   # noqa: E731
        return {"side": self.side, "opening": f(self.opening), "closing": f(self.closing), "opening_from": self.opening_from,
                "closing_from": self.closing_from, "opening_cell": self.opening_cell, "closing_cell": self.closing_cell,
                "movement": f"{self.movement:.2f}", "gaps": self.gaps}


@dataclass
class Review:
    uploads: list
    defaults: dict
    answers: dict
    today: _dt.date
    tolerance: Tolerance = field(default_factory=Tolerance)
    unusable_settings: list = field(default_factory=list)
    sides: dict = field(default_factory=dict)          # upload key → bank | book | unused | None (to ask)
    bank_files: list = field(default_factory=list)
    book_files: list = field(default_factory=list)
    carried_files: list = field(default_factory=list)
    others: list = field(default_factory=list)          # (upload, why)
    account: dict = field(default_factory=dict)
    gl_code: str | None = None
    gl_candidates: list = field(default_factory=list)
    gl_from: str = ""
    period: str | None = None
    period_source: str = ""
    start: _dt.date | None = None
    end: _dt.date | None = None
    bank: list = field(default_factory=list)            # Lines of the period
    book: list = field(default_factory=list)
    carried: list = field(default_factory=list)
    outside: list = field(default_factory=list)         # Lines outside the period (cut-off)
    unread: list = field(default_factory=list)          # rows with an amount and no readable date
    balances: dict = field(default_factory=dict)
    match: dict | None = None
    cutoff_pairs: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.bank_files and self.book_files and self.period and not self.waiting_for_side and not self.waiting_for_account)

    @property
    def waiting_for_side(self) -> list:
        return [u for u in self.uploads if u.kind == "table" and self.sides.get(u.key) is None]

    @property
    def waiting_for_account(self) -> bool:
        return bool(self.gl_candidates) and self.gl_code is None


# ── signs ────────────────────────────────────────────────────────────────

def signed(raw: dict, layout: str, side: str) -> Decimal:
    a, b = D(raw.get("a")), D(raw.get("b"))
    if layout == "out_in":
        return abs(b) - abs(a)                       # ถอน/ฝาก, จ่าย/รับ
    if layout == "debit_credit":
        # the bank's debit takes money out of the account; the company's debit to its bank account puts money in
        return (abs(b) - abs(a)) if side == "bank" else (abs(a) - abs(b))
    kind = C.key(raw.get("kind"))
    if kind in ("dr", "debit", "d", "ถอน", "เดบิต", "จ่าย", "x2"):
        return -abs(a) if side == "bank" else abs(a)
    if kind in ("cr", "credit", "c", "ฝาก", "เครดิต", "รับ", "x1"):
        return abs(a) if side == "bank" else -abs(a)
    return a


def line_id(letter: str, u: Upload, row: int) -> str:
    return f"{letter}{u.sha256[:6]}.{u.data.get('sheet_index', 0)}.{row}"


# ── the review ───────────────────────────────────────────────────────────

def review(uploads: list, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
           today: _dt.date | None = None, period: str | None = None) -> Review:
    """``period``: the period preflight settled on (compute judges the same month it asked about)."""
    from . import cards as K

    rv = Review(uploads=list(uploads), defaults=defaults or {}, answers=answers or {}, today=today or _dt.date(2000, 1, 1))
    rv.tolerance, rv.unusable_settings = tolerance_from(matching_settings(rv.defaults))
    _sides(rv)
    _account(rv)
    if rv.bank_files and rv.book_files and not rv.waiting_for_side:
        _period(rv, message)
        if period:
            source = rv.period_source if rv.period == period else "message"
            rv.period, rv.period_source = period, source
            rv.start, rv.end = TH.period_bounds(period)
    if rv.ready:
        _lines(rv)
        _balances(rv)
        _match(rv)
    rv.findings = K.findings(rv)
    rv.assumptions = _assumptions(rv)
    return rv


def _sides(rv: Review) -> None:
    seen: dict[str, Upload] = {}
    for u in rv.uploads:
        if u.sha256 in seen:
            rv.others.append((u, "same_file"))
            continue
        seen[u.sha256] = u
        if u.kind == "carried":
            rv.carried_files.append(u)
        elif u.kind != "table":
            rv.others.append((u, u.data.get("reason") or "unsupported_type"))
        else:
            said = rv.answers.get(f"bankrec_side:{u.key}")
            side = said if said in ("bank", "book", "unused") else u.side
            rv.sides[u.key] = side
            if side == "bank":
                rv.bank_files.append(u)
            elif side == "book":
                rv.book_files.append(u)
            elif side == "unused":
                rv.others.append((u, "set_aside"))
    first = lambda u: min((r["date"] for r in u.data["rows"]), default="")   # noqa: E731
    rv.bank_files.sort(key=lambda u: (first(u), u.name))
    rv.book_files.sort(key=lambda u: (first(u), u.name))


def _account(rv: Review) -> None:
    """Which account this is — and, in a ledger that holds several, which of them."""
    acc = account_settings(rv.defaults)
    meta = rv.bank_files[0].data["meta"] if rv.bank_files else {}
    rv.account = {
        "bank": acc.get("bank") or meta.get("bank") or "",
        "number": acc.get("number") or meta.get("account_number") or "",
        "name": acc.get("name") or meta.get("account_name") or "",
        "from": {k: ("settings" if acc.get(k) else "statement" if meta.get(m) else "")
                 for k, m in (("bank", "bank"), ("number", "account_number"), ("name", "account_name"))},
    }
    codes: dict[str, dict] = {}
    for u in rv.book_files:
        for a in u.data.get("accounts") or []:
            c = codes.setdefault(a["code"], {"code": a["code"], "name": a["name"], "rows": 0})
            c["rows"] += a["rows"]
    wanted = str(acc.get("gl_code") or "").strip()
    if len(codes) <= 1:
        # one account in the ledger is that account; a ledger with no account column takes the install's
        # setting (AM8: it was read after this return, so the paper said "ไม่ระบุ" with the code set)
        if codes:
            rv.gl_code, rv.gl_from = next(iter(codes)), "only_account"
        else:
            rv.gl_code, rv.gl_from = (wanted or None), ("settings" if wanted else "only_account")
        return
    said = rv.answers.get("bankrec_account")
    if isinstance(said, str) and said in codes:
        rv.gl_code, rv.gl_from = said, "answer"
    elif wanted and wanted in codes:
        rv.gl_code, rv.gl_from = wanted, "settings"
    else:
        bankish = [c for c in codes.values() if any(w in C.key(c["name"]) for w in ("ธนาคาร", "เงินฝาก", "bank", "กระแสรายวัน", "ออมทรัพย์"))]
        if len(bankish) == 1:
            rv.gl_code, rv.gl_from = bankish[0]["code"], "name"
        else:
            pool = bankish or list(codes.values())
            rv.gl_candidates = sorted(pool, key=lambda c: (-c["rows"], c["code"]))[:4]


def _period(rv: Review, message: str) -> None:
    named = TH.find_period_in_text(message or "", rv.today)
    months: dict[str, int] = {}
    for u in rv.bank_files:
        for r in u.data["rows"]:
            m = r["date"][:7]
            months[m] = months.get(m, 0) + 1
    if named:
        rv.period, rv.period_source = named, "message"
    elif months:
        rv.period, rv.period_source = max(months, key=lambda m: (months[m], m)), "statement"
    if rv.period:
        rv.start, rv.end = TH.period_bounds(rv.period)


def _lines(rv: Review) -> None:
    def of(files, side, letter):
        for u in files:
            d = u.data
            for raw in d["rows"]:
                if side == "book" and rv.gl_code and raw.get("account") and raw["account"] != rv.gl_code:
                    continue
                amount = signed(raw, d["layout"], side)
                date = _dt.date.fromisoformat(raw["date"])
                ref = raw.get("cheque") or raw.get("doc_no") or ""
                ln = Line(id=line_id(letter, u, raw["row"]), side=side, upload=u, raw=raw, amount=amount, date=date, ref=ref,
                          text=raw.get("description") or "", balance=None if raw.get("balance") is None else D(raw["balance"]))
                yield ln
            for s in d["skipped"]:
                if s["why"] == "no_date":
                    rv.unread.append({"file": u.name, "sheet": d["sheet"], "row": s["row"], "side": side, "text": s.get("text") or ""})

    for ln in of(rv.bank_files, "bank", "b"):
        (rv.bank if rv.start <= ln.date <= rv.end else rv.outside).append(ln)
    for ln in of(rv.book_files, "book", "k"):
        (rv.book if rv.start <= ln.date <= rv.end else rv.outside).append(ln)
    for u in rv.carried_files:
        for raw in u.data["rows"]:
            amount = D(raw["amount"])
            kind = raw["kind"]
            # a list of open items writes amounts without a sign: a cheque or a payment takes money out
            amount = -abs(amount) if kind in ("outstanding_cheque", "outstanding_payment") else abs(amount)
            date = _dt.date.fromisoformat(raw["date"])
            if date >= rv.start:
                continue    # an item of this period is in this period's ledger already
            rv.carried.append(Line(id=line_id("c", u, raw["row"]), side="carried", upload=u,
                                   raw={**raw, "cheque": raw["ref"] if kind == "outstanding_cheque" else ""},
                                   amount=amount, date=date, ref=raw["ref"], text=raw["description"], carried_kind=kind))
    order = lambda ln: (ln.date, ln.upload.name, ln.raw["row"])   # noqa: E731
    rv.bank.sort(key=order)
    rv.book.sort(key=order)
    rv.carried.sort(key=order)
    rv.outside.sort(key=order)


# ── balances ─────────────────────────────────────────────────────────────

def _file_order(u: Upload) -> list:
    """The file's rows and balance marks as one list, oldest first (some banks print the newest first)."""
    rows = [dict(r, _kind="row") for r in u.data["rows"]] + [dict(m, _kind=m["what"]) for m in u.data.get("marks") or []]
    rows.sort(key=lambda r: r["row"])
    dated = [r["date"] for r in rows if r["_kind"] == "row"]
    if len(dated) >= 2 and dated[0] > dated[-1]:
        rows.reverse()
    return rows


def _balances(rv: Review) -> None:
    for side, files, lines in (("bank", rv.bank_files, rv.bank), ("book", rv.book_files, rv.book)):
        b = Balance(side=side, movement=sum((ln.amount for ln in lines), ZERO))
        before = after = None       # the balance on the last row before the period, and on the last row of it
        first_in = None
        for u in files:
            d = u.data
            last = None             # (balance, row) of the row before, within this file
            for r in _file_order(u):
                cell = {"file": u.name, "sheet": d["sheet"], "row": r["row"], "cell": (r.get("cells") or {}).get("balance")}
                if side == "book" and rv.gl_code and r.get("account") and r["account"] != rv.gl_code:
                    continue        # another account of the same ledger
                if r["_kind"] != "row":
                    bal = r.get("balance")
                    if bal is None and side == "book" and (r.get("a") or r.get("b")):
                        bal = f"{signed(r, d['layout'], side):.2f}"      # ยอดยกมา written in the debit or credit column
                    if bal is None:
                        continue
                    when = r.get("date")
                    if r["_kind"] == "opening" and (when is None or when <= rv.end.isoformat()) and first_in is None and before is None:
                        before = (D(bal), cell, "row")
                    elif r["_kind"] == "closing" and first_in is not None:
                        after = (D(bal), cell, "row")
                    last = (D(bal), r["row"])
                    continue
                amount = signed(r, d["layout"], side)
                date = _dt.date.fromisoformat(r["date"])
                bal = None if r.get("balance") is None else D(r["balance"])
                if bal is not None and last is not None and last[0] + amount != bal:
                    b.gaps.append({"file": u.name, "sheet": d["sheet"], "row": r["row"], "after_row": last[1],
                                   "expected": f"{last[0] + amount:.2f}", "found": f"{bal:.2f}",
                                   "difference": f"{bal - (last[0] + amount):.2f}"})
                if date < rv.start:
                    if bal is not None:
                        before = (bal, cell, "balance_column")
                elif date <= rv.end:
                    if first_in is None:
                        first_in = (amount, bal, cell)
                    if bal is not None:
                        after = (bal, cell, "balance_column")
                if bal is not None:
                    last = (bal, r["row"])
        if before is None and first_in is not None and first_in[1] is not None:
            before = (first_in[1] - first_in[0], first_in[2], "balance_column")
        said = rv.answers.get(f"bankrec_closing:{side}")
        typed = said.get("typed") if isinstance(said, dict) else None
        if before is not None:
            b.opening, b.opening_cell, b.opening_from = before
        if after is not None:
            b.closing, b.closing_cell, b.closing_from = after
        elif b.opening is not None:
            b.closing, b.closing_from = b.opening + b.movement, "movement"
        if b.closing is None:
            value = None
            if typed is not None:
                from .inputs import money

                m = money(typed)
                value = D(m) if m is not None else None
                b.closing_from = "typed" if value is not None else ""
            elif said == "zero":
                value, b.closing_from = ZERO + b.movement, "zero"
            if value is not None:
                b.closing = value
        if b.opening is None and b.closing is not None:
            b.opening, b.opening_from = b.closing - b.movement, "movement"
        if b.closing is None:
            b.opening_from = b.closing_from = "unknown"
        rv.balances[side] = b


# ── the match ────────────────────────────────────────────────────────────

def _match(rv: Review) -> None:
    left = [ln.to_row("left") for ln in rv.book] + [ln.to_row("left") for ln in rv.carried]
    right = [ln.to_row("right") for ln in rv.bank]
    decisions, keys, _ = E3C.decisions_from(rv.answers)
    rv.match = e3.run(left, right, tolerance=rv.tolerance, decisions=decisions, keys=keys)
    # the other side of the cut-off: a row of the period whose counterpart is dated outside it
    open_l = [r["id"] for r in rv.match["groups"]["missing"]]
    open_r = [r["id"] for r in rv.match["groups"]["extra"]]
    by_id = {ln.id: ln for ln in rv.book + rv.carried + rv.bank + rv.outside}
    out_bank = [ln.to_row("right") for ln in rv.outside if ln.side == "bank"]
    out_book = [ln.to_row("left") for ln in rv.outside if ln.side == "book"]
    passes = e3.passes_by_id(*CUTOFF_PASSES)
    for l_rows, r_rows in (([by_id[i].to_row("left") for i in open_l], out_bank),
                           (out_book, [by_id[i].to_row("right") for i in open_r])):
        if l_rows and r_rows:
            got = e3.run(l_rows, r_rows, passes=passes, tolerance=rv.tolerance)
            for m in got["groups"]["matched"]:
                rv.cutoff_pairs.append({"pass": m["pass"], "left": m["left"], "right": m["right"], "days_apart": m["days_apart"]})


def line_of(rv: Review, rid: str) -> Line:
    for ln in rv.book + rv.carried + rv.bank + rv.outside:
        if ln.id == rid:
            return ln
    raise KeyError(rid)


# ── what the run assumed ─────────────────────────────────────────────────

def _assumptions(rv: Review) -> list[dict]:
    out = []
    for u, why in rv.others:
        say = {"same_file": W("unused.same_file"),
               "set_aside": W("unused.set_aside"),
               "unrecognised_sheet": W("unused.unrecognised_sheet"),
               "unsupported_type": W("unused.unsupported_type"), "unreadable": W("unused.unreadable"),
               "scanned_pdf": W("unused.scanned_pdf")}.get(why, W("used.none"))
        out.append({"code": "file_not_used", "source": "rule", "text": W("assume.file_not_used", name=u.name, say=say)})
    for k in rv.unusable_settings:
        out.append({"code": "setting_not_usable", "source": "rule", "changes_numbers": True,
                    "text": W("assume.setting", setting=SETTING_TH[k])})
    if not rv.ready:
        return out
    label = words.period(rv.period)
    src = {"message": W("assume.period.message"), "statement": W("assume.period.statement")}.get(rv.period_source, "")
    out.append({"code": "period", "source": "rule" if rv.period_source != "message" else "answer",
                "text": W("assume.period", label=label, src=src, end=E3C.date_th(rv.end))})
    if rv.gl_code and rv.gl_from in ("name", "settings", "answer"):
        how = {"name": W("assume.gl.name"), "settings": W("assume.gl.settings"),
               "answer": W("assume.gl.answer")}[rv.gl_from]
        out.append({"code": "gl_account", "source": "rule" if rv.gl_from == "name" else "answer",
                    "text": W("assume.gl", gl_code=rv.gl_code, how=how), "changes_numbers": True})
    t = rv.tolerance
    out.append({"code": "tolerance", "source": "rule",
                "text": W("assume.tolerance", amount=TH.fmt_money(t.amount), days=t.days, ask_above=TH.fmt_money(t.ask_above))})
    if rv.outside:
        out.append({"code": "cut_off", "source": "rule",
                    "text": W("assume.cut_off", n=len(rv.outside), label=label)})
    if rv.unread:
        out.append({"code": "rows_without_date", "source": "rule", "changes_numbers": True,
                    "text": W("assume.unread", n=len(rv.unread))})
    for side in ("bank", "book"):
        b = rv.balances[side]
        if b.closing_from == "movement":
            out.append({"code": f"{side}_closing_from_movement", "source": "rule", "changes_numbers": True,
                        "text": W("assume.closing_movement", side=SIDE_TH[side])})
        if b.closing_from == "typed":
            out.append({"code": f"{side}_closing_typed", "source": "answer", "changes_numbers": True,
                        "text": W("assume.closing_typed", side=SIDE_TH[side], closing=TH.fmt_money(b.closing))})
    if not rv.carried_files and rv.answers.get("bankrec_carried") == "proceed_without":
        out.append({"code": "no_carried_items", "source": "answer", "changes_numbers": True,
                    "text": W("assume.no_carried")})
    return out
