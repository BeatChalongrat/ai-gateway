"""The purchase request a choice makes — and everything in it that is still not known.

A purchase request is drafted from what the person chose, the quantity they
gave and what was found again on the seller's page. It is a draft: a person
completes it and submits it outside this agent.

Two things are true of every draft made here:

* every amount is arithmetic done in this file on figures that were found
  again in a stored snapshot. Tax is worked out only where the page itself
  says whether its price includes it; the rate is the install's setting;
* what a page cannot tell — and a page cannot tell most of what a purchase
  request needs — is not filled in. It is on the ``gaps`` list, each with
  why it is unknown, and that list is printed on the face of the draft. A
  price on a page is not a quotation: how long it holds, whether the seller
  will deliver by a date, whether the seller is one the organisation buys
  from, are never known from a page.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.e4 import money


def _quote(ev) -> str:
    return (ev or {}).get("quote") or ""


def describe(c: dict) -> str:
    """A candidate in a line: what the page calls it, with its model and variant where the page prints them."""
    parts = [_quote(c.get("name"))]
    for f in ("model", "variant"):
        q = _quote(c.get(f))
        if q and q not in parts[0]:
            parts.append(q)
    return " · ".join(p for p in parts if p)


def seller_shown(c: dict) -> str:
    return _quote(c.get("legal_name")) or _quote(c.get("seller")) or c.get("host") or ""


def seller_legal_name(c: dict) -> str:
    """The seller's name for the request: the organisation's own record of it when the seller is on its list,
    else what the page prints (and the draft says that is all it is)."""
    listed = c.get("on_list") or {}
    return listed.get("name") or _quote(c.get("legal_name"))


def seller_tax_id(c: dict) -> str:
    listed = c.get("on_list") or {}
    return listed.get("tax_id") or (c.get("tax_id") or {}).get("number") or ""


def _gaps(c: dict, line: int, need: dict) -> list[dict]:
    """What is not known about one chosen candidate, each with why (a code the words are found by)."""
    out = []

    def gap(code: str, why: str, seen: str = "") -> None:
        out.append({"code": code, "why": why, "seen": seen, "line": line})

    if not c.get("price"):
        whys = [p["why"] for p in c.get("not_found") or [] if p["field"] == "price"]
        gap("price", whys[0] if whys else "not_stated")
    if not c.get("vat"):
        gap("vat", "not_stated")
    if c.get("shipping"):
        gap("shipping", "not_to_address", _quote(c["shipping"]))
    else:
        gap("shipping", "not_stated")
    if c.get("lead_time"):
        gap("lead_time", "not_committed", _quote(c["lead_time"]))
    else:
        gap("lead_time", "not_stated")
    if c.get("minimum_order"):
        gap("minimum_order", "check_quantity", _quote(c["minimum_order"]))
    else:
        gap("minimum_order", "not_stated")
    listed = c.get("on_list")
    if not listed:
        # a name a page prints is the page's word for it: not the name on the company register
        if c.get("legal_name"):
            gap("legal_name", "as_shown", _quote(c["legal_name"]))
        else:
            gap("legal_name", "shop_name_only" if c.get("seller") else "not_stated", _quote(c.get("seller")))
    if not (listed or {}).get("tax_id"):
        if c.get("tax_id"):
            gap("tax_id", "as_shown", c["tax_id"].get("number") or "")
        else:
            gap("tax_id", "not_stated")
    if not listed:
        gap("supplier_listed", "not_on_list")
    if not c.get("stock"):
        gap("stock", "not_stated")
    else:
        gap("stock", "not_reserved", _quote(c["stock"]))
    gap("price_validity", "never_on_a_page")
    gap("payment_terms", "never_on_a_page")
    differs = [r["need"] for r in c.get("requirements") or [] if r["verdict"] == "differs"]
    unstated = [r["need"] for r in c.get("requirements") or [] if r["verdict"] == "not_stated"]
    if differs:
        gap("requirements_differ", "page_differs", " · ".join(differs))
    if unstated:
        gap("requirements_not_stated", "page_silent", " · ".join(unstated))
    return out


def build(need: dict, items: list[dict], picks: dict, *, vat_rate: str) -> dict | None:
    """The request made of the candidates picked ({item key: candidate key | "none"}); None while nothing is picked."""
    lines, gaps, passed = [], [], []
    for it in items:
        pick = picks.get(it["key"])
        if pick in (None, "none"):
            if pick == "none":
                passed.append(it["name"])
            continue
        c = next((x for x in it["candidates"] if x["key"] == pick), None)
        if c is None:
            continue
        n = len(lines) + 1
        price = c.get("price")
        lines.append({"no": n, "item": it["key"], "candidate": c["key"], "description": describe(c), "asked_for": it["name"],
                      "quantity": it.get("quantity") or "", "quantity_assumed": bool(it.get("quantity_assumed")),
                      "unit": it.get("unit") or "",
                      "unit_price": money.text(Decimal(price["amount"])) if price else None, "price_shown": _quote(c.get("price")),
                      "amount": c.get("subtotal"), "currency": c.get("currency"), "vat_says": (c.get("vat") or {}).get("says"),
                      "net": c.get("net"), "vat": c.get("vat_part"),
                      "seller": seller_shown(c), "host": c.get("host"), "url": c.get("url"), "observed_at": c.get("observed_at"),
                      "on_list": c.get("on_list"), "tax_id": seller_tax_id(c), "legal_name": seller_legal_name(c)})
        gaps.extend(_gaps(c, n, need))
    if not lines:
        return {"lines": [], "gaps": [], "passed": passed, "totals": None} if passed else None
    if not need.get("delivery_place") and need.get("delivery_place_from") != "pickup":
        gaps.append({"code": "delivery_place", "why": "not_given", "seen": "", "line": 0})
    if not need.get("needed_by"):
        gaps.append({"code": "needed_by", "why": "not_given", "seen": "", "line": 0})
    currencies = {l["currency"] for l in lines if l["currency"]}
    priced = all(l["amount"] for l in lines) and len(currencies) == 1
    totals = None
    if priced:
        sub = sum((Decimal(l["amount"]) for l in lines), Decimal(0))
        totals = {"currency": currencies.pop(), "subtotal": money.text(sub), "net": None, "vat": None, "total": None,
                  "vat_rate": str(vat_rate)}
        if all(l["vat_says"] in ("included", "excluded") and l["net"] and l["vat"] is not None for l in lines):
            net = sum((Decimal(l["net"]) for l in lines), Decimal(0))
            tax = sum((Decimal(l["vat"]) for l in lines), Decimal(0))
            totals.update(net=money.text(net), vat=money.text(tax), total=money.text(net + tax))
    sellers = {l["seller"] for l in lines}
    return {"lines": lines, "gaps": gaps, "passed": passed, "totals": totals, "one_seller": len(sellers) == 1,
            "seller": lines[0]["seller"] if len(sellers) == 1 else ""}
