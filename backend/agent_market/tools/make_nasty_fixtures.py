"""AM1b — GL exports as messy as the real ones.

    python -m agent_market.tools.make_nasty_fixtures [out_dir]

Writes into packages/financial_report/evals/nasty/ (default):

  GL_Express_2569-09.csv      Windows-874 (TIS-620) CSV, Thai headers over two
                              rows, title/print/page lines, a page header
                              repeated every 350 rows, blank rows, seven date
                              formats in one column (Buddhist era, two-digit
                              year, Thai month names), one unbalanced journal,
                              one document posted twice, 14 rows of the next
                              month and a trailing รวม row. ~1,250 rows.
  GL_Express_2569-08.xlsx     the prior month for variance: merged two-row
                              header, real Excel dates mixed with BE text.
  งบประมาณ_2569.xlsx          the matching monthly budget.
  SAP_FBL3N_09-2026_EN.xlsx   another ERP: English headers, US dates
                              (MM/DD/YYYY), amounts with an S/H indicator and
                              a 6-digit chart (400000 revenue, 5xxxxx cost,
                              6xxxxx expenses) that is not the Thai standard.
  GL_clean_2569-09.xlsx       the same September done right: must ask nothing.
  expected.json               the numbers each file should give, from the
                              in-memory journal (never from the parser).

Seeded: the same command writes byte-identical files.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import random
import sys
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from .make_fixtures import Book, Line, _save, q, split_amount

D = Decimal
COMPANY = "บริษัท ตัวอย่างเทรดดิ้ง จำกัด (ข้อมูลทดสอบ AM1b)"
VAT = D("0.07")

COA = {
    "1111-01": "เงินสดย่อย",
    "1112-01": "เงินฝากธนาคารกสิกรไทย - กระแสรายวัน",
    "1112-02": "เงินฝากธนาคารไทยพาณิชย์ - ออมทรัพย์",
    "1131-01": "ลูกหนี้การค้า",
    "1141-01": "สินค้าสำเร็จรูป",
    "1154-00": "ภาษีซื้อ",
    "1251-01": "ค่าเสื่อมราคาสะสม - อุปกรณ์สำนักงาน",
    "2111-01": "เจ้าหนี้การค้า",
    "2154-00": "ภาษีขาย",
    "2155-01": "ภาษีหัก ณ ที่จ่ายค้างจ่าย ภ.ง.ด.1",
    "2155-02": "ภาษีหัก ณ ที่จ่ายค้างจ่าย ภ.ง.ด.53",
    "2156-00": "เงินประกันสังคมค้างจ่าย",
    "2161-01": "ค่าใช้จ่ายค้างจ่าย",
    "4100-01": "รายได้จากการขายสินค้า",
    "4100-02": "รายได้ค่าบริการติดตั้ง",
    "4200-01": "ดอกเบี้ยรับ",
    "5100-01": "ต้นทุนขายสินค้า",
    "5100-02": "ต้นทุนบริการติดตั้ง",
    "5200-01": "เงินเดือนและค่าจ้าง",
    "5200-02": "ค่าล่วงเวลา",
    "5200-03": "เงินสมทบประกันสังคม",
    "5200-04": "สวัสดิการพนักงาน",
    "5300-01": "ค่าที่ปรึกษาและค่าวิชาชีพ",
    "5400-01": "ค่าเช่าสำนักงาน",
    "5400-02": "ค่าไฟฟ้า",
    "5400-03": "ค่าน้ำประปา",
    "5400-04": "ค่าโทรศัพท์และอินเทอร์เน็ต",
    "5400-05": "ค่าวัสดุสิ้นเปลือง",
    "5500-01": "ค่าโฆษณาและส่งเสริมการขาย",
    "5500-02": "ค่าขนส่งสินค้า",
    "5600-01": "ค่าเสื่อมราคา",
    "5700-01": "ค่าเดินทาง",
    "5700-02": "ค่ารับรอง",
    "5700-03": "ค่าธรรมเนียมธนาคาร",
    "5800-01": "ดอกเบี้ยจ่าย",
}
CCS = ["HQ01", "SL01", "OP01", "IT01"]
CUSTOMERS = ["บจก. ค้าปลีกรุ่งเรือง", "หจก. พรเจริญพาณิชย์", "บจก. โมเดิร์นเทรด", "บจก. อินโนเวชั่นซัพพลาย",
             "ร้านทองดีการค้า", "บจก. สตาร์ดิสทริบิวชั่น", "บจก. เมืองทองมาร์ท", "บจก. บางนาโลจิสติกส์",
             "บจก. เชียงใหม่ซัพพลาย", "บจก. ภูเก็ตรีเทล"]
VENDORS = {
    "5300-01": "บจก. ที่ปรึกษาธุรกิจสยาม", "5400-01": "บจก. อาคารสาทรทาวเวอร์", "5400-02": "การไฟฟ้านครหลวง",
    "5400-03": "การประปานครหลวง", "5400-04": "บมจ. ทรู คอร์ปอเรชั่น", "5400-05": "บจก. ออฟฟิศเมท",
    "5500-01": "บจก. มีเดียพลัส", "5500-02": "บจก. ขนส่งด่วนไทย", "5700-01": "บจก. ทราเวลโปร",
    "5700-02": "ร้านอาหารครัวไทย", "5200-04": "บจก. เฮลท์แคร์พลัส",
}
# (month) sales goods, sales install, cogs ratio, expense scale
PROFILE = {"2026-08": (11_240_000, 1_380_000, D("0.54"), D("1.00")),
           "2026-09": (12_050_000, 1_512_000, D("0.55"), D("1.08"))}


def workdays(y: int, m: int) -> list[dt.date]:
    d, out = dt.date(y, m, 1), []
    while d.month == m:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def gen_month(book: Book, period: str, rnd: random.Random) -> None:
    """~1,200 balanced lines: invoices with VAT, costs, receipts, purchases
    with input VAT and withholding tax, payroll by cost centre, depreciation,
    bank charges and interest."""
    y, m = (int(x) for x in period.split("-"))
    days = workdays(y, m)
    goods, install, cogs_ratio, scale = PROFILE[period]
    # Sales invoices: 190 goods + 30 installation, VAT 7 %.
    for i, amt in enumerate(split_amount(D(goods), 190, rnd) + split_amount(D(install), 30, rnd)):
        d = days[i % len(days)]
        acct = "4100-01" if i < 190 else "4100-02"
        vat = q(amt * VAT)
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        book.post(d, "IV", f"ขายเชื่อ {cust}", [("1131-01", amt + vat, 0), (acct, 0, amt), ("2154-00", 0, vat)],
                  cc="SL01" if acct == "4100-01" else "OP01")
    # Cost of sales, one entry per working day (+ installation cost).
    for i, amt in enumerate(split_amount(q(D(goods) * cogs_ratio), len(days), rnd)):
        book.post(days[i], "JV", "บันทึกต้นทุนขายประจำวัน", [("5100-01", amt, 0), ("1141-01", 0, amt)], cc="OP01")
    for i, amt in enumerate(split_amount(q(D(install) * D("0.40")), 12, rnd)):
        book.post(days[(i * 2) % len(days)], "JV", "ต้นทุนบริการติดตั้ง", [("5100-02", amt, 0), ("2161-01", 0, amt)], cc="OP01")
    # Receipts from customers.
    for i, amt in enumerate(split_amount(q(D(goods + install) * D("0.95")), 130, rnd)):
        bank = "1112-01" if i % 3 else "1112-02"
        book.post(days[(i * 7) % len(days)], "RV", f"รับชำระหนี้ {CUSTOMERS[i % len(CUSTOMERS)]}",
                  [(bank, amt, 0), ("1131-01", 0, amt)])
    # Purchases / services with input VAT; services also withhold 3 %.
    budget = {"5300-01": 420_000, "5400-01": 380_000, "5400-02": 96_000, "5400-03": 14_000, "5400-04": 38_000,
              "5400-05": 52_000, "5500-01": 460_000, "5500-02": 290_000, "5700-01": 71_000, "5700-02": 44_000,
              "5200-04": 58_000}
    n_docs = {"5300-01": 6, "5400-01": 1, "5400-02": 4, "5400-03": 1, "5400-04": 4, "5400-05": 14,
              "5500-01": 12, "5500-02": 26, "5700-01": 12, "5700-02": 9, "5200-04": 5}
    for acct, total in budget.items():
        for j, amt in enumerate(split_amount(q(D(total) * scale), n_docs[acct], rnd)):
            d = days[(j * 3 + len(acct)) % len(days)]
            vat = q(amt * VAT)
            service = acct in ("5300-01", "5500-02", "5500-01", "5400-01")
            wht = q(amt * D("0.03")) if service else D(0)
            entries = [(acct, amt, 0), ("1154-00", vat, 0)]
            if wht:
                entries += [("2155-02", 0, wht), ("2111-01", 0, amt + vat - wht)]
            else:
                entries += [("2111-01", 0, amt + vat)]
            book.post(d, "PV", f"{COA[acct]} - {VENDORS[acct]}", entries, cc=CCS[j % len(CCS)])
    # Payroll at month end, by cost centre.
    pay = {"HQ01": 1_180_000, "SL01": 860_000, "OP01": 740_000, "IT01": 520_000}
    last = days[-1]
    entries = []
    for cc, amt in pay.items():
        entries.append(("5200-01", q(D(amt) * scale), 0))
    ot = q(D(96_000) * scale)
    entries.append(("5200-02", ot, 0))
    sso = q(D(750) * 4 * 38)
    entries.append(("5200-03", sso, 0))
    gross = sum((e[1] for e in entries), D(0))
    wht = q((gross - sso) * D("0.045"))
    # employee and employer social security both go to the liability; the
    # employee's share and the withholding tax come out of the net pay
    entries += [("2156-00", 0, sso * 2), ("2155-01", 0, wht), ("1112-01", 0, gross - sso * 2 - wht)]
    book.post(last, "JV", "บันทึกเงินเดือนประจำเดือน", entries, cc="HQ01")
    # Depreciation by cost centre, bank charges, interest.
    dep = [("5600-01", q(D(a)), 0) for a in (38_400, 12_600, 21_900, 17_300)]
    book.post(last, "JV", "ค่าเสื่อมราคาประจำเดือน", dep + [("1251-01", 0, sum((e[1] for e in dep), D(0)))], cc="HQ01")
    for i in range(22):
        fee = q(D(rnd.choice([15, 25, 35, 50, 120])))
        book.post(days[i % len(days)], "BC", "ค่าธรรมเนียมโอนเงิน", [("5700-03", fee, 0), ("1112-01", 0, fee)])
    book.post(last, "JV", "ดอกเบี้ยเงินกู้ยืมธนาคาร", [("5800-01", q(D(42_750)), 0), ("1112-01", 0, q(D(42_750)))])
    book.post(last, "RV", "ดอกเบี้ยรับเงินฝาก", [("1112-02", q(D(3_184.66)), 0), ("4200-01", 0, q(D(3_184.66)))])


def next_month_rows(book: Book, rnd: random.Random) -> list[Line]:
    """Exactly 14 lines dated October: four invoices (12 lines) and one fee (2)."""
    before = len(book.lines)
    for i, amt in enumerate((D("186400.00"), D("92750.50"), D("241300.00"), D("58900.00"))):
        d = dt.date(2026, 10, 1 + (i % 2))
        vat = q(amt * VAT)
        book.post(d, "IV", f"ขายเชื่อ {CUSTOMERS[i]}", [("1131-01", amt + vat, 0), ("4100-01", 0, amt), ("2154-00", 0, vat)], cc="SL01")
    book.post(dt.date(2026, 10, 2), "BC", "ค่าธรรมเนียมโอนเงิน", [("5700-03", D("35.00"), 0), ("1112-01", 0, D("35.00"))])
    return book.lines[before:]


# ── measuring the journal (the independent side of the comparison) ──────────

def pl_totals(lines: list[Line]) -> dict:
    def side(prefixes, credit_natural):
        tot = D(0)
        for l in lines:
            code = l.account.replace("-", "")
            if any(code.startswith(p) for p in prefixes):
                tot += (l.credit - l.debit) if credit_natural else (l.debit - l.credit)
        return q(tot)
    revenue = side(("41",), True)
    other = side(("42",), True)
    cogs = side(("51",), False)
    opex = side(("52", "53", "54", "55", "56", "57"), False)
    fin = side(("58",), False)
    return {"revenue": str(revenue), "other_income": str(other), "cost_of_sales": str(cogs),
            "operating_expenses": str(opex), "finance_costs": str(fin),
            "net_profit": str(q(revenue + other - cogs - opex - fin))}


# ── the writers ─────────────────────────────────────────────────────────────

TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def date_text(d: dt.date, style: int) -> str:
    be = d.year + 543
    return [
        f"{d.day:02d}/{d.month:02d}/{be}",       # 05/09/2569
        f"{d.day}/{d.month}/{be}",               # 5/9/2569
        f"{be}-{d.month:02d}-{d.day:02d}",       # 2569-09-05
        f"{d.day} {TH_MON[d.month - 1]} {be}",   # 5 ก.ย. 2569
        f"{d.day:02d}/{d.month:02d}/{be % 100:02d}",  # 05/09/69
        f"{d.day:02d}-{d.month:02d}-{be}",       # 05-09-2569
        f"{d.day:02d}.{d.month:02d}.{be}",       # 05.09.2569
    ][style % 7]


def money_text(v: Decimal) -> str:
    return "" if v == 0 else f"{v:,.2f}"


def write_nasty_csv(path: str, lines: list[Line]) -> dict:
    """The Express-style export as Thai Windows saves it."""
    header_a = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบายรายการ", "ศูนย์ต้นทุน", "จำนวนเงิน", ""]
    header_b = ["", "", "", "", "", "", "เดบิต", "เครดิต"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([COMPANY] + [""] * 7)
    w.writerow(["สมุดรายวันทั่วไป"] + [""] * 7)
    w.writerow(["ประจำงวด 1 กันยายน 2569 ถึง 30 กันยายน 2569"] + [""] * 7)
    w.writerow(["พิมพ์วันที่ 03/10/2569 เวลา 09:14 น.", "", "", "", "", "", "หน้า 1", ""])
    w.writerow([])
    w.writerow(header_a)
    w.writerow(header_b)
    page, doc_i, prev_doc = 1, 0, None
    tot_d = tot_c = D(0)
    for n, l in enumerate(lines):
        if n and n % 350 == 0:  # a printed page break, the header repeated
            page += 1
            w.writerow([])
            w.writerow(["พิมพ์วันที่ 03/10/2569 เวลา 09:14 น.", "", "", "", "", "", f"หน้า {page}", ""])
            w.writerow(header_a)
            w.writerow(header_b)
        if l.doc != prev_doc:
            doc_i += 1
            prev_doc = l.doc
            if doc_i % 40 == 0:
                w.writerow([""] * 8)  # a blank line between documents
        w.writerow([date_text(l.date, doc_i), l.doc, l.account, COA[l.account], l.desc, l.cc,
                    money_text(l.debit), money_text(l.credit)])
        tot_d += l.debit
        tot_c += l.credit
    w.writerow([])
    w.writerow(["รวม", "", "", "", "", "", f"{tot_d:,.2f}", f"{tot_c:,.2f}"])
    with open(path, "wb") as f:
        f.write(buf.getvalue().encode("cp874"))
    return {"total_debit": str(tot_d), "total_credit": str(tot_c), "pages": page}


def write_merged_xlsx(path: str, lines: list[Line], title_period: str) -> None:
    """Merged two-row header, real Excel dates with every 5th date as BE text."""
    wb = Workbook()
    ws = wb.active
    ws.title = "GL"
    ws["A1"] = COMPANY
    ws.merge_cells("A1:H1")
    ws["A2"] = "รายงานบัญชีแยกประเภท"
    ws.merge_cells("A2:H2")
    ws["A3"] = title_period
    ws.merge_cells("A3:H3")
    for c in ("A1", "A2", "A3"):
        ws[c].alignment = Alignment(horizontal="center")
        ws[c].font = Font(bold=True)
    heads = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบาย", "ศูนย์ต้นทุน"]
    for i, h in enumerate(heads, start=1):
        ws.cell(row=5, column=i, value=h)
        ws.merge_cells(start_row=5, start_column=i, end_row=6, end_column=i)
    ws.cell(row=5, column=7, value="จำนวนเงิน (บาท)")
    ws.merge_cells("G5:H5")
    ws.cell(row=6, column=7, value="เดบิต")
    ws.cell(row=6, column=8, value="เครดิต")
    r = 7
    prev = None
    for n, l in enumerate(lines):
        if l.doc != prev and n and n % 97 == 0:
            r += 1  # blank row
        prev = l.doc
        if n % 5 == 0:
            ws.cell(row=r, column=1, value=f"{l.date.day:02d}/{l.date.month:02d}/{l.date.year + 543}")
        else:
            c = ws.cell(row=r, column=1, value=dt.datetime(l.date.year, l.date.month, l.date.day))
            c.number_format = "[$-th-TH,107]dd/mm/yyyy"
        ws.cell(row=r, column=2, value=l.doc)
        ws.cell(row=r, column=3, value=l.account)
        ws.cell(row=r, column=4, value=COA[l.account])
        ws.cell(row=r, column=5, value=l.desc)
        ws.cell(row=r, column=6, value=l.cc)
        ws.cell(row=r, column=7, value=float(l.debit) if l.debit else None)
        ws.cell(row=r, column=8, value=float(l.credit) if l.credit else None)
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="รวม")
    ws.cell(row=r, column=7, value=float(sum((l.debit for l in lines), D(0))))
    ws.cell(row=r, column=8, value=float(sum((l.credit for l in lines), D(0))))
    _save(wb, path)


def write_clean_xlsx(path: str, lines: list[Line]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "GL"
    ws.append(["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบาย", "ศูนย์ต้นทุน", "เดบิต", "เครดิต"])
    for l in lines:
        ws.append([dt.datetime(l.date.year, l.date.month, l.date.day), l.doc, l.account, COA[l.account], l.desc, l.cc,
                   float(l.debit) if l.debit else 0, float(l.credit) if l.credit else 0])
        ws.cell(row=ws.max_row, column=1).number_format = "dd/mm/yyyy"
    _save(wb, path)


def write_budget(path: str, sep_actual: dict) -> None:
    """Monthly budget 2569 for every P&L account (September close to actual)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append([COMPANY])
    ws.append(["งบประมาณประจำปี 2569"])
    ws.append(["รหัสบัญชี", "ชื่อบัญชี"] + [f"{m} 69" for m in TH_MON] + ["รวมทั้งปี"])
    rnd = random.Random(2569)
    for code, name in COA.items():
        if not code[0] in "45":
            continue
        base = abs(sep_actual.get(code, D(0)))
        vals = [float(q(base * D(str(rnd.uniform(0.9, 1.08))))) for _ in range(12)]
        ws.append([code, name] + vals + [round(sum(vals), 2)])
    _save(wb, path)


