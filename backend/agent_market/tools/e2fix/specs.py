"""The E2 fixture pack: every document's truth, as data (AM2 Phase A).

Each spec says what is PRINTED on the document and what the TRUE values are;
they differ only where a fixture is designed to fail (a printed line total
that is not qty × price, a VAT that is not 7 %, a check digit that is wrong).
The expected JSON (expected.py) is computed from these specs alone — never
from the engine — so the eval compares two independent paths.
"""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from decimal import Decimal as D

from .thai import make_tax_id, q2

# ── parties ─────────────────────────────────────────────────────────────────

ORG = {  # the installing organisation: the buyer on every purchase invoice
    "name": "บริษัท สรานเทค โซลูชั่นส์ จำกัด", "legal_form": "company_limited", "core": "สรานเทค โซลูชั่นส์",
    "tax_id": make_tax_id("010556301234"), "branch": "00000",
    "address": "99/9 อาคารสรานทาวเวอร์ ชั้น 12 ถนนวิภาวดีรังสิต แขวงจตุจักร เขตจตุจักร กรุงเทพมหานคร 10900",
}
OTHER_BUYER = {
    "name": "บริษัท สยามเทรดดิ้ง อินเตอร์ จำกัด", "legal_form": "company_limited", "core": "สยามเทรดดิ้ง อินเตอร์",
    "tax_id": make_tax_id("010555800135"), "branch": "00000",
    "address": "45 ถนนสาทรใต้ แขวงยานนาวา เขตสาทร กรุงเทพมหานคร 10120",
}

SUPPLIERS = {
    "S1": {"name": "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด", "print_name": "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด",
           "legal_form": "company_limited", "core": "ไทยออฟฟิศ ซัพพลาย",
           "tax_id": make_tax_id("010554500987"), "branch": "00000",
           "address": "123 ถนนพระราม 4 แขวงคลองเตย เขตคลองเตย กรุงเทพมหานคร 10110", "tel": "02-249-1234"},
    "S2": {"name": "บริษัท เอเชีย คลาวด์ โซลูชั่นส์ จำกัด", "print_name": "บจก. เอเชีย คลาวด์ โซลูชั่นส์",
           "legal_form": "company_limited", "core": "เอเชีย คลาวด์ โซลูชั่นส์",
           "tax_id": make_tax_id("010556207761"), "branch": "00002",
           "address": "88/1 ถนนรัชดาภิเษก แขวงดินแดง เขตดินแดง กรุงเทพมหานคร 10400", "tel": "02-641-5566"},
    "S3": {"name": "ห้างหุ้นส่วนจำกัด ช่างเหล็กการโยธา", "print_name": "หจก. ช่างเหล็กการโยธา",
           "legal_form": "limited_partnership", "core": "ช่างเหล็กการโยธา",
           "tax_id": make_tax_id("034356100452"), "branch": "00000",
           "address": "17/3 หมู่ 5 ถนนรัตนาธิเบศร์ ตำบลบางกระสอ อำเภอเมืองนนทบุรี จังหวัดนนทบุรี 11000", "tel": "02-950-7788"},
    "S4": {"name": "บริษัท ไทยพาณิชย์ขนส่ง จำกัด (มหาชน)", "print_name": "บมจ. ไทยพาณิชย์ขนส่ง",
           "legal_form": "public_company_limited", "core": "ไทยพาณิชย์ขนส่ง",
           "tax_id": make_tax_id("010753700214"), "branch": "00000",
           "address": "200 ถนนบางนา-ตราด แขวงบางนา เขตบางนา กรุงเทพมหานคร 10260", "tel": "02-398-2000"},
    "S5": {"name": "บริษัท มินิมาร์ท 24 จำกัด", "print_name": "บริษัท มินิมาร์ท 24 จำกัด",
           "legal_form": "company_limited", "core": "มินิมาร์ท 24",
           "tax_id": make_tax_id("010554303377"), "branch": "01234",
           "address": "สาขาวิภาวดี 62 แขวงตลาดบางเขน เขตหลักสี่ กรุงเทพฯ 10210", "tel": ""},
    "S6": {"name": "ร้านสมใจการพิมพ์", "print_name": "ร้านสมใจการพิมพ์",
           "legal_form": "sole_proprietor", "core": "สมใจการพิมพ์",
           "tax_id": make_tax_id("310990012345"), "branch": None,
           "address": "56 ซอยงามวงศ์วาน 23 ตำบลบางเขน อำเภอเมืองนนทบุรี จังหวัดนนทบุรี 11000", "tel": "081-234-5678"},
    "S7": {"name": "บริษัท กาแฟดี จำกัด", "print_name": "บริษัท กาแฟดี จำกัด",
           "legal_form": "company_limited", "core": "กาแฟดี",
           "tax_id": make_tax_id("010556004411"), "branch": "00015",
           "address": "สาขาเซ็นทรัลลาดพร้าว กรุงเทพฯ", "tel": ""},
    "S8": {"name": "Cloudnine Software Pte. Ltd.", "print_name": "Cloudnine Software Pte. Ltd.",
           "legal_form": "foreign", "core": "Cloudnine Software",
           "tax_id": None, "branch": None, "foreign_reg": "UEN 201912345K",
           "address": "1 Raffles Place, #20-61, Singapore 048616", "tel": "+65 6123 4567"},
}


