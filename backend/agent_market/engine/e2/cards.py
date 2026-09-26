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
}
HEADER = {  # ≤ 12 display characters
    "seller_name": "ผู้ขาย", "seller_tax_id": "เลขภาษีผู้ขาย", "seller_branch": "สาขาผู้ขาย", "buyer_name": "ผู้ซื้อ",
    "buyer_tax_id": "เลขภาษีผู้ซื้อ", "document_number": "เลขที่", "document_date": "วันที่", "due_date": "ครบกำหนด",
    "sum_lines": "รวมเงิน", "discount": "ส่วนลด", "subtotal": "ยอดก่อนVAT", "vat_amount": "VAT", "total": "ยอดรวม",
    "wht_amount": "หัก ณ ที่จ่าย", "net_payable": "ยอดสุทธิ", "currency": "สกุลเงิน",
}
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
INPUT_KIND = {"money": "money", "tax_id": "tax_id", "date": "date", "code": "text", "party": "text", "text": "text"}
INPUT_HINT = {"money": "พิมพ์ตัวเลข เช่น 12,850.70", "tax_id": "พิมพ์เลข 13 หลัก", "date": "พิมพ์วันที่ เช่น 15/09/2569",
              "text": "พิมพ์ตามที่เห็นในเอกสาร"}
EXCLUDE = ("ตัดเอกสารนี้ออก", "exclude", "ไม่นำเอกสารนี้เข้าตาราง บันทึกเป็นข้อยกเว้นให้คนตรวจ")


def _doc_label(doc: dict) -> str:
    f = doc.get("fields") or {}
    seller = ((f.get("seller_name") or {}).get("normalized") or {}).get("core") if f.get("seller_name") else None
    num = (f.get("document_number") or {}).get("normalized")
    name = doc.get("document", {}).get("name", "")
    parts = [p for p in (seller, f"เลขที่ {num}" if num else None) if p]
    return " ".join(parts) + f" ({name})" if parts else name


def _money(v) -> str:
    try:
        from decimal import Decimal

        return f"{Decimal(str(v)):,.2f}"
    except Exception:  # noqa: BLE001
        return str(v)


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
            shown = _money(q["proposed"]) if kind == "money" and q.get("proposed") is not None else q.get("proposed")
            what = FIELD_TH.get(field, field)
            if code == "source_conflict":
                text = (f"{label}: {what} อ่านได้ไม่ตรงกันจากสองแหล่ง ({q.get('reason')}) "
                        f"ค่าไหนถูก หรือพิมพ์ค่าที่ถูกต้อง{note}")
            elif shown is None:
                text = f"{label}: อ่าน{what}ไม่ได้ ช่วยพิมพ์ค่าที่เห็นในเอกสาร{note}"
            else:
                text = f"{label}: อ่าน{what}ได้ {shown} แต่ไม่มั่นใจพอ ตัวเลขนี้ถูกต้องไหม{note}"
            if shown is not None:
                options = [(f"ถูกต้อง {shown}"[:60], {"accept": str(q["proposed"])}, "ยืนยันค่าที่อ่านได้"), EXCLUDE]
            else:
                options = [("ไม่มีในเอกสาร", "absent", "เอกสารไม่ได้พิมพ์ค่านี้"), EXCLUDE]
            out.append(finding(f"e2_{code}", "blocking", "preflight", HEADER.get(field, "ค่าในเอกสาร"), text,
                               options, "exclude", evidence=ev, subject=subj,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]},
                               facts={"field": field, "proposed": shown}))
        elif code == "field_missing" and field in FIELD_KINDS:
            kind = FIELD_KINDS[field]
            what = FIELD_TH.get(field, field)
            text = f"{label}: ไม่พบ{what}ในเอกสาร ช่วยพิมพ์ค่าที่เห็น หรือยืนยันว่าเอกสารไม่มี{note}"
            out.append(finding("e2_field_missing", "blocking", "preflight", HEADER.get(field, "ค่าที่ขาด"), text,
                               [("ไม่มีในเอกสาร", "absent", "บันทึกเป็นข้อยกเว้น: เอกสารไม่ครบ"), EXCLUDE], "exclude",
                               evidence=ev, subject=subj,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]}, facts={"field": field}))
        elif code == "check_failed":
            head, why = CHECK_TH.get(subject, ("ตรวจไม่ผ่าน", q.get("reason") or subject))
            text = f"{label}: {why} — {q.get('reason') or ''}".strip(" —")
            opts = [("ใช้ตามเอกสาร เป็นข้อยกเว้น", "accept_as_printed", "บันทึกตามที่พิมพ์ พร้อมข้อยกเว้นให้คนตรวจ"), EXCLUDE]
            inp = None
            if subject == "currency":
                opts = [("ระบุอัตราแลกเปลี่ยน", "rate", "พิมพ์อัตรา เช่น 36.25"), EXCLUDE]
                inp = {"kind": "text", "hint": "พิมพ์อัตราแลกเปลี่ยนเป็นบาทต่อ 1 หน่วย เช่น 36.25"}
            out.append(finding("e2_check_failed", "blocking", "preflight", head, text, opts, "exclude",
                               evidence=ev, subject=subj, input=inp, facts={"check": subject}))
        elif code == "duplicate_candidate":
            text = f"{label}: {q.get('reason') or 'ดูเหมือนเอกสารที่เคยบันทึกแล้ว'} เป็นเอกสารซ้ำหรือไม่"
            out.append(finding("e2_duplicate", "blocking", "preflight", "เอกสารซ้ำ", text,
                               [("ซ้ำ ตัดออก", "exclude", "ไม่นับซ้ำ"), ("ไม่ซ้ำ นับรวม", "include", "เป็นเอกสารคนละฉบับ")],
                               "exclude", evidence=ev, subject=subj))
        elif code == "copy_not_original":
            out.append(finding("e2_copy", "cosmetic", "preflight", "สำเนา",
                               f"{label}: เอกสารนี้เป็นสำเนา ขอคืนภาษีซื้อต้องใช้ต้นฉบับ",
                               [("รับทราบ", "ok", "บันทึกตามเดิม พร้อมหมายเหตุว่าเป็นสำเนา"), EXCLUDE], "ok",
                               evidence=ev, subject=subj))
        elif code == "document_type_unknown":
            out.append(finding("e2_doc_type", "blocking", "preflight", "ชนิดเอกสาร",
                               f"{label}: ไม่แน่ใจว่าเป็นเอกสารชนิดใด",
                               [("ใบกำกับภาษีเต็มรูป", "full_tax_invoice", ""), ("ใบกำกับภาษีอย่างย่อ", "abbreviated_tax_invoice", ""),
                                ("ใบเสร็จรับเงิน", "receipt", ""), EXCLUDE], "exclude", evidence=ev, subject=subj))
        elif code == "page_quality":
            out.append(finding("e2_page_quality", "blocking", "preflight", "ภาพไม่ชัด",
                               f"{label}: หน้า {subject[1:]} อ่านได้ไม่ชัด ตัวเลขการเงินในหน้านี้อาจผิด",
                               [("อัปโหลดไฟล์ที่ชัดกว่า", "reupload", ""), EXCLUDE], "exclude", evidence=ev, subject=subj))
    return out


def answer_for(answers: dict, f: Finding):
    """The answer the person gave to one of these findings (by key), or None."""
    return answers.get(f.key)
