"""E2 questions → the existing clarification cards (engine/findings.py).

No second question mechanism: every E2 question becomes a ``Finding``, which
the runner's clarification engine (clarify.go) turns into the same cards AM1
asks with. Keys are per document and field (``e2_low_confidence:<doc>:<field>``)
so an answer can never land on another document's question.

The rule these cards enforce (AM2): a model may propose, a person decides.
Every financial card's *default* — what a skipped card records — is to leave
the document out as an exception, never to accept the number. A card that
takes a typed value says so (``input``); the typed words come back as
``{"typed": ...}`` and are checked by ``typed.parse`` before anything uses them.
"""

from __future__ import annotations

from ..findings import Finding, finding
from .schema import FIELD_KINDS

FIELD_TH = {
    "seller_name": "ชื่อผู้ขาย", "seller_tax_id": "เลขผู้เสียภาษีผู้ขาย", "seller_branch": "สาขาผู้ขาย",
    "buyer_name": "ชื่อผู้ซื้อ", "buyer_tax_id": "เลขผู้เสียภาษีผู้ซื้อ", "document_number": "เลขที่เอกสาร",
    "document_date": "วันที่เอกสาร", "due_date": "วันครบกำหนด", "sum_lines": "รวมเป็นเงิน", "discount": "ส่วนลด",
    "subtotal": "ยอดก่อน VAT", "vat_amount": "VAT", "total": "ยอดรวมทั้งสิ้น", "wht_amount": "หัก ณ ที่จ่าย",
    "net_payable": "ยอดชำระสุทธิ", "currency": "สกุลเงิน",
    # AM3: every field a card can name has a Thai name (a card once said "อ่านbank_accountได้")
    "buyer_branch": "สาขาผู้ซื้อ", "seller_address": "ที่อยู่ผู้ขาย", "buyer_address": "ที่อยู่ผู้ซื้อ",
    "vat_rate": "อัตรา VAT", "wht_rate": "อัตราหัก ณ ที่จ่าย", "amount_in_words": "จำนวนเงินตัวอักษร",
    "bank_account": "เลขบัญชีธนาคาร",
}
HEADER = {  # ≤ 12 display characters
    "seller_name": "ผู้ขาย", "seller_tax_id": "เลขภาษีผู้ขาย", "seller_branch": "สาขาผู้ขาย", "buyer_name": "ผู้ซื้อ",
    "buyer_tax_id": "เลขภาษีผู้ซื้อ", "document_number": "เลขที่", "document_date": "วันที่", "due_date": "ครบกำหนด",
    "sum_lines": "รวมเงิน", "discount": "ส่วนลด", "subtotal": "ยอดก่อนVAT", "vat_amount": "VAT", "total": "ยอดรวม",
    "wht_amount": "หัก ณ ที่จ่าย", "net_payable": "ยอดสุทธิ", "currency": "สกุลเงิน",
    "buyer_branch": "สาขาผู้ซื้อ", "seller_address": "ที่อยู่ผู้ขาย", "buyer_address": "ที่อยู่ผู้ซื้อ",
    "vat_rate": "อัตรา VAT", "wht_rate": "อัตราหักภาษี", "amount_in_words": "ตัวอักษร", "bank_account": "เลขบัญชี",
}
DOC_TYPE_TH = {"full_tax_invoice": "ใบกำกับภาษีเต็มรูป", "abbreviated_tax_invoice": "ใบกำกับภาษีอย่างย่อ",
               "receipt": "ใบเสร็จรับเงิน", "invoice": "ใบแจ้งหนี้", "credit_note": "ใบลดหนี้", "debit_note": "ใบเพิ่มหนี้"}
