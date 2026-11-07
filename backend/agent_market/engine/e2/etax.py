"""ETDA e-Tax Invoice / e-Receipt XML (ขมธอ. 3-2560) → E2 fields.

An e-Tax document is structured data: when a PDF carries the XML as an
embedded file (PDF/A-3 style) or the XML arrives alone, the XML is the
source of every field and the PDF text layer only confirms it
(source_agreement). Element names are matched by local name so the
namespace version does not matter.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from . import thai_text as T
from .schema import Field, Source

CONF = 0.995


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, *path):
    cur = [el]
    for name in path:
        nxt = []
        for c in cur:
            nxt.extend(x for x in c.iter() if _local(x.tag) == name and x is not c)
        if not nxt:
            return None
        cur = nxt[:1]
    return cur[0]


def _findall(el, name):
    return [x for x in el.iter() if _local(x.tag) == name]


def _dec(el) -> Decimal | None:
    if el is None or el.text is None:
        return None
    try:
        return T.q2(Decimal(el.text.strip()))
    except InvalidOperation:
        return None


def embedded_xml(pdf_path: str) -> tuple[str, bytes] | None:
    """The first embedded .xml file of a PDF (an e-Tax PDF/A-3 carries its XML this way)."""
    try:
        from pypdf import PdfReader

        att = PdfReader(pdf_path).attachments
    except Exception:  # noqa: BLE001 — not a PDF with attachments
        return None
    for name, datas in (att or {}).items():
        if name.lower().endswith(".xml") and datas:
            return name, datas[0]
    return None


def is_etda(data: bytes) -> bool:
    head = data[:2000].decode("utf-8", "ignore")
    return "CrossIndustryInvoice" in head or "etda" in head.lower()


def parse(data: bytes, source_name: str) -> dict:
    root = ET.fromstring(data)

    def f(value, normalized, path_desc) -> Field:
        return Field(value=value, normalized=normalized, confidence=CONF,
                     source=Source(page=None, bbox=None, span=None, method="etax_xml",
                                   raw_text=f"{source_name}: {path_desc}"[:200]))

    out: dict[str, Field] = {}
    doc = _find(root, "ExchangedDocument")
    if doc is not None:
        num = _find(doc, "ID")
        if num is not None and num.text:
            out["document_number"] = f(num.text.strip(), T.arabic(num.text.strip()), "ExchangedDocument/ID")
        tc = _find(doc, "TypeCode")
        dtype = T.ETAX_TYPE.get((tc.text or "").strip()) if tc is not None else None
        issue = _find(doc, "IssueDateTime")
        if issue is not None and issue.text:
            hit = T.parse_date(issue.text[:10])
            if hit:
                out["document_date"] = f(issue.text.strip(), hit[0], "ExchangedDocument/IssueDateTime")
    else:
        dtype = None
    for tag, prefix in (("SellerTradeParty", "seller"), ("BuyerTradeParty", "buyer")):
        p = _find(root, tag)
        if p is None:
            continue
        name = _find(p, "Name")
        if name is not None and name.text:
            out[f"{prefix}_name"] = f(name.text.strip(), T.legal_form(name.text.strip()) or {"legal_form": "unknown", "core": name.text.strip()},
                                      f"{tag}/Name")
        reg = _find(p, "SpecifiedTaxRegistration", "ID")
        if reg is not None and reg.text:
            digits = "".join(ch for ch in T.arabic(reg.text) if ch.isdigit())
            if len(digits) >= 13:
                out[f"{prefix}_tax_id"] = f(reg.text.strip(), digits[:13], f"{tag}/SpecifiedTaxRegistration/ID")
                if len(digits) >= 18:
                    out[f"{prefix}_branch"] = f(digits[13:18], digits[13:18], f"{tag}/SpecifiedTaxRegistration/ID")
        addr = _find(p, "PostalTradeAddress", "LineOne")
        if addr is not None and addr.text:
            out[f"{prefix}_address"] = f(addr.text.strip(), addr.text.strip(), f"{tag}/PostalTradeAddress")
    cur = _find(root, "InvoiceCurrencyCode")
    out["currency"] = f(cur.text.strip() if cur is not None and cur.text else "THB",
                        cur.text.strip() if cur is not None and cur.text else "THB", "InvoiceCurrencyCode")
    tax = _find(root, "ApplicableTradeTax")
    if tax is not None:
        rate = _find(tax, "CalculatedRate")
        if rate is not None and rate.text:
            out["vat_rate"] = f(rate.text.strip(), f"{Decimal(rate.text.strip()):.0f}", "ApplicableTradeTax/CalculatedRate")
    summ = _find(root, "SpecifiedTradeSettlementHeaderMonetarySummation")
    if summ is not None:
        for el, name in (("LineTotalAmount", "sum_lines"), ("AllowanceTotalAmount", "discount"),
                         ("TaxBasisTotalAmount", "subtotal"), ("TaxTotalAmount", "vat_amount"),
                         ("GrandTotalAmount", "total")):
            v = _dec(_find(summ, el))
            if v is not None:
                out[name] = f(str(v), v, f"MonetarySummation/{el}")
    items = []
    for n, li in enumerate(_findall(root, "IncludedSupplyChainTradeLineItem"), 1):
        name = _find(li, "SpecifiedTradeProduct", "Name")
        qty = _find(li, "BilledQuantity")
        price = _find(li, "ChargeAmount")
        amount = _find(li, "NetLineTotalAmount")
        items.append({"row": n, "page": None,
                      "description": f(name.text if name is not None else None, name.text if name is not None else None, "LineItem/Name"),
                      "quantity": f(qty.text if qty is not None else None,
                                    Decimal(qty.text) if qty is not None and qty.text else None, "LineItem/BilledQuantity"),
                      "unit_price": f(price.text if price is not None else None, _dec(price), "LineItem/ChargeAmount"),
                      "amount": f(amount.text if amount is not None else None, _dec(amount), "LineItem/NetLineTotalAmount")})
    dt = Field(value=(tc.text.strip() if doc is not None and tc is not None and tc.text else None),
               normalized=dtype or "unknown", confidence=CONF if dtype else 0.0,
               source=Source(page=None, bbox=None, span=None, method="etax_xml", raw_text=f"{source_name}: TypeCode"))
    return {"fields": out, "line_items": items, "document_type": dt}
