"""Deterministic checks on an extraction — independent of any model (AM2).

Every money figure, tax ID, date and total goes through here. A check
either passes, fails (with expected vs actual) or is skipped because it does
not apply to this document (a receipt has no VAT; only a full tax invoice
is matched against the buyer). Tolerance for money is one satang (0.01) —
one rounding step, which absorbs a system that truncates VAT
(86.415 → 86.41) and nothing more.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from . import thai_text as T
from .schema import REQUIRED, Check, Field

TOL = Decimal("0.01")
VAT_RATE = Decimal("7")


def _v(fields: dict[str, Field], name: str):
    f = fields.get(name)
    return f.normalized if f is not None else None


def run(fields: dict[str, Field], items: list[dict], *, doc_type: str, vat_mode: str, org: dict | None,
        history: list[dict] | None, copy_mark: str, today: _dt.date | None = None,
        agreement: dict | None = None) -> list[Check]:
    today = today or _dt.date.today()
    out: list[Check] = []
    add = out.append

    add(Check("document_type", "pass" if doc_type not in (None, "unknown") else "fail",
              expected="a known document type", actual=doc_type, fields=["document_type"]))

    for who in ("seller", "buyer"):
        tid = _v(fields, f"{who}_tax_id")
        if tid is None:
            add(Check(f"{who}_tax_id_format", "skip", fields=[f"{who}_tax_id"], message="not printed"))
        else:
            ok = T.tax_id_check_ok(tid)
            add(Check(f"{who}_tax_id_format", "pass" if ok else "fail", expected="13 digits with a valid check digit",
                      actual=tid, fields=[f"{who}_tax_id"],
                      message="" if ok else "the 13th digit does not match the other twelve (mod-11 check digit)"))

    d = _v(fields, "document_date")
    if d is None:
        add(Check("document_date", "fail", expected="a date", actual=None, fields=["document_date"], message="no date found"))
    else:
        bad = d > today + _dt.timedelta(days=3) or d.year < 2000
        add(Check("document_date", "fail" if bad else "pass", expected=f"not after {today + _dt.timedelta(days=3)}",
                  actual=d, fields=["document_date"], message="a date in the future or implausibly old" if bad else ""))

    # line items
    rows = [it for it in items if it.get("quantity") and it["quantity"].present and it.get("unit_price")
            and it["unit_price"].present and it.get("amount") and it["amount"].present]
    if not rows:
        add(Check("line_arithmetic", "skip", fields=["line_items"], message="no quantity × price rows"))
    else:
        bad = []
        for it in rows:
            want = T.q2(it["quantity"].normalized * it["unit_price"].normalized)
            if abs(want - it["amount"].normalized) > TOL:
                bad.append({"row": it["row"], "expected": f"{want:.2f}", "printed": f"{it['amount'].normalized:.2f}"})
        add(Check("line_arithmetic", "fail" if bad else "pass", expected="quantity × unit price = amount",
                  actual=bad or None, fields=["line_items"],
                  message="; ".join(f"row {b['row']}: {b['printed']} printed, {b['expected']} computed" for b in bad)))
    amounts = [it["amount"].normalized for it in items if it.get("amount") and it["amount"].present]
    subtotal, total, vat = _v(fields, "subtotal"), _v(fields, "total"), _v(fields, "vat_amount")
    sum_lines, discount = _v(fields, "sum_lines"), _v(fields, "discount") or Decimal("0")
    if not amounts:
        add(Check("subtotal_sum", "skip", fields=["line_items"], message="no line amounts"))
    else:
        s = sum(amounts, Decimal("0"))
        target_name = "total" if vat_mode == "inclusive" else ("sum_lines" if sum_lines is not None else "subtotal")
        target = _v(fields, target_name)
        if vat_mode == "inclusive" and target is None:
            target = subtotal
        want = s - (discount if target_name != "sum_lines" and vat_mode != "inclusive" else Decimal("0"))
        if target is None:
            add(Check("subtotal_sum", "fail", expected=want, actual=None, fields=["line_items", target_name],
                      message="no subtotal to compare the lines with"))
        else:
            add(Check("subtotal_sum", "pass" if abs(want - target) <= TOL else "fail", expected=want, actual=target,
                      fields=["line_items", target_name]))
            if target_name == "sum_lines" and subtotal is not None:
                add(Check("discount_arithmetic", "pass" if abs(sum_lines - discount - subtotal) <= TOL else "fail",
                          expected=sum_lines - discount, actual=subtotal, fields=["sum_lines", "discount", "subtotal"]))

    if vat_mode == "none" or vat is None:
        add(Check("vat_7_percent", "skip", fields=["vat_amount"], message="no VAT on this document"))
        add(Check("grand_total", "skip", fields=["total"], message="no VAT on this document"))
    else:
        rate = Decimal(_v(fields, "vat_rate") or VAT_RATE)
        if vat_mode == "inclusive":
            want = T.q2(total * rate / (Decimal("100") + rate)) if total is not None else None
        else:
            want = T.q2(subtotal * rate / Decimal("100")) if subtotal is not None else None
        if want is None:
            add(Check("vat_7_percent", "fail", expected="a VAT base", actual=vat, fields=["vat_amount", "subtotal"],
                      message="no base to check the VAT against"))
        else:
            ok = abs(want - vat) <= TOL and rate == VAT_RATE
            # the base is sum of lines − discount: a failure can be any of them
            base_parts = ["subtotal", "sum_lines", "discount"] if vat_mode != "inclusive" else ["total"]
            add(Check("vat_7_percent", "pass" if ok else "fail", expected=want, actual=vat,
                      fields=["vat_amount"] + base_parts,
                      message="" if ok else f"VAT should be {want:.2f} at {rate}% of the base"))
        if subtotal is None or total is None:
            add(Check("grand_total", "fail", expected="subtotal + VAT", actual=total,
                      fields=["subtotal", "vat_amount", "total", "sum_lines", "discount"]))
        else:
            add(Check("grand_total", "pass" if abs(subtotal + vat - total) <= TOL else "fail", expected=subtotal + vat,
                      actual=total, fields=["subtotal", "vat_amount", "total", "sum_lines", "discount"]))

    wht, net, wrate = _v(fields, "wht_amount"), _v(fields, "net_payable"), _v(fields, "wht_rate")
    if wht is None and net is not None and total is not None and abs(net - total) > TOL:
        add(Check("wht", "fail", expected="a withholding amount (the net payable is not the total)",
                  actual={"net": net, "total": total}, fields=["wht_amount", "net_payable", "wht_rate"],
                  message=f"net payable {net:.2f} is not the total {total:.2f}, and no withholding amount was read"))
    elif wht is None:
        add(Check("wht", "skip", fields=["wht_amount"], message="no withholding tax on this document"))
    else:
        problems = []
        base = subtotal if subtotal is not None else sum_lines
        if wrate is not None and base is not None and abs(T.q2(base * Decimal(wrate) / 100) - wht) > TOL:
            problems.append(f"{wrate}% of {base:.2f} is {T.q2(base * Decimal(wrate) / 100):.2f}, printed {wht:.2f}")
        if net is not None and total is not None and abs(total - wht - net) > TOL:
            problems.append(f"total − WHT = {total - wht:.2f}, net printed {net:.2f}")
        if net is None:
            problems.append("no net payable")
        add(Check("wht", "fail" if problems else "pass", expected="rate × base = WHT; total − WHT = net",
                  actual={"wht": wht, "net": net, "rate": wrate}, fields=["wht_amount", "net_payable", "wht_rate"],
                  message="; ".join(problems)))

    cur = _v(fields, "currency") or "THB"
    add(Check("currency", "pass" if cur == "THB" else "fail", expected="THB", actual=cur, fields=["currency"],
              message="" if cur == "THB" else f"amounts are in {cur}: the THB amount needs an exchange-rate source"))

    req = REQUIRED.get(doc_type, ())
    missing = [n for n in req if _v(fields, n) is None]
    add(Check("required_fields", "fail" if missing else "pass", expected=list(req), actual=missing or None,
              fields=missing, message=f"missing for a {doc_type}: {', '.join(missing)}" if missing else ""))

    btid = _v(fields, "buyer_tax_id")
    if doc_type != "full_tax_invoice" or btid is None or not org or not org.get("tax_id"):
        add(Check("buyer_match", "skip", fields=["buyer_tax_id"],
                  message="only a full tax invoice with a buyer tax ID is matched against the organisation"))
    else:
        ok = btid == org["tax_id"]
        add(Check("buyer_match", "pass" if ok else "fail", expected=org["tax_id"], actual=btid, fields=["buyer_tax_id"],
                  message="" if ok else "made out to another tax ID: input VAT cannot be claimed on it"))

    dup = duplicate_of(fields, history or [])
    add(Check("duplicate", "fail" if dup else "pass", expected="not seen before", actual=dup, fields=["document_number"],
              message=f"same supplier and {dup['match']} as {dup['name']}" if dup else ""))

    add(Check("original_copy", "fail" if copy_mark == "copy" else "pass", expected="original", actual=copy_mark,
              fields=["document_type"], message="marked สำเนา / copy" if copy_mark == "copy" else ""))

    words = _v(fields, "amount_in_words")
    target = _v(fields, "net_payable") or total
    if words is None or target is None:
        add(Check("amount_in_words", "skip", fields=["amount_in_words"]))
    else:
        ok = abs(words - target) <= TOL or (total is not None and abs(words - total) <= TOL)
        add(Check("amount_in_words", "pass" if ok else "fail", expected=target, actual=words,
                  fields=["amount_in_words", "total"], message="" if ok else "the amount in words is not the total"))

    for name, info in (agreement or {}).items():
        add(Check(f"source_agreement:{name}", "pass" if info["agree"] else "fail", expected=info.get("primary"),
                  actual=info.get("other"), fields=[name], message=info.get("message", "")))
    return out


def duplicate_of(fields: dict[str, Field], history: list[dict]) -> dict | None:
    """Same supplier tax ID + document number, or same supplier + date + total (a re-typed number)."""
    tid, num = _v(fields, "seller_tax_id"), _v(fields, "document_number")
    date, total = _v(fields, "document_date"), _v(fields, "total")
    for h in history:
        if not tid or h.get("seller_tax_id") != tid:
            continue
        if num and T.arabic(str(h.get("document_number") or "")).replace(" ", "") == str(num).replace(" ", ""):
            return {"name": h.get("name"), "match": "document number", "document_number": num}
        if date and total is not None and str(h.get("document_date")) == str(date) and \
                Decimal(str(h.get("total"))) == total:
            return {"name": h.get("name"), "match": "date and total", "document_number": h.get("document_number")}
    return None