# where a value came from, as a person would say it (a source_conflict card names both readings)
SOURCE_TH = {"etax_xml": ("ข้อมูล e-Tax (XML)", "ตัวหนังสือในไฟล์ PDF")}
SOURCE_DEFAULT = ("ตัวหนังสือในเอกสาร", "โมเดลในองค์กรอ่านจากภาพ")
MATCH_TH = {"document number": "เลขที่เอกสาร", "date and total": "วันที่และยอดรวม"}
CHECK_TH = {
    "line_arithmetic": ("ยอดรายการ", "จำนวน × ราคาต่อหน่วย ไม่เท่ากับยอดของบางรายการ"),
    "subtotal_sum": ("ผลรวมรายการ", "ผลรวมของรายการไม่เท่ากับยอดรวมที่พิมพ์"),
    "discount_arithmetic": ("ส่วนลดไม่ตรง", "รวมเป็นเงิน − ส่วนลด ไม่เท่ากับยอดหลังหักส่วนลด"),
    "vat_7_percent": ("VAT ไม่ตรง", "ภาษีมูลค่าเพิ่มไม่เท่ากับ 7% ของฐาน"),
    "grand_total": ("ยอดรวม", "ยอดก่อน VAT + VAT ไม่เท่ากับยอดรวมทั้งสิ้น"),
    "wht": ("หัก ณ ที่จ่าย", "ตัวเลขภาษีหัก ณ ที่จ่ายไม่สอดคล้องกัน"),
    "currency": ("สกุลเงิน", "เอกสารเป็นเงินตราต่างประเทศ ต้องมีอัตราแลกเปลี่ยนก่อนบันทึกเป็นบาท"),
    "buyer_match": ("ผู้ซื้อไม่ตรง", "ใบกำกับภาษีออกในนามเลขผู้เสียภาษีอื่น ขอคืนภาษีซื้อไม่ได้"),
    "seller_tax_id_format": ("เลขภาษีผิด", "เลขผู้เสียภาษีของผู้ขายไม่ผ่านการตรวจหลักสุดท้าย"),
    "buyer_tax_id_format": ("เลขภาษีผิด", "เลขผู้เสียภาษีของผู้ซื้อไม่ผ่านการตรวจหลักสุดท้าย"),
    "document_date": ("วันที่แปลก", "วันที่เอกสารอยู่ในอนาคตหรือเก่าผิดปกติ"),
    "amount_in_words": ("ตัวอักษร", "จำนวนเงินตัวอักษรไม่เท่ากับยอดรวม"),
}
INPUT_KIND = {"money": "money", "tax_id": "tax_id", "date": "date", "code": "text", "party": "text", "text": "text",
              "rate": "rate", "bank_account": "text"}
INPUT_HINT = {"money": "พิมพ์ตัวเลข เช่น 12,850.70", "tax_id": "พิมพ์เลข 13 หลัก", "date": "พิมพ์วันที่ เช่น 15/09/2569",
              "rate": "พิมพ์ตัวเลข เช่น 36.25", "text": "พิมพ์ตามที่เห็นในเอกสาร"}
EXCLUDE = ("ตัดเอกสารนี้ออก", "exclude", "ไม่นำเอกสารนี้เข้าทะเบียน บันทึกเป็นข้อยกเว้นให้คนตรวจ")


def _doc_label(doc: dict) -> str:
    """How a card names a document: the seller as printed (only when it was read with certainty — an
    OCR'd or photographed name can be garbled), the document number, and always the file name."""
    f = doc.get("fields") or {}
    s = f.get("seller_name") or {}
    seller = None
    if s.get("value") and (s.get("confidence") or 0) >= 0.95 and not doc.get("document", {}).get("triage_only"):
        seller = " ".join(str(s["value"]).replace("\x00", "").split())
        if len(seller) > 40:
            seller = seller[:39] + "…"
    num = (f.get("document_number") or {}).get("normalized")
    name = doc.get("document", {}).get("name", "")
    parts = [p for p in (seller, f"เลขที่ {num}" if num else None) if p]
    return " ".join(parts) + f" ({name})" if parts else name


def _where(ev: dict) -> str:
    return f" (ดูที่หน้า {ev['page']})" if ev.get("page") else ""


def _plain(v) -> str:
    """An expected/actual value from a check, as a person reads it."""
    if v is None:
        return ""
    try:
        from decimal import Decimal, InvalidOperation

        return f"{Decimal(str(v)):,.2f}" if str(v).replace(".", "", 1).replace("-", "", 1).isdigit() else str(v)
    except (InvalidOperation, ValueError):
        return str(v)


