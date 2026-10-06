"""What the person asked for, settled into something a search can work from.

A model has read the person's words (the brief: prompts/brief.md) and said
what it understood. It proposes; this code disposes:

* the brief is taken apart field by field and anything that is not the shape
  asked for is dropped, so a brief can add nothing but what it is: the items,
  how many, the requirements the person named, where and by when;
* what the brief says is unclear becomes a question only when it is one of
  the things a search cannot start without — which of several things is
  meant, and how many. Where it is delivered is asked once and kept in the
  install's settings; a date nobody gave is not asked for, it is a gap on
  the face of the draft;
* a quantity is a number the person gave. One the brief did not find in the
  person's words is asked for, never assumed.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.e4 import money
from ....engine.findings import finding
from ..words import t

MAX_ITEMS = 3
MAX_SPECS = 12
MAX_TEXT = 200


def _text(v, limit: int = MAX_TEXT) -> str:
    return " ".join(str(v).split())[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _quantity(v) -> str | None:
    """A quantity as the person wrote it, as a number; None when it is not one."""
    if isinstance(v, bool) or v is None:
        return None
    got = money.readings(str(v))
    if not got:
        return None
    # "1,200" of something counted is one thousand two hundred: a quantity has no thousandth
    whole = [d for d in got if d == d.to_integral_value()]
    d = (whole or got)[0]
    if d <= 0 or d > Decimal("100000000"):
        return None
    return str(d.normalize()) if d != d.to_integral_value() else str(int(d))


def _items(brief: dict) -> list[dict]:
    out = []
    for raw in (brief.get("items") or [])[:MAX_ITEMS] if isinstance(brief.get("items"), list) else []:
        if not isinstance(raw, dict) or not _text(raw.get("name")):
            continue
        specs = []
        for s in raw.get("requirements") or []:
            s = _text(s)
            if s and s not in specs and len(specs) < MAX_SPECS:
                specs.append(s)
        choices = []
        for c in raw.get("could_mean") or []:
            c = _text(c, 60)
            if c and c not in choices and len(choices) < 4:
                choices.append(c)
        out.append({"key": f"i{len(out) + 1}", "name": _text(raw.get("name")), "quantity": _quantity(raw.get("quantity")),
                    "unit": _text(raw.get("unit"), 30), "requirements": specs, "could_mean": choices if len(choices) >= 2 else []})
    return out


def settle(brief: dict | None, *, defaults: dict | None = None, answers: dict | None = None, lang: str = "th") -> dict:
    """{"need", "findings", "assumptions", "ready"}: the need as it stands, what must be asked before a
    search can start, and what was taken for granted."""
    brief = brief if isinstance(brief, dict) else {}
    defaults, answers = defaults or {}, answers or {}
    items = _items(brief)
    findings, assumptions = [], []
    more = isinstance(brief.get("items"), list) and len(brief["items"]) > MAX_ITEMS
    if more:
        assumptions.append({"code": "proc_items_limit", "source": "rule",
                            "text": t(lang, "need.assume.limit", n=str(MAX_ITEMS), all=str(len(brief["items"])))})
    for it in items:
        subject = it["key"]
        # which of several things is meant
        if it["could_mean"]:
            picked = answers.get(f"proc_variant:{subject}")
            if isinstance(picked, str) and picked in it["could_mean"]:
                it["meant"] = picked
            elif isinstance(picked, dict) and _text(picked.get("typed")):
                it["meant"] = _text(picked["typed"])
            elif picked == "any":
                assumptions.append({"code": "proc_variant", "source": "answer",
                                    "text": t(lang, "need.assume.any", item=it["name"])})
            else:
                options = [(c, c, "") for c in it["could_mean"][:3]] + [(t(lang, "need.variant.any"), "any", t(lang, "need.variant.any.note"))]
                findings.append(finding("proc_variant", "blocking", "preflight", t(lang, "need.variant.header"),
                                        t(lang, "need.variant.ask", item=it["name"]), options, "any", subject=subject,
                                        input={"kind": "text", "hint": t(lang, "need.variant.hint")}))
        # how many
        if it["quantity"] is None:
            given = answers.get(f"proc_quantity:{subject}")
            typed = _quantity(given.get("typed")) if isinstance(given, dict) else None
            if typed:
                it["quantity"] = typed
            elif given == "skip":
                it["skipped"] = True
                assumptions.append({"code": "proc_quantity", "source": "answer",
                                    "text": t(lang, "need.assume.skipped", item=it["name"])})
            elif given is not None:
                # a quantity that could not be read as a number is not guessed at: one unit, said plainly
                it["quantity"], it["quantity_assumed"] = "1", True
                assumptions.append({"code": "proc_quantity", "source": "answer", "changes_numbers": True,
                                    "text": t(lang, "need.assume.one", item=it["name"])})
            else:
                findings.append(finding("proc_quantity", "blocking", "preflight", t(lang, "need.quantity.header"),
                                        t(lang, "need.quantity.ask", item=it["name"]),
                                        [(t(lang, "need.quantity.one"), "one", t(lang, "need.quantity.one.note")),
                                         (t(lang, "need.quantity.skip"), "skip", t(lang, "need.quantity.skip.note"))],
                                        "one", subject=subject, input={"kind": "text", "hint": t(lang, "need.quantity.hint")}))
    items = [it for it in items if not it.get("skipped")]
    # where it is delivered: the request's own words, else the install's setting, else asked once
    place = _text(brief.get("delivery_place")) or _text(defaults.get("proc_delivery_place"))
    place_from = "request" if _text(brief.get("delivery_place")) else ("setting" if place else "")
    if not place and items:
        given = answers.get("proc_place")
        if isinstance(given, dict) and _text(given.get("typed")):
            place, place_from = _text(given["typed"]), "answer"
        elif given == "pickup":
            place_from = "pickup"
            assumptions.append({"code": "proc_place", "source": "answer", "text": t(lang, "need.assume.pickup")})
        elif given is None:
            findings.append(finding("proc_place", "blocking", "preflight", t(lang, "need.place.header"), t(lang, "need.place.ask"),
                                    [(t(lang, "need.place.blank"), "blank", t(lang, "need.place.blank.note")),
                                     (t(lang, "need.place.pickup"), "pickup", t(lang, "need.place.pickup.note"))],
                                    "blank", input={"kind": "text", "hint": t(lang, "need.place.hint")}))
    budget = None
    b = brief.get("budget")
    if isinstance(b, dict):
        amount = money.readings(str(b.get("amount") or ""))
        if amount and amount[0] > 0:
            budget = {"amount": money.text(amount[0]), "currency": _text(b.get("currency"), 3).upper() or None,
                      "each": b.get("basis") == "each"}
    need = {
        "items": items,
        "delivery_place": place, "delivery_place_from": place_from,
        "needed_by": _text(brief.get("needed_by"), 60),
        "budget": budget,
        "preferred": [s for s in (_text(x, 80) for x in (brief.get("preferred_suppliers") or [])[:5]) if s]
        if isinstance(brief.get("preferred_suppliers"), list) else [],
    }
    return {"need": need, "findings": findings, "assumptions": assumptions, "ready": bool(items) and not findings}