SAP_COA = {
    "1112-01": ("110100", "Bank - KBank Current"), "1112-02": ("110200", "Bank - SCB Savings"),
    "1131-01": ("140000", "Trade Receivables"), "1141-01": ("150000", "Finished Goods"),
    "1154-00": ("160000", "Input VAT"), "1251-01": ("179000", "Acc. Depreciation - Office Equip."),
    "2111-01": ("210000", "Trade Payables"), "2154-00": ("215000", "Output VAT"),
    "2155-01": ("216100", "WHT Payable PND1"), "2155-02": ("216200", "WHT Payable PND53"),
    "2156-00": ("217000", "Social Security Payable"), "2161-01": ("219000", "Accrued Expenses"),
    "4100-01": ("400000", "Sales Revenue - Goods"), "4100-02": ("400100", "Service Revenue - Installation"),
    "4200-01": ("480000", "Interest Income"), "5100-01": ("500000", "Cost of Goods Sold"),
    "5100-02": ("500100", "Cost of Installation Services"), "5200-01": ("610000", "Salaries and Wages"),
    "5200-02": ("610100", "Overtime"), "5200-03": ("610200", "Social Security Contribution"),
    "5200-04": ("610300", "Staff Welfare"), "5300-01": ("620000", "Consulting and Professional Fees"),
    "5400-01": ("630000", "Office Rent"), "5400-02": ("630100", "Electricity"), "5400-03": ("630200", "Water"),
    "5400-04": ("630300", "Telephone and Internet"), "5400-05": ("630400", "Office Supplies"),
    "5500-01": ("640000", "Advertising and Promotion"), "5500-02": ("640100", "Freight Out"),
    "5600-01": ("650000", "Depreciation"), "5700-01": ("660000", "Travel"), "5700-02": ("660100", "Entertainment"),
    "5700-03": ("690000", "Bank Charges"), "5800-01": ("700000", "Interest Expense"),
    "1111-01": ("100000", "Petty Cash"),
}