def _check_detail(code: str, ev: dict, reason: str) -> str:
    """What a failed check found, in Thai, from its own numbers (never the engine's English message)."""
    import re

    exp, act = ev.get("expected"), ev.get("actual")

    def num(v) -> bool:
        return v is not None and not isinstance(v, (dict, list)) and str(v).replace(".", "", 1).replace("-", "", 1).isdigit()

    if code == "vat_7_percent" and num(act) and num(exp):
        return f"เอกสารพิมพ์ VAT {_plain(act)} บาท แต่ 7% ของยอดก่อน VAT คือ {_plain(exp)} บาท"
    if code == "grand_total" and num(act) and num(exp):
        return f"เอกสารพิมพ์ยอดรวม {_plain(act)} บาท แต่ยอดก่อน VAT + VAT = {_plain(exp)} บาท"
    if code == "subtotal_sum" and num(act) and num(exp):
        return f"รายการรวมกันได้ {_plain(exp)} บาท แต่เอกสารพิมพ์ {_plain(act)} บาท"
    if code == "buyer_match" and act:
        return f"เอกสารออกในนามเลขผู้เสียภาษี {act} แต่บริษัทของคุณคือ {exp}"
    if code in ("seller_tax_id_format", "buyer_tax_id_format") and act:
        return f"เลขในเอกสาร {act} หลักสุดท้ายไม่ตรงกับ 12 หลักแรก อาจพิมพ์หรืออ่านผิด"
    if code == "document_date" and act:
        return f"วันที่ในเอกสาร {act}"
    if code == "currency" and act:
        return f"เอกสารเป็นเงินสกุล “{act}”"
    if code == "amount_in_words" and act:
        return f"ตัวอักษรในเอกสาร “{str(act)[:60]}”"
    if code == "wht" and isinstance(act, dict):
        if "net" in act and "total" in act and "wht" not in act:
            return f"ยอดชำระสุทธิ {_plain(act['net'])} บาท ไม่เท่ากับยอดรวม {_plain(act['total'])} บาท แต่อ่านยอดหัก ณ ที่จ่ายไม่ได้"
        bits = [f"หัก {_plain(act.get('wht'))} บาท" if act.get("wht") is not None else "",
                f"อัตรา {act['rate']}%" if act.get("rate") is not None else "",
                f"ยอดสุทธิ {_plain(act.get('net'))} บาท" if act.get("net") is not None else "ไม่พบยอดชำระสุทธิ"]
        return "เอกสารพิมพ์ " + " ".join(b for b in bits if b)
    if code == "line_arithmetic":
        rows = re.findall(r"row (\d+): ([\d.,-]+) printed, ([\d.,-]+) computed", reason or "")
        if rows:
            return " · ".join(f"รายการที่ {r} พิมพ์ {_plain(p)} แต่จำนวน × ราคาต่อหน่วย = {_plain(c)}" for r, p, c in rows[:3])
    return ""


def _money(v) -> str:
    try:
        from decimal import Decimal

        return f"{Decimal(str(v)):,.2f}"
    except Exception:  # noqa: BLE001
        return str(v)


def _shown(kind: str, proposed):
    if proposed is None:
        return None
    if kind == "money":
        return _money(proposed)
    if kind == "party" and isinstance(proposed, dict):
        return proposed.get("core") or None
    return proposed