def _lines(*rows):
    """rows: (code, description, qty, unit, unit_price[, printed_amount])"""
    out = []
    for r in rows:
        code, desc, qty, unit, price = r[:5]
        qty, price = D(str(qty)), D(str(price))
        true_amount = q2(qty * price)
        printed = q2(D(str(r[5]))) if len(r) > 5 else true_amount
        out.append({"code": code, "description": desc, "quantity": qty, "unit": unit, "unit_price": q2(price),
                    "amount": printed, "true_product": true_amount})
    return out


def _totals(lines, *, discount=D("0"), vat_rate=D("7"), vat_printed=None, vat_mode="exclusive",
            wht_rate=None, total_printed=None, vat_rounding="half_up"):
    s = sum((l["amount"] for l in lines), D("0"))
    if vat_mode == "inclusive":          # retail: prices include VAT
        total = q2(s - discount)
        vat_true = q2(total * vat_rate / (D("100") + vat_rate))
        vat = vat_printed if vat_printed is not None else vat_true
        base = q2(total - vat)
        out = {"sum_lines": q2(s), "discount": q2(discount), "subtotal": base, "vat_rate": vat_rate,
               "vat_amount": q2(vat), "vat_true": vat_true, "total": total, "vat_mode": "inclusive"}
    elif vat_mode == "none":
        total = q2(s - discount)
        out = {"sum_lines": q2(s), "discount": q2(discount), "subtotal": total, "vat_rate": None,
               "vat_amount": None, "vat_true": None, "total": total, "vat_mode": "none"}
    else:
        base = q2(s - discount)
        exact = base * vat_rate / D("100")
        vat_true = q2(exact)
        if vat_rounding == "truncate":
            vat_true_printed = (exact * 100).to_integral_value(rounding="ROUND_DOWN") / 100
        else:
            vat_true_printed = vat_true
        vat = q2(vat_printed) if vat_printed is not None else q2(vat_true_printed)
        total = q2(total_printed) if total_printed is not None else q2(base + vat)
        out = {"sum_lines": q2(s), "discount": q2(discount), "subtotal": base, "vat_rate": vat_rate,
               "vat_amount": vat, "vat_true": vat_true, "total": total, "vat_mode": "exclusive"}
    if wht_rate is not None:
        wht = q2(out["subtotal"] * D(str(wht_rate)) / D("100"))
        out.update(wht_rate=D(str(wht_rate)), wht_amount=wht, net_payable=q2(out["total"] - wht))
    return out


d = dt.date

# ── the documents ───────────────────────────────────────────────────────────
# kind: full (A4 invoice layouts) | pos (80 mm thermal) | receipt | billing | english
# render: digital | scan (dpi, of) | photo (of) | etax_pdf | etax_xml