def write_sap_en(path: str, lines: list[Line]) -> None:
    """FBL3N-style line items from a US-locale SAP: MM/DD/YYYY, S/H indicator."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Company Code", "Posting Date", "Document Number", "Account", "Account Name", "Text",
               "Cost Center", "Amount in Local Currency", "Debit/Credit Ind.", "Local Currency"])
    numbers: dict[str, str] = {}
    for l in lines:
        acct, name = SAP_COA[l.account]
        amt = l.debit if l.debit else l.credit
        docno = numbers.setdefault(l.doc, f"{5100000001 + len(numbers)}")
        ws.append(["TH01", f"{l.date.month:02d}/{l.date.day:02d}/{l.date.year}", docno, acct, name,
                   l.desc, l.cc, float(amt), "S" if l.debit else "H", "THB"])
    _save(wb, path)


def main(out: str | None = None) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = out or os.path.join(here, "..", "packages", "financial_report", "evals", "nasty")
    os.makedirs(out, exist_ok=True)
    rnd = random.Random(20260926)

    aug = Book()
    gen_month(aug, "2026-08", rnd)
    sep = Book()
    gen_month(sep, "2026-09", rnd)
    clean_lines = list(sep.lines)

    # The mess, all in September's export.
    nasty = Book(lines=list(sep.lines), seq=dict(sep.seq))
    # 1. an unbalanced journal: the credit is 1,000.00 short
    doc = nasty.doc("PV", dt.date(2026, 9, 18))
    nasty.lines += [Line(dt.date(2026, 9, 18), doc, "5400-05", "ค่าวัสดุสิ้นเปลือง - บจก. ออฟฟิศเมท", D("15000.00"), D(0), "HQ01"),
                    Line(dt.date(2026, 9, 18), doc, "1154-00", "ค่าวัสดุสิ้นเปลือง - บจก. ออฟฟิศเมท", D("1050.00"), D(0), "HQ01"),
                    Line(dt.date(2026, 9, 18), doc, "2111-01", "ค่าวัสดุสิ้นเปลือง - บจก. ออฟฟิศเมท", D(0), D("15050.00"), "HQ01")]
    unbalanced_doc = doc
    unbalanced_lines = nasty.lines[-3:]
    # 2. a document posted twice (an advertising invoice)
    ads = [l for l in sep.lines if l.account == "5500-01"][4]
    dup_lines = [l for l in sep.lines if l.doc == ads.doc]
    nasty.lines += [Line(l.date, l.doc, l.account, l.desc, l.debit, l.credit, l.cc) for l in dup_lines]
    # 3. 14 rows of October
    oct_lines = next_month_rows(nasty, rnd)
    # the export sorts by date, then document
    order = {id(l): i for i, l in enumerate(nasty.lines)}
    nasty_sorted = sorted(nasty.lines, key=lambda l: (l.date, l.doc, order[id(l)]))
    csv_meta = write_nasty_csv(os.path.join(out, "GL_Express_2569-09.csv"), nasty_sorted)
    write_merged_xlsx(os.path.join(out, "GL_Express_2569-08.xlsx"), aug.lines, "ประจำงวด 1 สิงหาคม 2569 ถึง 31 สิงหาคม 2569")
    write_clean_xlsx(os.path.join(out, "GL_clean_2569-09.xlsx"), clean_lines)
    sep_by_acct: dict[str, Decimal] = {}
    for l in clean_lines:
        sep_by_acct[l.account] = sep_by_acct.get(l.account, D(0)) + l.debit - l.credit
    write_budget(os.path.join(out, "งบประมาณ_2569.xlsx"), sep_by_acct)
    write_sap_en(os.path.join(out, "SAP_FBL3N_09-2026_EN.xlsx"), clean_lines)

    expected = {
        "GL_Express_2569-09.csv": {
            "encoding": "cp874", "data_rows": len(nasty_sorted), "rows_in_september": len(nasty_sorted) - len(oct_lines),
            "rows_next_month": len(oct_lines), "unbalanced_doc": unbalanced_doc, "unbalanced_by": "1000.00",
            "duplicate_doc": ads.doc, "duplicate_rows": len(dup_lines), **csv_meta,
            # what a correct close reports: September only, the copy dropped,
            # the unbalanced journal kept as posted (it is reported, not fixed)
            "pl_correct": pl_totals(clean_lines + unbalanced_lines),
            "pl_as_exported": pl_totals([l for l in nasty.lines if l.date.month == 9]),
        },
        "GL_Express_2569-08.xlsx": {"data_rows": len(aug.lines), "pl": pl_totals(aug.lines)},
        "GL_clean_2569-09.xlsx": {"data_rows": len(clean_lines), "pl": pl_totals(clean_lines), "questions": 0},
        "SAP_FBL3N_09-2026_EN.xlsx": {"data_rows": len(clean_lines), "pl": pl_totals(clean_lines),
                                      "days_over_12": sum(1 for l in clean_lines if l.date.day > 12)},
    }
    with open(os.path.join(out, "expected.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(expected, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps({k: v.get("data_rows") for k, v in expected.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
