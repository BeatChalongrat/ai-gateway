"""What a person decides in a reconciliation → clarification cards, in the order they are asked.

    bankrec_side:<file>      a file whose columns are only เดบิต/เครดิต and whose side nothing
                             else says: the bank's, the company's, or not used (a skipped card
                             sets the file aside — it is then on the round's "not included" list)
    missing_file             a side with no file at all
    bankrec_account          a ledger that holds several accounts: which one is this bank account
    bankrec_closing:<side>   a balance the file does not show: typed by the person
    bankrec_carried          the opening balances differ and no list of items carried in came
    e3_confirm_all / e3_candidate / e3_unmatched   the match itself (engine/e3/cards.py)

A pair is matched only by a rule of the exact tier or by a person's answer; a skipped card never
matches one. A large row without a partner that nobody classifies stays out of both adjusted
balances, so the reconciliation does not tie until a person has said what it is.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine import words
from ....engine.e3 import cards as E3C
from ....engine.findings import Finding, finding
from ....engine.thai import fmt_money
from ..words import W
from . import classify as C

BANK_ROW = W.table({"book": "row.bank.book", "bank_error": "row.bank.bank_error", "review": "row.review"})
BOOK_ROW = W.table({"timing": "row.book.timing", "book_error": "row.book.book_error", "review": "row.review"})
SPECIFIC = ("bank_fee", "interest", "wht_interest", "returned_cheque", "direct_debit")


def side_options() -> list:
    return [(words.cap(W("side.bank")), "bank", W("card.side.bank.d")),
            (words.cap(W("side.book")), "book", W("card.side.book.d")),
            (W("card.side.unused"), "unused", W("card.side.unused.d"))]


def side_card(u) -> Finding:
    d = u.data
    rows = [[r["date_raw"], E3C.safe(r["description"])[:60], r["a"] or "", r["b"] or "", r["balance"] or ""] for r in d["rows"][:5]]
    return finding(
        "bankrec_side", "blocking", "preflight", W("card.side.header"),
        W("card.side.question", name=E3C.safe(u.name)),
        side_options(), "unused", subject=u.key, leaves_out=True, rememberable=False,
        evidence={"file": u.name, "sheet": d["sheet"], "columns": [W("col.date"), W("col.item"), W("col.dr"), W("col.cr"), W("col.balance")], "rows": rows},
        facts={"file": u.name, "layout": d["layout"]})


def missing_card(rv) -> Finding | None:
    want = []
    if not rv.bank_files:
        want.append({"role": "bank", "label": W("need.bank"),
                     "why": W("need.bank.why"), "required": True,
                     "accept": [".xlsx", ".csv", ".pdf"]})
    if not rv.book_files:
        want.append({"role": "book", "label": W("need.book"),
                     "why": W("need.book.why"), "required": True, "accept": [".xlsx", ".csv"]})
    if not want:
        return None
    names = words.and_(w["label"] for w in want)
    unused = [u.name for u, why in rv.others if why != "same_file"]
    return finding("missing_file", "blocking", "preflight", W("card.missing.header"),
                   W("card.missing.question_unused", names=names, extra=W("card.missing.unused", files=E3C.safe(", ".join(unused[:3]))))
                   if unused else W("card.missing.question", names=names),
                   [(W("opt.wait"), "wait", W("opt.wait.d")),
                    (W("opt.cancel"), "cancel", W("opt.cancel.d"))], "wait", kind="request_files",
                   subject="+".join(w["role"] for w in want), request=want, facts={"missing": [w["role"] for w in want]},
                   evidence={"columns": [W("col.received"), W("col.used_as")], "rows": _received(rv)})


def _received(rv) -> list:
    from .review import SIDE_TH

    rows = [[u.name, words.cap(SIDE_TH["bank"])] for u in rv.bank_files] + [[u.name, words.cap(SIDE_TH["book"])] for u in rv.book_files]
    rows += [[u.name, W("used.carried")] for u in rv.carried_files] + [[u.name, W("used.none")] for u, _ in rv.others]
    return rows or [[W("used.no_file"), ""]]


def _where(cell: dict | None) -> str:
    c = cell or {}
    return " · ".join(str(x) for x in (c.get("file"), c.get("sheet"), W("where.row", row=c["row"]) if c.get("row") else "", c.get("cell")) if x)


def account_card(rv) -> Finding:
    cands = rv.gl_candidates[:4]
    options = [(f"{c['code']} {E3C.safe(c['name'])}".strip()[:60], c["code"], W("card.account.rows", rows=c["rows"])) for c in cands]
    return finding(
        "bankrec_account", "blocking", "preflight", W("card.account.header"),
        W("card.account.question_number", number=rv.account["number"]) if rv.account.get("number") else W("card.account.question"),
        options, cands[0]["code"], rememberable=False,
        evidence={"columns": [W("col.account_code"), W("col.account_name"), W("col.rows")],
                  "rows": [[c["code"], c["name"], c["rows"]] for c in cands]},
        facts={"candidates": [c["code"] for c in cands]})


def closing_card(side: str, rv) -> Finding:
    from .review import SIDE_TH

    b = rv.balances[side]
    return finding(
        "bankrec_closing", "blocking", "preflight", W("card.closing.header"),
        W("card.closing.question", side=SIDE_TH[side], end=E3C.date_th(rv.end), movement=fmt_money(b.movement)),
        [(W("card.closing.unknown"), "unknown", W("card.closing.unknown.d")),
         (W("card.closing.zero"), "zero", W("card.closing.zero.d"))],
        "unknown", subject=side, rememberable=False, input={"kind": "money", "hint": W("card.closing.hint")},
        evidence={"file": ", ".join(u.name for u in (rv.bank_files if side == "bank" else rv.book_files))},
        facts={"side": side, "movement": f"{b.movement:.2f}"})


def carried_card(rv, gap: Decimal) -> Finding:
    bank, book = rv.balances["bank"], rv.balances["book"]
    return finding(
        "bankrec_carried", "blocking", "preflight", W("card.carried.header"),
        W("card.carried.question", bank=fmt_money(bank.opening), book=fmt_money(book.opening), gap=fmt_money(abs(gap))),
        [(W("card.carried.without"), "proceed_without", W("card.carried.without.d")),
         (W("opt.wait"), "wait", W("opt.wait.d"))],
        "wait", kind="request_files", subject="carried",
        request=[{"role": "carried", "label": W("need.carried"),
                  "why": W("need.carried.why"),
                  "required": False, "accept": [".xlsx", ".csv"]}],
        evidence={"columns": [W("col.side"), W("col.opening"), W("col.read_from")],
                  "rows": [[W("word.bank"), f"{bank.opening:,.2f}", _where(bank.opening_cell)],
                           [W("word.books"), f"{book.opening:,.2f}", _where(book.opening_cell)]]},
        facts={"opening_bank": f"{bank.opening:.2f}", "opening_book": f"{book.opening:.2f}", "gap": f"{gap:.2f}"})


def lone_card(rv):
    """The card for a large row without a partner, in the words of a bank reconciliation."""
    def make(row: dict, names: dict, moment: str) -> Finding | None:
        amount = Decimal(row["amount"])
        if row["side"] == "right":
            ln = _line(rv, row["id"])
            guess = C.of_bank_row(ln.text if ln else row.get("name") or "", amount, pdf=bool(ln and ln.pdf))
            return E3C.lone_card(
                row, names, moment, default="review",
                question=W("card.lone.bank", row=E3C.line(_shown(rv, row)), type=C.TYPE_TH[guess]),
                options=[(BANK_ROW["book"], "book", W("row.bank.book.d", type=C.TYPE_TH[guess])),
                         (BANK_ROW["bank_error"], "bank_error", W("row.bank.bank_error.d")),
                         (BANK_ROW["review"], "review", W("row.review.d"))])
        ln = _line(rv, row["id"])
        guess = C.of_book_row(ln.cheque if ln else "", amount, ln.carried_kind if ln else None)
        return E3C.lone_card(
            row, names, moment, default="review",
            question=W("card.lone.book", row=E3C.line(_shown(rv, row))),
            options=[(BOOK_ROW["timing"], "timing", W("row.book.timing.d", type=C.TYPE_TH[guess])),
                     (BOOK_ROW["book_error"], "book_error", W("row.book.book_error.d")),
                     (BOOK_ROW["review"], "review", W("row.review.d"))])
    return make


def _line(rv, rid: str):
    for ln in rv.book + rv.carried + rv.bank:
        if ln.id == rid:
            return ln
    return None


def _shown(rv, row: dict) -> dict:
    """The row with the line as the file wrote it (the engine compares a cleaned text)."""
    ln = _line(rv, row["id"])
    return dict(row, name=ln.text if ln else row.get("name"))


def as_written(rv, result: dict) -> dict:
    """The match with every row's text as its file wrote it — what a person reads on a card and in
    the working paper (the engine compares the text without the words about how money moved)."""
    def fix(r):
        return _shown(rv, r)

    def both(e):
        return dict(e, left=[fix(r) for r in e["left"]], right=[fix(r) for r in e["right"]])
    g = result["groups"]
    return dict(result, groups={
        "matched": [both(m) for m in g["matched"]], "missing": [fix(r) for r in g["missing"]],
        "extra": [fix(r) for r in g["extra"]], "suspect": [both(s) for s in g["suspect"]]})


def findings(rv) -> list:
    out = [side_card(u) for u in rv.waiting_for_side]
    if out:
        return out                      # which file is which comes before anything else can be judged
    m = missing_card(rv)
    if m is not None:
        return [m]
    if rv.waiting_for_account:
        return [account_card(rv)]
    if not rv.ready:
        return []
    for side in ("bank", "book"):
        if rv.balances[side].closing is None and f"bankrec_closing:{side}" not in rv.answers:
            out.append(closing_card(side, rv))
    bank, book = rv.balances["bank"], rv.balances["book"]
    if bank.opening is not None and book.opening is not None and not rv.carried_files \
            and bank.opening != book.opening and rv.answers.get("bankrec_carried") != "proceed_without":
        out.append(carried_card(rv, bank.opening - book.opening))
    out += E3C.findings(as_written(rv, rv.match), rv.answers, rv_names(), tolerance=rv.tolerance, lone=lone_card(rv))
    return out


def rv_names() -> dict:
    from .review import NAMES

    return NAMES