BASE = {
    "d01_clean_full_tax_invoice": {
        "title": "ใบกำกับภาษี / ใบเสร็จรับเงิน", "title_en": "TAX INVOICE / RECEIPT", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S1", "buyer": ORG,
        "number": "IV6909-0015", "date": d(2026, 9, 15), "date_style": "th_full", "due": d(2026, 10, 15),
        "tax_style": "plain",
        "lines": _lines(("A4-80", "กระดาษถ่ายเอกสาร A4 80 แกรม", 20, "รีม", "125.00"),
                        ("HP107A", "หมึกพิมพ์เลเซอร์ HP 107A", 4, "ตลับ", "1890.00"),
                        ("FD-3B", "แฟ้มเอกสาร 3 นิ้ว สีน้ำเงิน", 30, "เล่ม", "65.00")),
        "notes": "ชำระโดยโอนเข้าบัญชี ธนาคารกรุงไทย เลขที่ 012-3-45678-9",
        "class": "digital", "expect_questions": [],
        "purpose": "clean digital full tax invoice: every field read from the text layer, zero questions",
    },
    "d02_classic_hyphen_taxid": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "classic", "font": "Tahoma", "seller": "S2", "buyer": ORG,
        "number": "INV2569/0912", "date": d(2026, 9, 12), "date_style": "slash_be", "due": d(2026, 10, 12),
        "tax_style": "hyphen",
        "lines": _lines(("SW-M365", "Microsoft 365 Business Standard (รายปี)", 12, "ผู้ใช้", "4590.00"),
                        ("SV-INST", "ค่าติดตั้งและตั้งค่าระบบ", 1, "งาน", "8000.00"),
                        ("HW-SSD1", "SSD 1TB NVMe", 5, "ชิ้น", "2350.00"),
                        ("SV-TRN", "อบรมการใช้งาน", 6, "ชั่วโมง", "1200.00")),
        "discount": "2030.00",
        "class": "digital", "expect_questions": [],
        "purpose": "second clean layout: the full company name (the modern layouts print บจก.), hyphenated tax IDs, DD/MM/BE date, branch 00002, header discount",
    },
    "d03_abbreviated_retail": {
        "title": "ใบกำกับภาษีอย่างย่อ", "title_en": "TAX INV (ABB)", "doc_type": "abbreviated_tax_invoice",
        "layout": "pos", "font": "Tahoma", "seller": "S5", "buyer": None,
        "number": "01234-2609-004512", "date": d(2026, 9, 15), "date_style": "slash_ce", "time": "14:32",
        "tax_style": "plain", "vat_mode": "inclusive",
        "lines": _lines(("", "น้ำดื่ม 600มล. แพ็ค 6", 2, "", "42.00"),
                        ("", "กาแฟกระป๋อง", 3, "", "17.00"),
                        ("", "แซนด์วิชทูน่า", 1, "", "35.00"),
                        ("", "ทิชชู่เปียก", 1, "", "29.00")),
        "pos_id": "E#0123456", "payment": "เงินสด", "cash": "500.00",
        "class": "digital", "expect_questions": [],
        "purpose": "abbreviated tax invoice: no buyer is normal for this type (not a failure); VAT included in prices",
    },
    "d04_receipt_no_vat_no_lines": {
        "title": "ใบเสร็จรับเงิน", "title_en": "RECEIPT", "doc_type": "receipt",
        "layout": "receipt", "font": "Sarabun", "seller": "S6", "buyer": ORG,
        "number": "045/2569", "date": d(2026, 9, 20), "date_style": "th_abbr",
        "tax_style": "plain", "vat_mode": "none",
        "lines": _lines(("", "ค่าพิมพ์นามบัตร 5 กล่อง", 1, "", "1250.00")),
        "no_line_table": True,
        "class": "digital", "expect_questions": [],
        "purpose": "receipt only: no VAT line, no line-item table (one amount in the text), seller not VAT-registered",
    },
    "d05_wht_service_invoice": {
        "title": "ใบแจ้งหนี้ / ใบกำกับภาษี", "title_en": "INVOICE / TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "service", "font": "Angsana New", "seller": "S3", "buyer": ORG,
        "number": "CS-2569-0187", "date": d(2026, 9, 18), "date_style": "th_abbr", "due": d(2026, 10, 18),
        "tax_style": "space",
        "lines": _lines(("", "ค่าบริการซ่อมแซมโครงหลังคาเหล็ก อาคาร B", 1, "งาน", "45000.00"),
                        ("", "ค่าบริการตรวจสอบโครงสร้างหลังซ่อม", 1, "งาน", "5000.00")),
        "wht_rate": "3",
        "class": "digital", "expect_questions": [],
        "purpose": "service invoice with withholding tax 3 % and net payable; หจก.; tax IDs with spaces",
    },
    "d06_thai_digits_bmj": {
        "title": "ใบกำกับภาษี / ใบส่งของ", "title_en": "", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S4", "buyer": ORG,
        "number": "ขส.๖๙/๐๓๓๔", "number_value": "ขส.69/0334", "date": d(2026, 9, 12), "date_style": "th_digits_abbr_yy",
        "due": None, "tax_style": "thai", "thai_digits": True,
        "lines": _lines(("", "ค่าขนส่งสินค้า กรุงเทพฯ-ชลบุรี", 3, "เที่ยว", "4500.00"),
                        ("", "ค่าบริการขนถ่ายสินค้า", 1, "งาน", "1500.00")),
        "class": "digital", "expect_questions": [],
        "purpose": "Thai digits for every number and date, abbreviated month with a 2-digit BE year, บมจ.",
    },
    "d07_multipage": {
        "title": "ใบกำกับภาษี / ใบเสร็จรับเงิน", "title_en": "TAX INVOICE / RECEIPT", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S1", "buyer": ORG,
        "number": "IV6909-0021", "date": d(2026, 9, 22), "date_style": "th_full", "due": d(2026, 10, 22),
        "tax_style": "plain", "pages": 2, "rows_page1": 16,
        "lines": _lines(
            ("PN-01", "ปากกาลูกลื่น 0.5 มม. สีน้ำเงิน", 50, "ด้าม", "8.50"),
            ("PN-02", "ปากกาลูกลื่น 0.5 มม. สีแดง", 20, "ด้าม", "8.50"),
            ("PC-2B", "ดินสอ 2B", 48, "แท่ง", "6.00"),
            ("ER-01", "ยางลบดินสอ", 24, "ก้อน", "5.00"),
            ("RL-30", "ไม้บรรทัดเหล็ก 30 ซม.", 10, "อัน", "35.00"),
            ("ST-01", "ลวดเย็บกระดาษ เบอร์ 10", 40, "กล่อง", "12.00"),
            ("ST-02", "เครื่องเย็บกระดาษ", 6, "เครื่อง", "145.00"),
            ("CL-01", "คลิปหนีบกระดาษ 2 ขา", 30, "กล่อง", "18.00"),
            ("PT-01", "เทปใส 1 นิ้ว", 24, "ม้วน", "22.00"),
            ("PT-02", "เทปกาวสองหน้า", 12, "ม้วน", "38.00"),
            ("NB-A5", "สมุดบันทึก A5", 36, "เล่ม", "45.00"),
            ("PS-01", "โพสต์อิท 3x3 นิ้ว", 30, "แพ็ค", "55.00"),
            ("HL-01", "ปากกาเน้นข้อความ", 24, "ด้าม", "25.00"),
            ("MK-01", "ปากกาเคมี 2 หัว", 24, "ด้าม", "28.00"),
            ("WB-01", "ปากกาไวท์บอร์ด", 24, "ด้าม", "32.00"),
            ("EN-A4", "ซองจดหมาย A4 สีน้ำตาล", 200, "ซอง", "3.50"),
            ("EN-DL", "ซองจดหมาย DL สีขาว", 300, "ซอง", "1.80"),
            ("FL-01", "แฟ้มสันกว้าง 2 นิ้ว", 20, "เล่ม", "59.00"),
            ("FL-02", "แฟ้มซองพลาสติก", 50, "ซอง", "9.00"),
            ("CT-01", "คัตเตอร์ใหญ่", 10, "อัน", "39.00"),
            ("SC-01", "กรรไกร 8 นิ้ว", 10, "อัน", "49.00"),
            ("GL-01", "กาวแท่ง", 24, "แท่ง", "19.00"),
            ("CR-01", "ลิควิดเปเปอร์", 24, "ด้าม", "35.00"),
            ("CA-01", "เครื่องคิดเลข 12 หลัก", 5, "เครื่อง", "320.00"),
            ("BT-AA", "ถ่าน AA แพ็ค 4", 20, "แพ็ค", "89.00"),
            ("LB-01", "สติ๊กเกอร์ลาเบล A4", 10, "แพ็ค", "150.00"),
            ("TR-01", "ถาดเอกสาร 3 ชั้น", 6, "ชุด", "260.00"),
            ("PB-01", "ที่เจาะกระดาษ", 4, "อัน", "185.00")),
        "class": "digital", "expect_questions": [],
        "purpose": "two pages, the item table continues on page 2 and the totals are only on page 2",
    },
    "d08_vat_rounding_edge": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "classic", "font": "Tahoma", "seller": "S2", "buyer": ORG,
        "number": "INV2569/0931", "date": d(2026, 9, 24), "date_style": "slash_be", "due": d(2026, 10, 24),
        "tax_style": "hyphen", "vat_rounding": "truncate",
        "lines": _lines(("SV-SUP", "ค่าบริการดูแลระบบรายชั่วโมง", 3, "ชั่วโมง", "411.50")),
        "class": "digital", "expect_questions": [],
        "purpose": "VAT rounding edge: 7 % of 1,234.50 is 86.415; the software truncated to 86.41 — within the 0.01 tolerance, not a failure",
    },
    "d09_billing_note_not_tax_invoice": {
        "title": "ใบแจ้งหนี้", "title_en": "INVOICE", "doc_type": "invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S3", "buyer": ORG,
        "number": "BN-2569-0045", "date": d(2026, 9, 10), "date_style": "th_full", "due": d(2026, 10, 10),
        "tax_style": "plain",
        "lines": _lines(("", "ค่ามัดจำงานติดตั้งโครงเหล็ก งวดที่ 1", 1, "งวด", "20000.00")),
        "class": "digital", "expect_questions": [],
        "purpose": "an invoice/bill that is not a tax invoice: document type drives the rules (no input-VAT claim, no failure)",
    },
    "e01_etax_pdf_embedded_xml": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE (e-Tax Invoice)", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S2", "buyer": ORG,
        "number": "ET2569090042", "date": d(2026, 9, 19), "date_style": "th_full", "due": d(2026, 10, 19),
        "tax_style": "plain", "etax": True, "etax_type": "388",
        "lines": _lines(("CL-VM", "บริการคลาวด์เซิร์ฟเวอร์ รายเดือน ก.ย. 2569", 1, "เดือน", "18500.00"),
                        ("CL-BK", "บริการสำรองข้อมูล 500GB", 1, "เดือน", "1500.00")),
        "class": "etax_pdf", "expect_questions": [],
        "purpose": "e-Tax Invoice PDF carrying the ETDA XML as an embedded file: the XML is the source, the text layer confirms it",
    },
    "e02_etax_xml_receipt": {
        "title": "ใบเสร็จรับเงิน/ใบกำกับภาษี", "title_en": "", "doc_type": "full_tax_invoice",
        "layout": "xml", "seller": "S4", "buyer": ORG,
        "number": "ER2569-000871", "date": d(2026, 9, 21), "date_style": "iso",
        "tax_style": "plain", "etax_type": "T03",
        "lines": _lines(("TR-BKK", "ค่าขนส่งสินค้า กรุงเทพฯ-ระยอง", 2, "เที่ยว", "5200.00")),
        "class": "etax_xml", "expect_questions": [],
        "purpose": "e-Receipt/e-Tax Invoice delivered as the ETDA XML file alone",
    },
    "f01_line_total_mismatch": {
        "title": "ใบกำกับภาษี / ใบเสร็จรับเงิน", "title_en": "TAX INVOICE / RECEIPT", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S1", "buyer": ORG,
        "number": "IV6909-0018", "date": d(2026, 9, 17), "date_style": "th_full", "due": d(2026, 10, 17),
        "tax_style": "plain",
        "lines": _lines(("CH-E2", "เก้าอี้สำนักงาน รุ่น ERGO-2", 2, "ตัว", "3200.00"),
                        ("FR-01", "ที่วางเท้าปรับระดับ", 3, "ชิ้น", "450.00", "1530.00"),
                        ("LP-01", "โคมไฟตั้งโต๊ะ LED", 2, "ชิ้น", "890.00")),
        "class": "digital", "expect_questions": ["check_failed:line_arithmetic"],
        "purpose": "line 2 prints 1,530.00 for 3 × 450.00 (1,350.00): the line check fails and asks, never picks a number",
    },
    "f02_vat_mismatch": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "classic", "font": "Tahoma", "seller": "S2", "buyer": ORG,
        "number": "INV2569/0940", "date": d(2026, 9, 25), "date_style": "slash_be", "due": d(2026, 10, 25),
        "tax_style": "hyphen", "vat_printed": "750.00",
        "lines": _lines(("SV-DEV", "ค่าพัฒนาระบบรายงาน", 1, "งาน", "4000.00"),
                        ("SV-DSN", "ค่าออกแบบหน้าจอ", 1, "งาน", "6000.00")),
        "class": "digital", "expect_questions": ["check_failed:vat_7_percent"],
        "purpose": "VAT printed 750.00 on a 10,000.00 base (7 % is 700.00): the VAT check fails and asks",
    },
    "f03_duplicate_of_d01": {
        "copy_of": "d01_clean_full_tax_invoice", "copy_mark": "สำเนา", "class": "digital", "history": ["d01_clean_full_tax_invoice"],
        "expect_questions": ["duplicate_candidate"], "allowed_extra": ["copy_not_original"], "duplicate_of": "d01_clean_full_tax_invoice",
        "purpose": "the same invoice as d01 sent again as a copy (different file bytes, same supplier tax ID + number + date + total)",
    },
    "f04_wrong_buyer": {
        "title": "ใบกำกับภาษี / ใบเสร็จรับเงิน", "title_en": "TAX INVOICE / RECEIPT", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S1", "buyer": OTHER_BUYER,
        "number": "IV6909-0019", "date": d(2026, 9, 17), "date_style": "th_full", "due": d(2026, 10, 17),
        "tax_style": "plain",
        "lines": _lines(("TN-01", "ผงหมึกเครื่องถ่ายเอกสาร", 2, "กล่อง", "2450.00")),
        "class": "digital", "expect_questions": ["check_failed:buyer_match"],
        "purpose": "a full tax invoice made out to another company's tax ID: input VAT cannot be claimed, ask",
    },
    "f05_missing_seller_tax_id": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "modern", "font": "Sarabun", "seller": "S2", "buyer": ORG, "omit_seller_tax_id": True,
        "number": "AC-0923", "date": d(2026, 9, 23), "date_style": "th_full", "due": d(2026, 10, 23),
        "tax_style": "plain",
        "lines": _lines(("SV-NET", "ค่าบริการเครือข่ายรายเดือน", 1, "เดือน", "3500.00")),
        "class": "digital", "expect_questions": ["field_missing:seller_tax_id"],
        "purpose": "a full tax invoice with no seller tax ID: a required field for this document type is missing",
    },
    "f06_invalid_tax_id_checksum": {
        "title": "ใบกำกับภาษี", "title_en": "TAX INVOICE", "doc_type": "full_tax_invoice",
        "layout": "classic", "font": "Tahoma", "seller": "S4", "buyer": ORG, "corrupt_seller_tax_id": True,
        "number": "TS-6909-112", "date": d(2026, 9, 16), "date_style": "slash_be", "due": d(2026, 10, 16),
        "tax_style": "hyphen",
        "lines": _lines(("TR-01", "ค่าขนส่งพัสดุ", 10, "เที่ยว", "980.00")),
        "class": "digital", "expect_questions": ["check_failed:seller_tax_id_format"],
        "purpose": "a 13-digit seller tax ID whose check digit is wrong: the format check fails and asks",
    },
    "f07_foreign_currency_usd": {
        "title": "INVOICE", "title_en": "INVOICE", "doc_type": "invoice",
        "layout": "english", "font": "Arial", "seller": "S8", "buyer": ORG, "currency": "USD",
        "number": "CN-2026-00917", "date": d(2026, 9, 1), "date_style": "en", "due": d(2026, 10, 1),
        "tax_style": "plain", "vat_mode": "none",
        "lines": _lines(("", "Cloudnine Pro subscription, 20 seats, Sep 2026", 20, "", "60.00")),
        "class": "digital", "expect_questions": ["check_failed:currency"], "allowed_extra": ["pp36_self_assessment", "wht_section_70"],
        "purpose": "a USD invoice from a foreign supplier: the THB amount needs an exchange-rate source, ask",
    },
    "p01_photo_crumpled_thermal": {
        "title": "ใบกำกับภาษีอย่างย่อ", "title_en": "TAX INV (ABB)", "doc_type": "abbreviated_tax_invoice",
        "layout": "pos", "font": "Tahoma", "seller": "S7", "buyer": None,
        "number": "00015-2609-000731", "date": d(2026, 9, 21), "date_style": "slash_ce", "time": "08:14",
        "tax_style": "plain", "vat_mode": "inclusive",
        "lines": _lines(("", "ลาเต้เย็น", 2, "", "65.00"), ("", "ครัวซองต์เนยสด", 1, "", "55.00")),
        "pos_id": "E#0998812", "payment": "QR พร้อมเพย์",
        "class": "photo", "photo": {"crumple": 1.0, "angle": -5.5, "light": 0.55, "width": 1100, "blur": 1.3, "jpeg": 62},
        "expect_questions": "may", "triage": True,
        "purpose": "phone photo of a crumpled thermal receipt: triage only (vendor/date/total/VAT/tax ID); low confidence becomes a question, never a number",
    },
    "p02_photo_flat_receipt": {
        "title": "ใบกำกับภาษีอย่างย่อ", "title_en": "TAX INV (ABB)", "doc_type": "abbreviated_tax_invoice",
        "layout": "pos", "font": "Tahoma", "seller": "S5", "buyer": None,
        "number": "01234-2609-004987", "date": d(2026, 9, 23), "date_style": "slash_ce", "time": "19:05",
        "tax_style": "plain", "vat_mode": "inclusive",
        "lines": _lines(("", "นมจืด 1 ลิตร", 2, "", "47.00"), ("", "ขนมปังโฮลวีต", 1, "", "42.00"),
                        ("", "ไข่ไก่ 10 ฟอง", 1, "", "59.00")),
        "pos_id": "E#0123456", "payment": "บัตรเครดิต",
        "class": "photo", "photo": {"crumple": 0.15, "angle": 2.0, "light": 0.2, "width": 1500, "blur": 0.5, "jpeg": 85},
        "expect_questions": "may", "triage": True,
        "purpose": "a flat, well-lit phone photo of a receipt: still triage only, questions allowed but numbers must be right or asked",
    },
    "s01_scan300_full_tax_invoice": {"scan_of": "d01_clean_full_tax_invoice", "dpi": 300, "class": "scan300",
                                     "scan": {"angle": 0.35, "noise": 6, "blur": 0.4, "jpeg": 82, "seed": 11},
                                     "expect_questions": "may",
                                     "purpose": "office-quality 300 DPI scan of d01: OCR path"},
    "s02_scan200_full_tax_invoice": {"scan_of": "d01_clean_full_tax_invoice", "dpi": 200, "class": "scan200",
                                     "scan": {"angle": -0.6, "noise": 8, "blur": 0.5, "jpeg": 78, "seed": 12},
                                     "expect_questions": "may",
                                     "purpose": "office-quality 200 DPI scan of d01"},
    "s03_scan300_wht_invoice": {"scan_of": "d05_wht_service_invoice", "dpi": 300, "class": "scan300",
                                "scan": {"angle": 0.8, "noise": 7, "blur": 0.45, "jpeg": 80, "seed": 13},
                                "expect_questions": "may",
                                "purpose": "300 DPI scan of the WHT invoice (Angsana New, spaces in tax IDs)"},
    "s04_scan200_classic": {"scan_of": "d02_classic_hyphen_taxid", "dpi": 200, "class": "scan200",
                            "scan": {"angle": -0.3, "noise": 9, "blur": 0.55, "jpeg": 76, "seed": 14},
                            "expect_questions": "may",
                            "purpose": "200 DPI scan of the classic layout (Tahoma, hyphenated tax IDs, discount)"},
}


def resolved() -> dict[str, dict]:
    """Every spec with its computed totals, copies and scans resolved to their source."""
    out: dict[str, dict] = {}
    for fid, spec in BASE.items():
        src = spec.get("copy_of") or spec.get("scan_of")
        s = deepcopy(BASE[src]) if src else {}
        s.update(deepcopy(spec))
        if src:
            s["source_fixture"] = src
        s["id"] = fid
        s["totals"] = _totals(
            s["lines"], discount=D(s.get("discount", "0")),
            vat_mode=s.get("vat_mode", "exclusive"), wht_rate=s.get("wht_rate"),
            vat_printed=D(s["vat_printed"]) if s.get("vat_printed") else None,
            vat_rounding=s.get("vat_rounding", "half_up"))
        out[fid] = s
    return out
