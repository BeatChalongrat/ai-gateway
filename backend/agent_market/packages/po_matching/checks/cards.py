"""What a person decides in a round → clarification cards, in the order they are asked.

    pomatch_kind:<file.sheet>   a table whose headers fit more than one kind of document
    missing_file                no purchase order lines, or nothing billed
    ap_gr_practice              whether the company issues goods receipts — asked once, with the
                                answer the round's own files suggest, and remembered
    e2_*                        a document's unclear values (engine/e2/cards.py)
    e3_candidate:<pair>         a proposed link: this line of the document and that PO line; or
                                an invoice that may be one presented before
    pomatch_supplier:<inv:po>   the supplier cannot be told by tax ID, and the names only resemble
    pomatch_unit:<from>to>      two units of measure nothing converts

A company that does not issue goods receipts is asked nothing about a missing one. A link is made
only by a rule of the exact tier or by a person's answer; a skipped card never makes one: its lines
wait as "ต้องให้คนตัดสิน".
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.e2 import cards as E2C
from ....engine.e2.schema import FIELD_KINDS
from ....engine.e3 import cards as E3C
from ....engine.e3.match import MATCH, SEPARATE, UNDECIDED
from ....engine.findings import Finding, finding
from ....engine import words
from ....engine.thai import fmt_money
from ..words import W
from . import book as B
from . import inputs as I
from . import links as L
from . import parties as P
from . import settings as S

# what a sheet may be, and the headings of a card's table of lines: keys of the manifest's sentences, each said
# in the language of the run when a card is made (engine/words.py)
KIND_OPTIONS = {"po": ("kind.po", "po", "card.kind.po.d"),
                "gr": ("kind.gr", "gr", "card.kind.gr.d"),
                "invoice": ("card.kind.invoice", "invoice", "card.kind.invoice.d"),
                "unused": ("card.kind.unused", "unused", "card.kind.unused.d")}
COLUMNS = ("col.document", "col.date", "col.item", "col.qty", "col.unit", "col.unit_price", "col.amount", "col.file", "col.row")


def kind_option(k: str) -> tuple:
    label, value, description = KIND_OPTIONS[k]
    return words.cap(W(label)), value, W(description)


def columns() -> list:
    return [W(k) for k in COLUMNS]


def kind_card(u, t: dict) -> Finding:
    could = [k for k in (t.get("could_be") or list(I.KINDS)) if k in KIND_OPTIONS][:3]
    heads = list(t["columns"])
    rows = [[str(r.get(f) or "") for f in heads[:6]] for r in t["rows"][:4]]
    return finding(
        "pomatch_kind", "blocking", "preflight", W("card.kind.header"),
        W("card.kind.question", sheet=E3C.safe(t["sheet"]), name=E3C.safe(u.name)),
        [kind_option(k) for k in could] + [kind_option("unused")], "unused", subject=f"{u.key}.{t['sheet_index']}",
        leaves_out=True, rememberable=False,
        evidence={"file": u.name, "sheet": t["sheet"], "columns": heads[:6], "rows": rows},
        facts={"file": u.name, "sheet": t["sheet"], "could_be": could})


def missing_card(rv) -> Finding | None:
    want = []
    if not rv.po:
        want.append({"role": "po", "label": W("need.po"),
                     "why": W("need.po.why"), "required": True,
                     "accept": [".xlsx", ".csv"]})
    if not rv.invoices and not any(d.status == "pending" for d in rv.docs):
        want.append({"role": "invoice", "label": W("need.invoice"),
                     "why": W("need.invoice.why"), "required": True,
                     "accept": [".pdf", ".xml", ".xlsx", ".csv"]})
    if not want:
        return None
    got = [[u.name, I.KIND_TH.get(k, k)] for u, _t, k in rv.tables] + [[d.upload.name, W("used.invoice_doc")] for d in rv.docs] + \
          [[u.name, words.cap(W("used.none"))] for u, _why, _d in rv.others]
    names = words.and_(w["label"] for w in want)
    return finding("missing_file", "blocking", "preflight", W("card.missing.header"),
                   W("card.missing.question", names=names),
                   [(W("opt.wait"), "wait", W("opt.wait.d")), (W("opt.cancel"), "cancel", W("opt.cancel.d"))],
                   "wait", kind="request_files", subject="+".join(w["role"] for w in want), request=want,
                   facts={"missing": [w["role"] for w in want]},
                   evidence={"columns": [W("col.received"), W("col.used_as")], "rows": got or [[W("used.no_file"), ""]]})


def practice_card(rv) -> Finding:
    if rv.gr_files:
        seen = W("card.practice.seen.yes", gr_files=rv.gr_files)
    else:
        seen = W("card.practice.seen.no")
    return finding(
        "ap_gr_practice", "blocking", "preflight", W("card.practice.header"),
        W("card.practice.question", seen=seen),
        [(S.GR_PRACTICE_VALUE_TH[S.GR_YES], S.GR_YES, W("card.practice.yes.d")),
         (S.GR_PRACTICE_VALUE_TH[S.GR_NO], S.GR_NO, W("card.practice.no.d"))],
        rv.gr_guess, recommended=rv.gr_guess, rememberable=True,
        evidence={"columns": [W("col.received"), W("col.used_as")],
                  "rows": [[u.name, I.KIND_TH.get(k, k)] for u, _t, k in rv.tables] or [[W("used.no_table"), ""]]},
        facts={"gr_files": rv.gr_files, "guess": rv.gr_guess})


def invalid_cards(u, out: dict, dec) -> list:
    """A typed value that could not be used is asked once more (as the invoice agent does)."""
    fs = []
    label = E2C._doc_label(out)
    for name, (key, why) in sorted(dec.invalid.items()):
        if name == "fx_rate":
            what, kind, hint = W("field.fx_rate"), "rate", W("hint.fx")
            opts = [E2C.exclude(), (W("opt.no_rate"), "reupload", W("opt.no_rate.d"))]
        else:
            what = E2C.FIELD_TH.get(name, W("field.value"))
            kind = E2C.INPUT_KIND.get(FIELD_KINDS.get(name, "text"), "text")
            hint = E2C.INPUT_HINT.get(kind, E2C.INPUT_HINT["text"])
            opts = [(W("opt.absent"), "absent", W("opt.absent.d")), E2C.exclude()]
        fs.append(finding("e2_typed_invalid", "blocking", "preflight", W("card.retype.header"),
                          W("card.retype.question", label=label, what=what, why=why), opts, "exclude",
                          subject=f"{u.key}:{name}", input={"kind": kind, "hint": hint}, leaves_out=True,
                          evidence={"document": u.name, "answered_by": key}, facts={"field": name}))
    return fs


# ── links ────────────────────────────────────────────────────────────────

def _po_row(pl: B.PoLine) -> list:
    return [pl.label, E3C.date_th(pl.date.isoformat() if pl.date else None), E3C.safe(pl.description)[:80], B.qty_text(pl.qty),
            E3C.safe(pl.unit), "" if pl.unit_price is None else f"{pl.unit_price:,.2f}",
            "" if pl.amount is None else f"{pl.amount:,.2f}", E3C.safe(pl.source.file if pl.source else ""),
            pl.source.row if pl.source else None]


def _doc_row(doc: str, date, l) -> list:
    return [doc, E3C.date_th(date.isoformat() if date else None), E3C.safe(l.description)[:80], B.qty_text(l.qty), E3C.safe(l.unit),
            "" if getattr(l, "unit_price", None) is None else f"{l.unit_price:,.2f}",
            "" if getattr(l, "amount", None) is None else f"{l.amount:,.2f}", E3C.safe(l.source.file if l.source else ""),
            (l.source.row if l.source else None)]


def _unit(u) -> str:
    """A unit as a card names it: its Thai name when it has one ("pcs" is ชิ้น), else as written, quoted."""
    from . import units as U

    c = U.canonical(u)
    if not c:
        return ""
    return U.said(u) if c in U.UNITS else f"“{E3C.safe(u)}”"


def _line_words(l) -> str:
    bits = [f"“{E3C.safe(l.description)[:60]}”" if l.description else "", W("line.code", item_code=E3C.safe(l.item_code)) if l.item_code else ""]
    if l.qty is not None:
        bits.append(f"{B.qty_text(l.qty)} {_unit(l.unit)}".strip())
    if getattr(l, "unit_price", None) is not None:
        bits.append(W("line.unit_price", unit_price=fmt_money(l.unit_price)))
    return words.clauses(*bits)


def _why(c: dict) -> str:
    pct = int((Decimal(c["similarity"] or "0") * 100).to_integral_value()) if c.get("similarity") else None
    return {
        "po_price": W("why.po_price"),
        "supplier_item": W("why.supplier_item"),
        "po_similar": W("why.po_similar", pct=pct),
        "supplier_similar": W("why.supplier_similar", pct=pct),
    }.get(c["pass"], c.get("pass_label") or "")


def link_card(c: dict, rv, doc_label: str, doc_date, lines: dict, names: dict) -> Finding | None:
    pl = rv.po_by_id(c["left"][0]["id"])
    right = [lines[r["id"]] for r in c["right"] if r["id"] in lines]
    if pl is None or not right:
        return None
    l = right[0]
    if c.get("alternatives"):
        question = W("card.link.question_others", right=names["right"], doc_label=doc_label, line=_line_words(l), left=names["left"],
                     label=pl.label, po_line=_line_words(pl), why=_why(c), n=len(c["alternatives"]))
    else:
        question = W("card.link.question", right=names["right"], doc_label=doc_label, line=_line_words(l), left=names["left"],
                     label=pl.label, po_line=_line_words(pl), why=_why(c))
    return finding(
        E3C.CODE_PAIR, "blocking", "preflight", W("card.link.header"), words.cap(question),
        [(W("opt.same"), MATCH, W("opt.same.d")),
         (W("opt.separate"), SEPARATE, W("opt.separate.d")),
         (W("opt.undecided"), UNDECIDED, W("opt.undecided.d"))],
        UNDECIDED, subject=c["pair"], rememberable=False,
        evidence={"columns": columns(),
                  "rows": [_po_row(pl)] + [_doc_row(words.cap(f"{names['right']} {doc_label}"), doc_date, x) for x in right]},
        facts={"pass": c["pass"], "tier": c["tier"], "similarity": c.get("similarity")})


def duplicate_card(c: dict, inv: B.Invoice) -> Finding:
    e = c["left"][0]
    why = {"same_amount_date": W("dup.same_amount_date"),
           "similar_number": W("dup.similar_number"),
           "same_number_other_total": W("dup.same_number_other_total")}.get(c["pass"], c.get("pass_label") or "")
    if inv.total is None:
        question = W("card.dup.question", label=inv.label, number=E3C.safe(e.get("ref")), date=E3C.date_th(e.get("date")),
                     amount=E3C.money(e["amount"]), why=why)
    else:
        question = W("card.dup.question_total", label=inv.label, total=fmt_money(inv.total), number=E3C.safe(e.get("ref")),
                     date=E3C.date_th(e.get("date")), amount=E3C.money(e["amount"]), why=why)
    return finding(
        E3C.CODE_PAIR, "blocking", "preflight", W("card.dup.header"), question,
        [(W("opt.dup.same"), MATCH, W("opt.dup.same.d")),
         (W("opt.dup.separate"), SEPARATE, W("opt.dup.separate.d")),
         (W("opt.undecided"), UNDECIDED, W("opt.dup.undecided.d"))],
        UNDECIDED, subject=c["pair"], rememberable=False,
        evidence={"columns": [words.cap(W("name.invoice")), W("col.date"), W("col.number"), W("col.total"), W("col.file")],
                  "rows": [[W("dup.row.earlier"), E3C.date_th(e.get("date")), E3C.safe(e.get("ref")), f"{Decimal(e['amount']):,.2f}",
                            E3C.safe(e.get("file"))],
                           [W("dup.row.this"), E3C.date_th(inv.date.isoformat() if inv.date else None), E3C.safe(inv.number),
                            "" if inv.total is None else f"{inv.total:,.2f}", E3C.safe(inv.name)]]},
        facts={"pass": c["pass"], "tier": c["tier"], "similarity": c.get("similarity")})


def supplier_card(inv: B.Invoice, po_key: str, s: dict) -> Finding:
    a, b = inv.supplier, s["po_supplier"]
    return finding(
        "pomatch_supplier", "blocking", "preflight", W("card.supplier.header"),
        W("card.supplier.question", label=inv.label, po=E3C.safe(s["po"]), name=E3C.safe(b["name"]), how_label=s["how_label"]),
        [(W("opt.supplier.same"), P.SAME, W("opt.supplier.same.d")),
         (W("opt.supplier.different"), P.DIFFERENT, W("opt.supplier.different.d")),
         (W("opt.undecided"), P.UNSURE, W("opt.supplier.unsure.d"))],
        P.UNSURE, subject=f"{inv.key}:{po_key}", rememberable=False,
        evidence={"columns": [W("col.document"), words.cap(W("col.supplier_name")), W("col.tax_id")],
                  "rows": [[words.cap(W("name.invoice")), E3C.safe(a.name), a.tax_id or W("word.not_stated")],
                           [words.cap(W("name.po")), E3C.safe(b["name"]), b["tax_id"] or W("word.not_stated")]]},
        facts={"how": s["how"]})


def unit_card(frm: str, pl: B.PoLine, key: str) -> Finding:
    from . import units as U

    a, b = U.said(frm), U.said(pl.unit)
    if pl.description:
        question = W("card.unit.question_item", b=b, a=a, item=E3C.safe(pl.description)[:50])
    else:
        question = W("card.unit.question", b=b, a=a)
    return finding(
        "pomatch_unit", "blocking", "preflight", W("card.unit.header"), question,
        [(W("opt.unit.unknown"), "unknown", W("opt.unit.unknown.d")),
         (W("opt.unit.same"), "same", W("opt.unit.same.d", b=b, a=a))],
        "unknown", subject=key[len("pomatch_unit:"):], rememberable=False,
        input={"kind": "text", "hint": W("hint.unit", a=a, b=b)},
        evidence={"columns": columns(), "rows": [_po_row(pl)]}, facts={"from": U.canonical(frm), "to": U.canonical(pl.unit)})


def findings(rv) -> list:
    from .review import unit_factor, unit_key

    out = [kind_card(u, t) for u, t in rv.waiting_kind]
    if out:
        return out                      # what each table is comes before anything else can be judged
    m = missing_card(rv)
    if m is not None:
        return [m] + [f for d in rv.docs for f in d.findings]
    if not rv.practice_known:
        out.append(practice_card(rv))
    for d in rv.docs:
        out += d.findings
    asked = set()

    def waiting(res):
        return [c for c in res["groups"]["suspect"] if c["kind"] == "candidate" and c["decision"] is None]

    gr_by_id = {g.id: g for g in rv.gr}
    for k in sorted(rv.links_gr):
        for c in waiting(rv.links_gr[k]):
            g = gr_by_id.get(c["right"][0]["id"])
            f = link_card(c, rv, E3C.safe(g.gr) if g else "", g.date if g else None, gr_by_id, L.NAMES_GR)
            if f is not None and f.key not in asked:
                asked.add(f.key)
                out.append(f)
    for inv in rv.invoices:
        for c in waiting(rv.dup.get(inv.key) or {"groups": {"suspect": []}}):
            f = duplicate_card(c, inv)
            if f.key not in asked:
                asked.add(f.key)
                out.append(f)
        lines = {l.id: l for l in inv.lines}
        for c in waiting(rv.links_inv.get(inv.key) or {"groups": {"suspect": []}}):
            f = link_card(c, rv, inv.label, inv.date, lines, L.NAMES_INV)
            if f is not None and f.key not in asked:
                asked.add(f.key)
                out.append(f)
        for po_key, s in sorted((rv.supplier_of.get(inv.key) or {}).items()):
            if s["verdict"] == P.UNSURE and s["how"] != "unknown":
                out.append(supplier_card(inv, po_key, s))
        for l in inv.lines:
            hit = rv.po_of.get(l.id)
            if not hit or not l.unit or not hit[0].unit:
                continue
            factor, how = unit_factor(rv, l.unit, hit[0])
            key = unit_key(l.unit, hit[0])
            if factor is None and how == "unknown" and key not in asked and key not in rv.answers:
                asked.add(key)
                out.append(unit_card(l.unit, hit[0], key))
    return out
