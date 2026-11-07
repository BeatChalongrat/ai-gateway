"""From what a model read in the captured pages to a list of candidates a person can choose from.

A reading (prompts/read.md) is a set of claims: for each thing it says a page
shows, the words as printed and the block they are printed in. Nothing of a
reading is taken on its word. Every claim is looked for in the stored
snapshot of the page (engine.e4.refind); what is found again is kept with
where it was found, and what is not found is not in the table — it is listed
as something to ask the seller. That goes for a price, and equally for a
model number, a warranty, a seller's name.

What a reading cannot do, by the shape of what is kept from it:

* it has no field that says a supplier is approved. Whether a seller is on
  the organisation's own list is worked out here, from that list;
* tax treatment, delivery, lead time, minimum order, stock, warranty and the
  seller's identity are each either a quote found again on the page or
  unknown. There is no third state a reading can set;
* a figure is a number only when the amount it gives is one of the
  mechanical readings of the string it quoted (engine.e4.money); that string
  is on the page where it said, whole (not part of a longer figure) and not
  struck through; it is shown as a figure (little else in its block, no
  second figure); and it is printed with the product it was read for — in
  the same part of the page as the product's name, under no other heading,
  with no other figure shown as a price nearer to that name. None of this
  reads the words around a figure: it is where the figure sits in the page.

Every amount below — a subtotal, tax, a total — is arithmetic on such figures
and the quantity the person gave, done here with Decimal. The tax rate is the
install's setting for its jurisdiction, never something read off a page.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ....engine.e4 import money, refind
from ....engine.e4.snapshot import Snapshot, crop

MAX_OFFERS_PER_PAGE = 3
# what a page may say of an offer beyond what it is and what it costs: each a quote, or unknown
SAID = ("model", "variant", "seller", "price_basis", "shipping", "lead_time", "minimum_order", "stock", "warranty",
        "tax_id", "legal_name")
KINDS = ("own_site", "marketplace")
VERDICTS = ("meets", "differs")
TAX_ID_DIGITS = {"TH": 13}   # how many digits a tax ID has, by the install's jurisdiction
MAX_AROUND_A_FIGURE = 40   # characters a block may hold besides the figure, for the figure to be a price shown as one


def _claim(snap: Snapshot, c, problems: list, field: str) -> dict | None:
    """A claim found again in the snapshot: {quote, block, how, beside, box}; None (and a note of why) when not."""
    if c is None:
        return None
    if not isinstance(c, dict) or not isinstance(c.get("quote"), str) or not c["quote"].strip():
        problems.append({"field": field, "why": "no_quote"})
        return None
    found = refind.locate(snap.blocks, c["quote"], c.get("block"))
    if found is None:
        problems.append({"field": field, "why": "not_on_page"})
        return None
    b = snap.block(found.block) or {}
    return {"quote": " ".join(c["quote"].split()), "block": found.block, "how": found.how, "relocated": found.relocated,
            "beside": refind.around(snap.blocks, found.block), "box": b.get("box"), "struck": bool(b.get("struck"))}


def _digits_in(figure: str) -> int:
    return sum(ch.isdigit() for ch in figure)


def _shown_as_a_figure(text: str, quote: str | None = None) -> str | None:
    """Why a block's text is not a price shown as one — or None when it is: little besides the figure, and no
    second figure. ``quote`` is the figure looked at; without it the block must hold exactly one."""
    flat = refind.spacing(text)
    if quote is None:
        figs = money.figures(flat)
        if not figs:
            return "no_figure"
        quote = max(figs, key=_digits_in)
    rest = flat.replace(refind.spacing(quote), "", 1)
    if len(rest) > MAX_AROUND_A_FIGURE:
        return "inside_text"
    if any(_digits_in(f) >= 2 for f in money.figures(rest)):
        return "several_figures"
    return None


def _a_heading(path: str) -> bool:
    last = path.rsplit(">", 1)[-1].split(":")[0]
    return len(last) == 2 and last[0] == "h" and last[1] in "123456"


def _price(snap: Snapshot, c, name: dict, problems: list) -> dict | None:
    ev = _claim(snap, c, problems, "price")
    if ev is None:
        return None
    if ev["struck"]:
        problems.append({"field": "price", "why": "struck_through"})
        return None
    block = snap.block(ev["block"]) or {}
    block_text = block.get("text", "")
    # a price is shown as a figure: what it is printed in holds little besides it, and no second figure. A
    # number inside a sentence, and one of several in a line, are not taken as the price — they are asked about.
    why = _shown_as_a_figure(block_text, c["quote"])
    if why:
        problems.append({"field": "price", "why": why})
        return None
    # and it is printed with the product it was read for (a page lists several products and several prices):
    name_block = snap.block(name["block"]) or {}
    here, there = block.get("path") or "", name_block.get("path") or ""
    if here and there:
        # … in the same part of the page as the product's name, not in a strip across the top of the page
        if refind.depth(there) > 3 and refind.shared(there, here) <= 2:
            problems.append({"field": "price", "why": "apart_from_product"})
            return None
        # … under no other heading than the product's own
        lo, hi = sorted((name_block["i"], block["i"]))
        if any(_a_heading(b.get("path") or "") for b in snap.blocks if lo < b["i"] < hi):
            problems.append({"field": "price", "why": "under_another_heading"})
            return None
        # … and no other figure the page shows as a price sits nearer to the product's name than this one
        near = refind.distance(here, there)
        for b in snap.blocks:
            if b["i"] in (block["i"], name_block["i"]) or b.get("struck") or not b.get("path"):
                continue
            if refind.distance(b["path"], there) < near and money.mark(b["text"]) and not _shown_as_a_figure(b["text"]):
                problems.append({"field": "price", "why": "nearer_another"})
                return None
    # of every place the page prints that same figure, this is the one nearest the product
    if not refind.belongs_with(snap.blocks, c["quote"], ev["block"], name["block"]):
        problems.append({"field": "price", "why": "nearer_another"})
        return None
    # a price is printed with its currency — in the figure's own line. A currency printed somewhere near is
    # not taken as this figure's: a number with no currency of its own is a number, not a price
    currency = money.mark(c["quote"]) or money.mark(block_text)
    if not currency:
        problems.append({"field": "price", "why": "no_mark"})
        return None
    amount = money.agrees(c["quote"], c.get("amount"), currency)
    if amount is None:
        problems.append({"field": "price", "why": "not_that_number"})
        return None
    if amount <= 0:
        problems.append({"field": "price", "why": "not_that_number"})
        return None
    said = str(c.get("currency") or "").upper().strip()
    if said and said != currency:
        problems.append({"field": "price", "why": "other_currency"})
        return None
    ev.update({"display": ev["quote"], "amount": str(amount), "currency": currency})
    return ev


def _digits(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _same_name(a, b) -> bool:
    x, y = refind.forms(str(a or "")).lower(), refind.forms(str(b or "")).lower()
    return bool(x) and x == y


def on_list(candidate: dict, suppliers: list[dict]) -> dict | None:
    """The entry of the organisation's own supplier list this seller is, if any: by the site, by a tax
    number printed on the page, or by a name that is the same name. The list is the organisation's; a
    page cannot add to it and a reading cannot say a seller is on it."""
    host = (candidate.get("host") or "").lower()
    for s in suppliers or []:
        site = str(s.get("website") or "").lower().strip()
        site = site.split("//")[-1].split("/")[0]
        if site and host and (host == site or host.endswith("." + site) or site.endswith("." + host)):
            return {"name": s.get("name") or "", "by": "site", "tax_id": _digits(s.get("tax_id"))}
        tid = _digits(s.get("tax_id"))
        if tid and tid == (candidate.get("tax_id") or {}).get("number"):
            return {"name": s.get("name") or "", "by": "tax_id", "tax_id": tid}
        for f in ("legal_name", "seller"):
            if _same_name(s.get("name"), (candidate.get(f) or {}).get("quote")):
                return {"name": s.get("name") or "", "by": "name", "tax_id": _digits(s.get("tax_id"))}
    return None


def _amounts(price: dict | None, quantity: str | None, vat: dict | None, rate: Decimal) -> dict:
    out = {"subtotal": None, "net": None, "vat_part": None, "total": None}
    if not price or not quantity:
        return out
    try:
        qty = Decimal(quantity)
    except InvalidOperation:
        return out
    sub = money.cents(Decimal(price["amount"]) * qty)
    out["subtotal"] = money.text(sub)
    status = (vat or {}).get("says")
    if status == "included":
        net = money.cents(sub / (1 + rate))
        out.update(net=money.text(net), vat_part=money.text(sub - net), total=money.text(sub))
    elif status == "excluded":
        tax = money.cents(sub * rate)
        out.update(net=money.text(sub), vat_part=money.text(tax), total=money.text(sub + tax))
    return out


def _tax_id(ev: dict | None, digits: int | None, problems: list) -> dict | None:
    """A tax ID is a number of a known length: a sentence about one is not one."""
    if ev is None:
        return None
    number = _digits(refind.forms(ev["quote"]))
    if (digits and len(number) != digits) or not (8 <= len(number) <= 15):
        problems.append({"field": "tax_id", "why": "not_that_shape", "digits": digits or 0})
        return None
    ev["number"] = number
    return ev


def _offer(snap: Snapshot, source: dict, item: dict, raw: dict, n: int, rate: Decimal, suppliers: list, out_dir: str | None,
           tax_digits: int | None = None) -> dict | None:
    problems: list[dict] = []
    name = _claim(snap, raw.get("name"), problems, "name")
    if name is None:
        return None
    cand = {"key": f"{source['id']}.{n}", "source": source["id"], "host": snap.host, "url": snap.url,
            "observed_at": snap.capture.get("finished_at") or "", "name": name,
            "kind": raw.get("kind") if raw.get("kind") in KINDS else "unknown"}
    for f in SAID:
        cand[f] = _claim(snap, raw.get(f), problems, f)
    cand["tax_id"] = _tax_id(cand["tax_id"], tax_digits, problems)
    cand["price"] = _price(snap, raw.get("price"), name, problems)
    vat = raw.get("vat")
    cand["vat"] = None
    if isinstance(vat, dict) and vat.get("says") in ("included", "excluded"):
        ev = _claim(snap, vat, problems, "vat")
        if ev:
            ev["says"] = vat["says"]
            cand["vat"] = ev
    # the requirements the person named, each: met or not by something printed on the page, or not stated
    rows = []
    claimed = {str(r.get("need")): r for r in raw.get("requirements") or [] if isinstance(r, dict)}
    for need in item.get("requirements") or []:
        r = claimed.get(need)
        row = {"need": need, "verdict": "not_stated", "said": None}
        if r and r.get("verdict") in VERDICTS:
            ev = _claim(snap, r, problems, "requirement")
            if ev:
                row.update(verdict=r["verdict"], said=ev)
        rows.append(row)
    cand["requirements"] = rows
    cand["on_list"] = on_list(cand, suppliers)
    cand.update(_amounts(cand["price"], item.get("quantity"), cand["vat"], rate))
    cand["currency"] = (cand["price"] or {}).get("currency")
    # what is still to be settled before this could be bought: counted, never weighed
    open_points = []
    if not cand["price"]:
        open_points.append("price")
    if not cand["vat"]:
        open_points.append("vat")
    for f in ("shipping", "lead_time"):
        if not cand[f]:
            open_points.append(f)
    if not cand["on_list"] and not (cand["tax_id"] and cand["legal_name"]):
        open_points.append("seller_identity")
    if cand["minimum_order"]:
        open_points.append("minimum_order")
    differs = [r["need"] for r in rows if r["verdict"] == "differs"]
    unstated = [r["need"] for r in rows if r["verdict"] == "not_stated"]
    if differs:
        open_points.append("requirements_differ")
    if unstated:
        open_points.append("requirements_not_stated")
    cand["open"] = open_points
    cand["not_found"] = problems
    if out_dir:
        boxes = [e["box"] for e in (cand["price"], name) if e and e.get("box")]
        if cand["price"] and cand["price"].get("box"):
            # the figure with the product it belongs to, when they sit close enough for one picture
            near = [b for b in boxes if abs(b[1] - cand["price"]["box"][1]) < 380]
            cand["picture"] = crop(snap, near or [cand["price"]["box"]], out_dir)
        elif name.get("box"):
            cand["picture"] = crop(snap, [name["box"]], out_dir)
    return cand


def _order(c: dict):
    total = c.get("total") or c.get("subtotal")
    return (len(c["open"]), 0 if total else 1, Decimal(total) if total else Decimal(0), c["key"])


def build(need: dict, research: dict, snaps: dict[str, Snapshot], *, vat_rate: str, suppliers: list | None = None,
          out_dir: str | None = None, jurisdiction: str = "TH") -> list[dict]:
    """Per item of the need: its candidates (fewest open points first, then the lowest total that could be
    worked out) and every source that did not become one, with why."""
    try:
        rate = Decimal(str(vat_rate)) / 100
    except InvalidOperation:
        rate = Decimal("0.07")
    out = []
    for item in need.get("items") or []:
        cands, dropped = [], []
        for src in research.get("sources") or []:
            if src.get("item") != item["key"]:
                continue
            gone = {"source": src.get("id"), "url": src.get("url") or "", "host": src.get("host") or "", "detail": src.get("detail") or {}}
            if src.get("outcome") != "captured":
                dropped.append({**gone, "why": src.get("reason") or src.get("outcome") or "not_fetched"})
                continue
            snap = snaps.get(str(src.get("id")))
            reading = src.get("reading")
            if snap is None or not snap.blocks:
                dropped.append({**gone, "why": "snapshot_missing"})
                continue
            if not isinstance(reading, dict):
                # no model read the page — and when it was the organisation's own guardrail that would not
                # let the page's text go to a model, that is the reason, said as that
                if src.get("read_refused"):
                    dropped.append({**gone, "why": "read_refused"})
                else:
                    dropped.append({**gone, "why": "not_read"})
                continue
            no = _claim(snap, reading.get("says_no"), [], "says_no")
            if no:
                dropped.append({**gone, "why": "page_states_no", "detail": {"quote": no["quote"]}})
                continue
            made = []
            offers = reading.get("offers") if isinstance(reading.get("offers"), list) else []
            for n, raw in enumerate(offers[:MAX_OFFERS_PER_PAGE], start=1):
                if isinstance(raw, dict):
                    c = _offer(snap, src, item, raw, n, rate, suppliers or [], out_dir, TAX_ID_DIGITS.get(jurisdiction))
                    if c:
                        made.append(c)
            if not made:
                dropped.append({**gone, "why": "cannot_describe"})
            cands.extend(made)
        cands.sort(key=_order)
        for i, c in enumerate(cands, start=1):
            c["no"] = i
        out.append({"key": item["key"], "name": item["name"], "meant": item.get("meant") or "", "quantity": item.get("quantity"),
                    "quantity_assumed": bool(item.get("quantity_assumed")), "unit": item.get("unit") or "",
                    "requirements": item.get("requirements") or [], "candidates": cands, "dropped": dropped})
    return out