def findings_for(doc_key: str, doc: dict) -> list[Finding]:
    """Findings for one E2 extraction (``doc`` is the e2.extraction.v1 dict)."""
    out: list[Finding] = []
    label = _doc_label(doc)
    triage = doc.get("document", {}).get("triage_only")
    note = " (รูปถ่าย อ่านแบบพยายามที่สุด)" if triage else ""
    for q in doc.get("questions") or []:
        code, subject, field = q["code"], q.get("subject") or "", q.get("field")
        subj = f"{doc_key}:{subject}" if subject else doc_key
        ev = {"document": doc.get("document", {}).get("name"), "question": q.get("reason"), **(q.get("evidence") or {})}
        if field in FIELD_KINDS:
            src = (doc.get("fields", {}).get(field) or {}).get("source") or {}
            ev["page"], ev["bbox"], ev["raw_text"] = src.get("page"), src.get("bbox"), src.get("raw_text")
        if code in ("low_confidence", "source_conflict") and field in FIELD_KINDS:
            kind = FIELD_KINDS[field]
            shown = _shown(kind, q.get("proposed"))
            what = FIELD_TH.get(field, field)
            this = "ตัวเลขนี้" if kind in ("money", "tax_id", "rate", "bank_account") else "ค่านี้"
            reason = str(q.get("reason") or "")
            other = _shown(kind, (q.get("evidence") or {}).get("other")) if code == "source_conflict" else None
            first, second = SOURCE_TH.get(doc.get("method"), SOURCE_DEFAULT)
            where = _where(ev)
            if code == "source_conflict" and shown is not None and other is not None:
                text = (f"{label}: {what}อ่านได้สองค่าที่ไม่ตรงกัน — {first}: “{shown}” · {second}: “{other}”{where} "
                        f"ค่าไหนตรงกับเอกสาร หรือพิมพ์ค่าที่ถูก{note}")
            elif code == "source_conflict":
                text = f"{label}: {what}อ่านได้ไม่ตรงกันสองแบบ{where} ช่วยพิมพ์ค่าที่เห็นในเอกสาร{note}"
            elif shown is None and reason.startswith("the label is printed"):
                text = f"{label}: เห็นหัวข้อ{what}ในเอกสาร แต่อ่านค่าไม่ออก{where} ช่วยพิมพ์ค่าที่เห็นในเอกสาร{note}"
            elif shown is None:
                text = f"{label}: อ่าน{what}ไม่ได้{where} ช่วยพิมพ์ค่าที่เห็นในเอกสาร{note}"
            elif reason.startswith("only a model"):
                text = (f"{label}: ตัวหนังสือในเอกสารอ่าน{what}ไม่ได้ โมเดลในองค์กรอ่านจากภาพได้ “{shown}”{where} "
                        f"ตรงกับที่เห็นในเอกสารไหม ถ้าไม่ตรง พิมพ์ค่าที่ถูก{note}")
            elif reason.startswith("the numbers do not add up"):
                why = CHECK_TH.get(str((q.get("evidence") or {}).get("check") or ""), ("", "ตัวเลขในเอกสารบวกกันไม่ลงตัว"))[1]
                text = (f"{label}: อ่าน{what}ได้ “{shown}” แต่{why}{where} ช่วยดูว่าเอกสารพิมพ์ “{shown}” จริงไหม "
                        f"ถ้าไม่ใช่ พิมพ์ค่าที่ถูก{note}")
            else:
                text = f"{label}: อ่าน{what}ได้ “{shown}” แต่ภาพไม่ชัดพอให้มั่นใจ{where} {this}ตรงกับเอกสารไหม ถ้าไม่ตรง พิมพ์ค่าที่ถูก{note}"
            if shown is not None and other is not None and str(other) != str(shown):
                # two readings: the person picks the one the document shows (or types it)
                raw_other = (q.get("evidence") or {}).get("other")
                options = [(f"ใช้ {shown}"[:60], {"accept": shown if kind == "party" else str(q["proposed"])}, f"ค่าจาก{first}"),
                           (f"ใช้ {other}"[:60], {"accept": other if kind == "party" else str(raw_other)}, f"ค่าจาก{second}"),
                           EXCLUDE]
            elif shown is not None:
                options = [(f"ถูกต้อง {shown}"[:60], {"accept": shown if kind == "party" else str(q["proposed"])},
                            f"ยืนยันว่าเอกสารพิมพ์{this}"), EXCLUDE]
            else:
                options = [("ไม่มีในเอกสาร", "absent", "เอกสารไม่ได้พิมพ์ค่านี้"), EXCLUDE]
            out.append(finding(f"e2_{code}", "blocking", "preflight", HEADER.get(field, "ค่าในเอกสาร"), text,
                               options, "exclude", evidence=ev, subject=subj,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]},
                               facts={"field": field, "proposed": shown}))
        elif code == "field_missing" and field in FIELD_KINDS:
            kind = FIELD_KINDS[field]
            what = FIELD_TH.get(field, field)
            reason = str(q.get("reason") or "")
            dtype = DOC_TYPE_TH.get(str((doc.get("document_type") or {}).get("normalized") or ""))
            if reason.startswith("could not be read, and"):
                text = f"{label}: อ่าน{what}ไม่ได้ และถ้าไม่มีค่านี้ ตัวเลขในเอกสารจะบวกกันไม่ลงตัว ช่วยพิมพ์ค่าที่เห็นในเอกสาร{note}"
            elif reason.startswith("a ") and dtype:
                text = f"{label}: {dtype}ต้องมี{what} แต่ไม่พบในเอกสารนี้ ช่วยพิมพ์ค่าที่เห็น หรือยืนยันว่าเอกสารไม่ได้พิมพ์ไว้{note}"
            else:
                text = f"{label}: ไม่พบ{what}ในเอกสาร ช่วยพิมพ์ค่าที่เห็น หรือยืนยันว่าเอกสารไม่ได้พิมพ์ไว้{note}"
            out.append(finding("e2_field_missing", "blocking", "preflight", HEADER.get(field, "ค่าที่ขาด"), text,
                               [("ไม่มีในเอกสาร", "absent", "บันทึกเป็นข้อยกเว้น: เอกสารไม่ครบ"), EXCLUDE], "exclude",
                               evidence=ev, subject=subj,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]}, facts={"field": field}))
        elif code == "check_failed":
            head, why = CHECK_TH.get(subject, ("ตรวจไม่ผ่าน", "ตัวเลขในเอกสารไม่ผ่านการตรวจ"))
            detail = _check_detail(subject, q.get("evidence") or {}, str(q.get("reason") or ""))
            text = f"{label}: {why}" + (f" ({detail})" if detail else "")
            opts = [("บันทึกตามเอกสาร มีข้อยกเว้น", "accept_as_printed",
                     "เข้าทะเบียนตามที่เอกสารพิมพ์ และมีข้อยกเว้นให้ผู้ตรวจดู"), EXCLUDE]
            inp = None
            if subject == "currency":
                opts = [("ระบุอัตราแลกเปลี่ยน", "rate", "พิมพ์อัตรา เช่น 36.25"), EXCLUDE]
                inp = {"kind": "rate", "hint": "พิมพ์อัตราแลกเปลี่ยนเป็นบาทต่อ 1 หน่วย เช่น 36.25"}
            out.append(finding("e2_check_failed", "blocking", "preflight", head, text, opts, "exclude",
                               evidence=ev, subject=subj, input=inp, facts={"check": subject}))
        elif code == "duplicate_candidate":
            m = (q.get("evidence") or {}).get("match") or {}
            if isinstance(m, dict) and m.get("name"):
                how = MATCH_TH.get(str(m.get("match")), "รายละเอียด")
                text = (f"{label}: ผู้ขายเดียวกันและ{how}ตรงกับ “{m['name']}” ที่บันทึกไว้แล้ว "
                        f"เป็นเอกสารฉบับเดียวกันที่ส่งซ้ำหรือไม่")
            else:
                text = f"{label}: ดูเหมือนเอกสารที่บันทึกไว้แล้ว เป็นเอกสารฉบับเดียวกันที่ส่งซ้ำหรือไม่"
            out.append(finding("e2_duplicate", "blocking", "preflight", "เอกสารซ้ำ", text,
                               [("ซ้ำ ตัดออก", "exclude", "ไม่นำฉบับนี้เข้าทะเบียนอีก"),
                                ("ไม่ซ้ำ นับรวม", "include", "เป็นเอกสารคนละฉบับ นำเข้าทะเบียนด้วย")],
                               "exclude", evidence=ev, subject=subj))
        elif code == "copy_not_original":
            # blocking: whether a copy is recorded changes which documents count (a cosmetic answer never
            # reaches the numbers); skipped → left out, like every other E2 card
            out.append(finding("e2_copy", "blocking", "preflight", "สำเนา",
                               f"{label}: เอกสารนี้เป็นสำเนา ขอคืนภาษีซื้อต้องใช้ต้นฉบับ บันทึกไว้หรือไม่",
                               [("บันทึก พร้อมหมายเหตุ", "ok", "บันทึกตามเดิม พร้อมข้อยกเว้นว่าเป็นสำเนา"), EXCLUDE], "exclude",
                               evidence=ev, subject=subj))
        elif code == "document_type_unknown":
            out.append(finding("e2_doc_type", "blocking", "preflight", "ชนิดเอกสาร",
                               f"{label}: หัวเอกสารไม่ได้บอกชัดว่าเป็นเอกสารชนิดไหน ดูหัวกระดาษแล้วเลือกให้หน่อย",
                               [("ใบกำกับภาษีเต็มรูป", "full_tax_invoice", "มีชื่อ ที่อยู่ และเลขผู้เสียภาษีของผู้ซื้อ"),
                                ("ใบกำกับภาษีอย่างย่อ", "abbreviated_tax_invoice", "ไม่มีชื่อผู้ซื้อ เช่น บิลจากร้านค้าปลีก"),
                                ("ใบเสร็จรับเงิน", "receipt", "ใบเสร็จที่ไม่ใช่ใบกำกับภาษี"), EXCLUDE],
                               "exclude", evidence=ev, subject=subj))
        elif code == "page_quality":
            out.append(finding("e2_page_quality", "blocking", "preflight", "ภาพไม่ชัด",
                               f"{label}: หน้า {subject[1:]} ภาพไม่ชัดพอ ตัวเลขเงินที่อ่านจากหน้านี้อาจผิด",
                               [("อัปโหลดไฟล์ที่ชัดกว่า", "reupload", "สแกนใหม่ที่ 300 DPI แล้วส่งมาแทน"), EXCLUDE],
                               "exclude", evidence=ev, subject=subj))
    return out


def answer_for(answers: dict, f: Finding):
    """The answer the person gave to one of these findings (by key), or None."""
    return answers.get(f.key)
