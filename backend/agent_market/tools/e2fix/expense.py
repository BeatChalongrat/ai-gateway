"""Expense-audit fixtures (AM2 Phase A, input to Phase F).

    python -m agent_market.tools.e2fix.expense [out_dir]   (from backend/)

A structured policy (what an install stores: limits per category, per claim,
per person per month; prohibited merchants; the receipt threshold; the
holiday calendar), the same policy as an owner-uploadable sheet, a KB policy
document whose wording is deliberately vaguer and contradicts one limit (it
is context only — never the source of a limit), one month of claims, the
receipts they point to, and expected.json: for every claim the deterministic
facts (pass / fail per rule) and what becomes an exception (a person
decides) or a clarification card (judgement) — never an automatic rejection.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal as D
from pathlib import Path

from docx import Document
from openpyxl import Workbook

from . import html as H
from . import render
from .specs import SUPPLIERS, _lines, _totals
from .thai import make_tax_id

POLICY = {
    "schema": "am2.expense_policy.v1",
    "currency": "THB",
    "receipt_required_above": "500.00",
    "approval_threshold": "3000.00",
    "categories": {
        "meals": {"label": "ค่าอาหาร", "per_claim": "1500.00", "per_person_month": "5000.00"},
        "taxi": {"label": "ค่าแท็กซี่/เดินทางในเมือง", "per_claim": "800.00"},
        "hotel": {"label": "ค่าที่พัก", "per_claim": "2500.00"},
        "office_supplies": {"label": "วัสดุสำนักงาน", "per_claim": "3000.00"},
        "entertainment": {"label": "ค่ารับรองลูกค้า", "per_claim": "5000.00", "needs_purpose": True},
        "phone": {"label": "ค่าโทรศัพท์มือถือ", "per_person_month": "1000.00"},
        "contractor": {"label": "ค่าจ้างบุคคลภายนอก", "per_claim": "20000.00", "wht_rate": "3", "wht_from": "1000.00"},
    },
    "prohibited_merchants": ["คาราโอเกะ", "ผับ", "บาร์", "คาสิโน", "ร้านทอง"],
    "weekend_holiday": "flag",
    "vat_claim_requires": "full_tax_invoice",
}

HOLIDAYS_2569 = {  # Thai public holidays in the claim month (October 2026 = ตุลาคม 2569)
    "2026-10-13": "วันนวมินทรมหาราช", "2026-10-23": "วันปิยมหาราช",
}

EMPLOYEES = {"E001": "สมชาย ใจดี", "E002": "สุดา รักงาน", "E003": "วิชัย ช่างคิด", "E004": "นภา สดใส"}

d = dt.date
# (id, employee, date, category, merchant, amount, receipt file or None, vat_claim, description, extra)
CLAIMS = [
    ("C01", "E001", d(2026, 10, 1), "meals", "ร้านอาหารครัวไทย", "850.00", "r_c01", False, "ค่าอาหารกลางวันประชุมทีมขาย", {}),
    ("C02", "E001", d(2026, 10, 5), "meals", "ร้านสุกี้ชาบูบ้านเรา", "1980.00", "r_c02", False, "ค่าอาหารเย็นทีมขาย (ทำงานล่วงเวลาปิดยอด)", {}),
    ("C03", "E002", d(2026, 10, 6), "taxi", "แท็กซี่", "320.00", None, False, "ไปส่งเอกสารที่กรมสรรพากร", {}),
    ("C04", "E002", d(2026, 10, 7), "office_supplies", "ร้านเครื่องเขียนสยาม", "2450.00", None, False, "ซื้อกระดาษและแฟ้ม", {}),
    ("C05", "E003", d(2026, 10, 8), "hotel", "โรงแรมเชียงใหม่ริมดอย", "2300.00", "r_c05", False, "ค่าที่พักติดตั้งระบบลูกค้าเชียงใหม่ 1 คืน", {}),
    ("C06", "E003", d(2026, 10, 8), "hotel", "โรงแรมเชียงใหม่ริมดอย", "2300.00", "r_c05", False, "ค่าที่พักเชียงใหม่", {}),
    ("C07", "E004", d(2026, 10, 10), "meals", "ร้านกาแฟบ้านไร่", "600.00", "r_c07", False, "ประชุมออกแบบแคมเปญ", {}),
    ("C08", "E004", d(2026, 10, 13), "taxi", "แท็กซี่", "450.00", None, False, "เดินทางไปงานอีเวนต์", {}),
    ("C09", "E001", d(2026, 10, 14), "entertainment", "ร้านคาราโอเกะมิวสิคบ็อกซ์", "4200.00", "r_c09", False, "รับรองลูกค้า", {}),
    ("C10", "E002", d(2026, 10, 15), "office_supplies", "บริษัท มินิมาร์ท 24 จำกัด", "2500.00", "r_c10", True, "ซื้อของใช้สำนักงาน ขอคืนภาษีซื้อ", {}),
    ("C11", "E003", d(2026, 10, 16), "contractor", "นายสมศักดิ์ ช่างแอร์ (บุคคลธรรมดา)", "5000.00", "r_c11", False, "ค่าจ้างซ่อมแอร์ห้องเซิร์ฟเวอร์", {"wht_deducted": "0.00"}),
    ("C12", "E004", d(2026, 10, 19), "office_supplies", "ร้านไอทีพลาซ่า", "2000.00", "r_c12", False, "เมาส์และคีย์บอร์ด", {}),
    ("C13", "E004", d(2026, 10, 19), "office_supplies", "ร้านไอทีพลาซ่า", "1900.00", "r_c13", False, "จอภาพเสริม", {}),
    ("C14", "E001", d(2026, 10, 20), "meals", "ร้านอาหารบ้านสวน", "1200.00", "r_c14", False, "ค่าอาหาร", {}),
    ("C15", "E002", d(2026, 10, 22), "taxi", "แท็กซี่", "780.00", "r_c15", False, "เดินทางไปพบลูกค้าบางนา", {}),
    ("C16", "E001", d(2026, 10, 27), "meals", "ร้านอาหารครัวไทย", "1400.00", "r_c16", False, "ค่าอาหารประชุมปิดยอด", {}),
    ("C17", "E003", d(2026, 10, 21), "phone", "บริษัท โมบายเน็ต ไทย จำกัด (มหาชน)", "1200.00", "r_c17", False, "ค่าโทรศัพท์เดือนตุลาคม", {}),
    ("C18", "E002", d(2026, 10, 26), "meals", "ร้านข้าวมันไก่ประตูน้ำ", "280.00", None, False, "อาหารกลางวันทำงานนอกสถานที่", {}),
]

# What each claim must produce. facts: rule → pass|fail (only rules that apply are listed);
# outcome: pass | exception (a person decides; the code says why) | question (judgement card).
EXPECTED = {
    "C01": {"facts": {"within_claim_limit": "pass", "receipt": "pass"}, "outcome": "pass"},
    "C02": {"facts": {"within_claim_limit": "fail"}, "outcome": "exception", "codes": ["over_claim_limit"]},
    "C03": {"facts": {"receipt": "pass"}, "outcome": "pass", "note": "320 is under the 500 receipt threshold"},
    "C04": {"facts": {"receipt": "fail"}, "outcome": "exception", "codes": ["missing_receipt"]},
    "C05": {"facts": {"within_claim_limit": "pass", "duplicate": "pass"}, "outcome": "pass"},
    "C06": {"facts": {"duplicate": "fail"}, "outcome": "exception", "codes": ["duplicate_claim"], "duplicate_of": "C05"},
    "C07": {"facts": {"weekend_holiday": "fail"}, "outcome": "question", "codes": ["weekend_or_holiday"],
            "note": "Saturday: a flag for a person, never a rejection"},
    "C08": {"facts": {"weekend_holiday": "fail"}, "outcome": "question", "codes": ["weekend_or_holiday"],
            "note": "13 Oct 2026 is วันนวมินทรมหาราช"},
    "C09": {"facts": {"prohibited_merchant": "fail"}, "outcome": "exception", "codes": ["prohibited_merchant"]},
    "C10": {"facts": {"vat_document": "fail"}, "outcome": "exception", "codes": ["vat_claim_without_full_tax_invoice"],
            "note": "the receipt is an abbreviated tax invoice: no input-VAT claim"},
    "C11": {"facts": {"wht": "fail"}, "outcome": "exception", "codes": ["wht_missing"],
            "note": "5,000 to an individual for services: 3 % WHT (150.00) expected, 0.00 deducted"},
    "C12": {"facts": {"split_purchase": "fail"}, "outcome": "exception", "codes": ["split_purchase"], "group": ["C12", "C13"]},
    "C13": {"facts": {"split_purchase": "fail"}, "outcome": "exception", "codes": ["split_purchase"], "group": ["C12", "C13"]},
    "C14": {"facts": {"within_claim_limit": "pass"}, "outcome": "question", "codes": ["vague_purpose"],
            "note": "'ค่าอาหาร' alone: business purpose is judgement — a card, not a rule"},
    "C15": {"facts": {"within_claim_limit": "pass", "receipt": "pass"}, "outcome": "pass"},
    "C16": {"facts": {"within_period_limit": "fail"}, "outcome": "exception", "codes": ["over_period_limit"],
            "note": "E001 meals in Oct: 850 + 1,980 + 1,200 + 1,400 = 5,430 > 5,000; this is the claim that crosses. "
                    "Period limits count CLAIMED amounts: the audit runs before anyone decides C02, so C02's own "
                    "over-limit exception does not shrink the month"},
    "C17": {"facts": {"within_period_limit": "fail"}, "outcome": "exception", "codes": ["over_period_limit"]},
    "C18": {"facts": {"receipt": "pass"}, "outcome": "pass", "note": "280 is under the receipt threshold"},
}

# every VAT-registered receipt seller has its own valid tax ID (a public company: 0107…)
RECEIPT_TAX_PREFIX = {"r_c05": "010556108812", "r_c09": "010556211190", "r_c17": "010753900418"}
# the individual contractor's receipt carries what a 50 ทวิ certificate needs: ID number and address
RECEIPT_ADDRESS = {"r_c11": "เลขประจำตัวผู้เสียภาษี 3 1009 00456 78 1 · 12/4 ซอยพหลโยธิน 48 เขตบางเขน กรุงเทพฯ 10220"}

RECEIPTS = {  # receipt file → (seller spec, title, doc_type, lines, vat_mode)
    "r_c01": ("ร้านอาหารครัวไทย", [("", "ข้าวผัดกะเพรา", 5, "", "110.00"), ("", "ต้มยำกุ้ง", 1, "", "300.00")], "none"),
    "r_c02": ("ร้านสุกี้ชาบูบ้านเรา", [("", "บุฟเฟ่ต์สุกี้", 4, "", "495.00")], "none"),
    "r_c05": ("โรงแรมเชียงใหม่ริมดอย", [("", "ห้องพักดีลักซ์ 1 คืน", 1, "", "2300.00")], "inclusive"),
    "r_c07": ("ร้านกาแฟบ้านไร่", [("", "กาแฟ", 6, "", "100.00")], "none"),
    "r_c09": ("ร้านคาราโอเกะมิวสิคบ็อกซ์", [("", "ห้อง VIP 3 ชั่วโมง", 1, "", "2400.00"), ("", "เครื่องดื่ม", 1, "", "1800.00")], "inclusive"),
    "r_c10": ("S5", [("", "กระดาษทิชชู่ ลัง", 5, "", "300.00"), ("", "น้ำยาทำความสะอาด", 10, "", "100.00")], "inclusive"),
    "r_c11": ("นายสมศักดิ์ ช่างแอร์", [("", "ค่าแรงซ่อมแอร์", 1, "", "5000.00")], "none"),
    "r_c12": ("ร้านไอทีพลาซ่า", [("", "เมาส์ไร้สาย", 4, "", "250.00"), ("", "คีย์บอร์ด", 4, "", "250.00")], "none"),
    "r_c13": ("ร้านไอทีพลาซ่า", [("", "จอภาพ 22 นิ้ว (มือสอง)", 1, "", "1900.00")], "none"),
    "r_c14": ("ร้านอาหารบ้านสวน", [("", "อาหารชุด", 4, "", "300.00")], "none"),
    "r_c15": ("แท็กซี่", [("", "ค่าโดยสาร", 1, "", "780.00")], "none"),
    "r_c16": ("ร้านอาหารครัวไทย", [("", "อาหารชุดประชุม", 7, "", "200.00")], "none"),
    "r_c17": ("บริษัท โมบายเน็ต ไทย จำกัด (มหาชน)", [("", "ค่าบริการรายเดือน", 1, "", "1200.00")], "inclusive"),
}


def _receipt_spec(name, rid, seller_name, lines, vat_mode, date):
    if seller_name == "S5":
        seller = SUPPLIERS["S5"]
        title, title_en, doc_type = "ใบกำกับภาษีอย่างย่อ", "TAX INV (ABB)", "abbreviated_tax_invoice"
    else:
        seller = {"name": seller_name, "print_name": seller_name, "legal_form": "other", "core": seller_name,
                  "tax_id": None if vat_mode == "none" else make_tax_id(RECEIPT_TAX_PREFIX.get(rid, "010553600099")),
                  "branch": None, "address": RECEIPT_ADDRESS.get(rid, ""), "tel": ""}
        title, title_en, doc_type = ("ใบเสร็จรับเงิน", "RECEIPT", "receipt") if vat_mode == "none" else \
            ("ใบกำกับภาษีอย่างย่อ", "TAX INV (ABB)", "abbreviated_tax_invoice")
    ls = _lines(*lines)
    spec = {"id": rid, "title": title, "title_en": title_en, "doc_type": doc_type, "layout": "pos", "seller": None,
            "number": f"{rid.upper()}-2569", "date": date, "date_style": "slash_be", "time": "12:00",
            "tax_style": "plain", "vat_mode": vat_mode, "lines": ls, "payment": "เงินสด",
            "totals": _totals(ls, vat_mode=vat_mode)}
    return spec, seller


def _pos_no_taxid(spec, seller) -> str:
    """The POS layout, for shops that print no tax ID."""
    page = H.pos(spec, {**seller, "tax_id": seller["tax_id"] or ""}, None)
    return page if seller["tax_id"] else page.replace("<div class=\"c\">TAX ID : </div>", "")


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "expense"
    (out / "receipts").mkdir(parents=True, exist_ok=True)
    (out / "html").mkdir(parents=True, exist_ok=True)
    (out / "policy.json").write_text(json.dumps(POLICY, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    (out / "holidays.json").write_text(json.dumps(HOLIDAYS_2569, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    # the policy as a sheet an owner would upload at install
    wb = Workbook()
    ws = wb.active
    ws.title = "นโยบาย"
    ws.append(["หมวด", "รหัส", "วงเงินต่อครั้ง", "วงเงินต่อคนต่อเดือน", "หมายเหตุ"])
    for code, c in POLICY["categories"].items():
        ws.append([c["label"], code, float(c["per_claim"]) if c.get("per_claim") else None,
                   float(c["per_person_month"]) if c.get("per_person_month") else None,
                   "ต้องระบุวัตถุประสงค์" if c.get("needs_purpose") else ("หัก ณ ที่จ่าย 3%" if c.get("wht_rate") else "")])
    ws2 = wb.create_sheet("ร้านต้องห้าม")
    for m in POLICY["prohibited_merchants"]:
        ws2.append([m])
    ws3 = wb.create_sheet("วันหยุด")
    for k, v in HOLIDAYS_2569.items():
        ws3.append([k, v])
    wb.save(out / "policy.xlsx")
    # the KB policy document: vaguer, and it contradicts the meals limit on purpose
    doc = Document()
    doc.add_heading("ระเบียบการเบิกค่าใช้จ่าย (ฉบับปรับปรุง 2568)", 1)
    doc.add_paragraph("ค่าอาหารในการปฏิบัติงานเบิกได้ตามความเหมาะสม โดยทั่วไปไม่ควรเกินประมาณ 2,000 บาทต่อครั้ง "
                      "ทั้งนี้ขึ้นอยู่กับดุลยพินิจของผู้บังคับบัญชา")
    doc.add_paragraph("การเลี้ยงรับรองลูกค้าต้องระบุชื่อลูกค้าและวัตถุประสงค์ทางธุรกิจ และควรหลีกเลี่ยงสถานบันเทิง")
    doc.add_paragraph("การเบิกในวันหยุดให้แนบเหตุผลประกอบ")
    doc.save(out / "policy_kb.docx")
    # claims sheet
    wb = Workbook()
    ws = wb.active
    ws.title = "เบิกค่าใช้จ่าย ต.ค. 69"
    ws.append(["เลขที่เบิก", "รหัสพนักงาน", "ชื่อพนักงาน", "วันที่", "หมวด", "ร้านค้า/ผู้รับเงิน", "จำนวนเงิน",
               "ใบเสร็จ", "ขอคืนภาษีซื้อ", "หัก ณ ที่จ่าย", "รายละเอียด"])
    for cid, emp, date, cat, merchant, amount, rec, vat, desc, extra in CLAIMS:
        ws.append([cid, emp, EMPLOYEES[emp], f"{date.day:02d}/{date.month:02d}/{date.year + 543}",
                   POLICY["categories"][cat]["label"], merchant, float(amount),
                   f"{rec}.pdf" if rec else "", "ใช่" if vat else "", float(extra["wht_deducted"]) if "wht_deducted" in extra else None, desc])
    wb.save(out / "claims_2569-10.xlsx")
    # receipts (POS layout), one file per receipt name
    dates = {c[6]: c[2] for c in CLAIMS if c[6]}
    for rid, (seller_name, lines, vat_mode) in RECEIPTS.items():
        spec, seller = _receipt_spec(rid, rid, seller_name, lines, vat_mode, dates[rid])
        hp = out / "html" / f"{rid}.html"
        hp.write_text(_pos_no_taxid(spec, seller), encoding="utf-8", newline="\n")
        render.chrome_pdf(hp, out / "receipts" / f"{rid}.pdf")
    render.cleanup_profile(out / "receipts")
    # amounts on the receipts equal the claimed amounts (the test checks it)
    claims = [{"id": c[0], "employee": c[1], "date": c[2].isoformat(), "category": c[3], "merchant": c[4], "amount": c[5],
               "receipt": f"receipts/{c[6]}.pdf" if c[6] else None, "vat_claim": c[7], "description": c[8], **c[9]}
              for c in CLAIMS]
    receipt_totals = {rid: str(_totals(_lines(*lines), vat_mode=vm)["total"]) for rid, (_, lines, vm) in RECEIPTS.items()}
    (out / "expected.json").write_text(json.dumps({
        "schema": "am2.expense_expected.v1", "period": "2026-10", "employees": EMPLOYEES, "claims": claims,
        "receipt_totals": receipt_totals, "expected": EXPECTED,
        "kb_policy_rule": "policy_kb.docx says meals 'ประมาณ 2,000'; the structured table says 1,500: C02 (1,980) must be an "
                          "over_claim_limit exception under 1,500, and the KB text may only be quoted as context",
        "never": ["automatic_rejection", "limit_from_kb_document"],
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(CLAIMS)} claims, {len(RECEIPTS)} receipts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
