"""The comparison as a workbook: the candidates, what is not known of each, and where every value was seen.

Sheets (named in the package's language): the summary (its face, one page) ·
candidates (one row each: what it is, the figures, how many points are still
open) with two companions read beside it by row — its terms as the page
states them or "unknown", and the evidence of its price (the page, the
string as printed, the block it is in, what is printed around it, the hash
of the snapshot) · sources not used, each with why · what to ask each seller
· every source that was opened.

A value nobody found again on a page is written as "unknown". It is never
left looking like a blank that happens to be empty, and never a guess.

``check`` opens the finished file and reads it back against computed.json:
every candidate's figures, cell by cell; that an unknown is written as one;
and that the workbook prints (page setup, one page across).
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl import load_workbook

from ....deliverables import workpaper
from ....deliverables.look.xlsx_styles import Book
from ....deliverables.reread import check as _check
from ....deliverables.reread import dec
from ..words import t
from . import wording

TOP = 4
ID_COLS = 2

MAIN = ["no", "source", "item", "product", "model", "seller", "kind", "unit_price", "quantity", "subtotal", "currency", "vat",
        "total", "open", "observed"]
TERMS = ["no", "source", "shipping", "lead_time", "minimum_order", "stock", "warranty", "legal_name", "tax_id", "on_list",
         "requirements"]
EVID = ["no", "source", "address", "price_shown", "price_block", "price_found", "beside", "snapshot"]


# AM13: how the table of options is ordered when a person asked for the price — by which price, said on the face
PRICE_ORDER = {"total": "face.order.price_total", "shown": "face.order.price_shown"}


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def _quote(ev) -> str:
    return (ev or {}).get("quote") or ""


class ComparisonWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        self.lang = computed.get("lang") or "th"
        self.book = Book(theme, self.lang)
        self.wb = self.book.wb

    def w(self, key: str, **kw) -> str:
        return t(self.lang, key, **kw)

    def said(self, ev) -> str:
        return _quote(ev) or self.w("unknown")

    # ── styling ────────────────────────────────────────────────────────────
    def heads(self, keys: list[str]) -> list[str]:
        return [self.w(f"col.{k}") for k in keys]

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        c = self.c
        ws = self.book.sheet(self.w("sheet.summary"))
        asked = " · ".join(wording.item_line(self.lang, it) for it in c["items"]) or self.w("unknown")
        self.book.title(ws, self.w("doc.comparison.title"),
                    " · ".join(x for x in (self.run.get("organisation"), self.w("doc.prepared", date=wording.date(self.lang, c.get("today")))) if x))
        offers = sum(len(it["candidates"]) for it in c["items"])
        dropped = sum(len(it["dropped"]) for it in c["items"])
        rnd = c.get("round") or {}
        rows = [[self.w("face.asked"), asked],
                [self.w("face.candidates"), offers],
                [self.w("face.unused"), dropped],
                [self.w("face.opened"), len(c.get("sources") or [])],
                [self.w("face.searches"), wording.used(self.lang, rnd, "searches")],
                [self.w("face.pages"), wording.used(self.lang, rnd, "pages")],
                [self.w("face.order"), self.w(self.order_key())]]
        for it in c["items"]:
            pick = (c.get("picks") or {}).get(it["key"])
            if pick:
                rows.append([self.w("face.chosen", item=it["name"]), wording.pick_line(self.lang, it, pick)])
        self.book.table(ws, TOP, [self.w("col.what"), self.w("col.value")], rows, freeze=False)
        r = TOP + len(rows) + 2
        for line in [self.w("face.not_advice")] + wording.round_lines(self.lang, rnd) + [a["text"] for a in c.get("assumptions") or []]:
            self.book.note(ws, r, line, cols=2)
            r += 1

    def order_key(self) -> str:
        """How the table of options is ordered, in the package's words: as computed (the fewest unknown
        points first), or by price when a person asked for that (AM13) — by which price, said."""
        order = self.c.get("shown_order") or {}
        if order.get("by") == "price" and order.get("basis") in PRICE_ORDER:
            return PRICE_ORDER[order["basis"]]
        return "face.order.value"

    def _rows(self):
        """One row per candidate, per table (main, terms, evidence), in the order the candidates are shown."""
        main, terms, evid = [], [], []
        for it in self.c["items"]:
            for c in it["candidates"]:
                ident = [f"{it['key'][1:]}.{c['no']}" if len(self.c["items"]) > 1 else str(c["no"]), c["host"]]
                price = c.get("price") or {}
                priced = bool(price) and bool(c.get("currency"))
                unknown = self.w("unknown")
                main.append(ident + [
                    it["name"], _quote(c["name"]), self.said(c.get("model")), self.said(c.get("seller")), self.w(f"kind.{c['kind']}"),
                    _num(price.get("amount")) if priced else (price.get("display") or unknown),
                    it.get("quantity") or unknown,
                    _num(c.get("subtotal")) if c.get("subtotal") else unknown,
                    c.get("currency") or unknown,
                    self.w(f"vat.{(c.get('vat') or {}).get('says') or 'unknown'}"),
                    _num(c.get("total")) if c.get("total") else unknown,
                    len(c["open"]), wording.moment(self.lang, c.get("observed_at"))])
                terms.append(ident + [self.said(c.get(f)) for f in ("shipping", "lead_time", "minimum_order", "stock", "warranty",
                                                                    "legal_name", "tax_id")]
                             + [wording.on_list(self.lang, c.get("on_list")), wording.requirements(self.lang, c.get("requirements") or [])])
                evid.append(ident + [c["url"], price.get("display") or unknown,
                                     price.get("block") if price else unknown,
                                     self.w(f"found.{price.get('how')}") if price else unknown,
                                     " | ".join(price.get("beside") or c["name"].get("beside") or []),
                                     wording.snapshot_of(self.c, c["source"])])
        return main, terms, evid

    def candidates(self):
        main, terms, evid = self._rows()
        name = self.w("sheet.candidates")
        ws = self.book.sheet(name)
        self.book.title(ws, self.w("doc.candidates.title"), self.w("doc.candidates.sub"))
        self.book.table(ws, TOP, self.heads(MAIN), main, money_cols=(8, 10, 13))
        for what, keys, rows in ((self.w("sheet.terms"), TERMS, terms), (self.w("sheet.evidence"), EVID, evid)):
            w2 = self.book.sheet(f"{name}{workpaper.COMPANION}{what}")
            self.book.title(w2, f"{self.w('doc.candidates.title')}{workpaper.COMPANION}{what}", self.w("doc.companion.sub", sheet=name))
            self.book.table(w2, TOP, self.heads(keys), rows)

    def unused(self):
        ws = self.book.sheet(self.w("sheet.unused"))
        self.book.title(ws, self.w("doc.unused.title"), self.w("doc.unused.sub"))
        rows = []
        for it in self.c["items"]:
            for d in it["dropped"]:
                rows.append([len(rows) + 1, d.get("host") or "", it["name"], wording.dropped(self.lang, d), d.get("url") or ""])
        self.book.table(ws, TOP, self.heads(["no", "source", "item", "why", "address"]), rows)

    def ask(self):
        ws = self.book.sheet(self.w("sheet.ask"))
        self.book.title(ws, self.w("doc.ask.title"), self.w("doc.ask.sub"))
        rows = []
        for it in self.c["items"]:
            for c in it["candidates"]:
                for point in c["open"]:
                    rows.append([len(rows) + 1, c["host"], _quote(c["name"]), self.w(f"open.{point}"), wording.open_why(self.lang, c, point)])
                for p in c.get("not_found") or []:
                    rows.append([len(rows) + 1, c["host"], _quote(c["name"]), self.w(f"field.{p['field']}"), self.w(f"notfound.{p['why']}", digits=p.get("digits") or "")])
        self.book.table(ws, TOP, self.heads(["no", "source", "product", "point", "why"]), rows)

    def sources(self):
        ws = self.book.sheet(self.w("sheet.sources"))
        self.book.title(ws, self.w("doc.sources.title"), self.w("doc.sources.sub"))
        rows = []
        for s in self.c.get("sources") or []:
            rows.append([len(rows) + 1, s.get("host") or "", self.w(f"outcome.{s.get('outcome') or 'failed'}"),
                         wording.reason(self.lang, s), wording.moment(self.lang, s.get("fetched_at")), s.get("rule") or "",
                         s.get("snapshot_sha256") or "", s.get("url") or ""])
        self.book.table(ws, TOP, self.heads(["no", "source", "outcome", "why", "observed", "site_rule", "snapshot", "address"]), rows,
                    [5, 24, 14, 44, 16, 22, 26, 44])

    def write(self, path: str) -> dict:
        self.summary()
        self.candidates()
        self.unused()
        self.ask()
        self.sources()
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        main = self.w("sheet.candidates")
        ids = {s.title: ID_COLS for s in self.wb.worksheets if s.title.split(workpaper.COMPANION)[0] == main}
        plan = self.book.finish(path, id_cols=ids, author=str(self.run.get("organisation") or ""))
        return {"sheets": [s.title for s in self.wb.worksheets], "print": plan}


def check(path: str, computed: dict, run: dict | None = None) -> list[dict]:
    """The finished workbook, opened again and read back against computed.json."""
    lang = computed.get("lang") or "th"
    w = lambda key, **kw: t(lang, key, **kw)  # noqa: E731
    wb = load_workbook(path)
    name = w("sheet.candidates")
    heads = [w(f"col.{k}") for k in MAIN]
    bad: list[str] = []
    n = 0
    if name not in wb.sheetnames:
        return [_check("xlsx.figures", False, w("check.figures.no_sheet", sheet=name), w("check.figures.label"), 0)]
    ws = wb[name]
    got_heads = [c.value for c in ws[TOP]][:len(heads)]
    if got_heads != heads:
        bad.append(w("check.figures.heads"))
    want = [(it, c) for it in computed["items"] for c in it["candidates"]]
    rows = [r for r in ws.iter_rows(min_row=TOP + 1, values_only=True) if r and r[0] not in (None, "")]
    if len(rows) != len(want):
        bad.append(w("check.figures.rows", file=str(len(rows)), computed=str(len(want))))
    col = {k: i for i, k in enumerate(MAIN)}
    unknown = w("unknown")
    for (it, c), row in zip(want, rows):
        where = w("check.figures.row", no=str(row[col["no"]]))
        price = c.get("price") or {}
        priced = bool(price) and bool(c.get("currency"))
        for key, value in (("unit_price", price.get("amount") if priced else None), ("subtotal", c.get("subtotal")), ("total", c.get("total"))):
            n += 1
            cell = row[col[key]]
            if value is None:
                # what is not known must read as not known — never a number, never an empty cell
                if isinstance(cell, (int, float)) or cell in (None, ""):
                    bad.append(w("check.figures.unknown", where=where, column=w(f"col.{key}")))
            elif not isinstance(cell, (int, float)) or dec(cell) != dec(value):
                bad.append(w("check.figures.differs", where=where, column=w(f"col.{key}"), file=str(cell if cell not in (None, "") else unknown),
                             computed=f"{Decimal(str(value)):,.2f}"))
        n += 1
        if (row[col["product"]] or "") != _quote(c["name"]):
            bad.append(w("check.figures.differs", where=where, column=w("col.product"), file=str(row[col["product"]]), computed=_quote(c["name"])))
        n += 1
        if row[col["vat"]] != w(f"vat.{(c.get('vat') or {}).get('says') or 'unknown'}"):
            bad.append(w("check.figures.differs", where=where, column=w("col.vat"), file=str(row[col["vat"]]),
                         computed=w(f"vat.{(c.get('vat') or {}).get('says') or 'unknown'}")))
    # the sources that did not become a candidate are all on their sheet, each with a reason
    dropped = sum(len(it["dropped"]) for it in computed["items"])
    listed = [r for r in wb[w("sheet.unused")].iter_rows(min_row=TOP + 1, values_only=True) if r and r[0] not in (None, "")] \
        if w("sheet.unused") in wb.sheetnames else []
    n += 1
    if len(listed) != dropped or any(not (r[3] or "").strip() for r in listed):
        bad.append(w("check.figures.unused", file=str(len(listed)), computed=str(dropped)))
    more = " " + w("check.more", n=str(len(bad) - 4)) if len(bad) > 4 else ""
    figures = _check("xlsx.figures", not bad, ("; ".join(bad[:4]) + more) if bad else w("check.figures.ok", n=str(n)),
                     w("check.figures.label"), n)
    return [figures] + workpaper.checks(path, lang=lang)
