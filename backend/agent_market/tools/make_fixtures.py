"""Deterministic generator for sample data and the eval set (agent 9).

    python -m agent_market.tools.make_fixtures [out_dir]

Builds a fictional Thai SME ("บริษัท สยามตัวอย่างการค้า จำกัด") whose books
are generated as balanced journal entries (sales with output VAT, purchases
with input VAT, payroll with WHT and social security, rent, consulting,
depreciation, receipts and payments), then written out in the shapes real
Thai exports come in: Express-style Thai GL with title rows and subtotal
lines, Xero-style English GL, SAP line items with an S/H indicator, a D/C
indicator export, a CP874 CSV with signed amounts, a trial balance, a P&L
and a monthly budget — plus deliberately broken variants for each check.

The expected numbers in evals/cases.json come from the in-memory journal,
never from the parser, so the eval compares two independent code paths.
Seeded: the same command always writes byte-identical data.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import random
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from openpyxl import Workbook
from openpyxl.styles import Font

D = Decimal
CENT = D("0.01")
COMPANY = "บริษัท สยามตัวอย่างการค้า จำกัด"

COA = {
    "1110-01": "เงินสด",
    "1120-01": "เงินฝากธนาคาร - กระแสรายวัน",
    "1130-01": "ลูกหนี้การค้า",
    "1140-01": "สินค้าคงเหลือ",
    "1150-01": "ภาษีซื้อ",
    "1290-01": "ค่าเสื่อมราคาสะสม - อุปกรณ์",
    "2110-01": "เจ้าหนี้การค้า",
    "2150-01": "ภาษีขาย",
    "2160-01": "ภาษีหัก ณ ที่จ่ายค้างจ่าย",
    "2170-02": "เงินประกันสังคมค้างจ่าย",
    "2180-01": "ค่าใช้จ่ายค้างจ่าย",
    "4100-01": "รายได้จากการขายสินค้า",
    "4100-02": "รายได้จากการให้บริการ",
    "4200-01": "ดอกเบี้ยรับ",
    "5100-01": "ต้นทุนขาย",
    "5200-01": "เงินเดือนและค่าจ้าง",
    "5200-02": "เงินสมทบประกันสังคม",
    "5200-04": "ค่าขนส่ง",
    "5300-01": "ค่าที่ปรึกษา",
    "5300-02": "ค่าสอบบัญชี",
    "5400-01": "ค่าเช่าสำนักงาน",
    "5400-02": "ค่าสาธารณูปโภค",
    "5500-01": "ค่าการตลาดและโฆษณา",
    "5600-01": "ค่าเสื่อมราคา",
    "5700-01": "ค่าเดินทาง",
    "5800-01": "ดอกเบี้ยจ่าย",
}
COST_CENTERS = ["CC-100", "CC-200", "CC-300"]
VENDORS = ["บจก. ขนส่งด่วนไทย", "บจก. ที่ปรึกษาธุรกิจสยาม", "บจก. มีเดียพลัส", "บมจ. พลังงานไฟฟ้า",
           "บจก. อาคารสาทรทาวเวอร์", "สำนักงานสอบบัญชี ศรีสุข", "บจก. วัตถุดิบภาคกลาง"]
CUSTOMERS = ["บจก. ค้าปลีกรุ่งเรือง", "หจก. พรเจริญพาณิชย์", "บจก. โมเดิร์นเทรด", "บจก. อินโนเวชั่นซัพพลาย",
             "ร้านทองดีการค้า", "บจก. สตาร์ดิสทริบิวชั่น", "บจก. เมืองทองมาร์ท", "บจก. บางนาโลจิสติกส์"]


def q(x) -> Decimal:
    return D(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    date: dt.date
    doc: str
    account: str
    desc: str
    debit: Decimal = D("0.00")
    credit: Decimal = D("0.00")
    cc: str = ""
    currency: str = "THB"


@dataclass
class Book:
    lines: list[Line] = field(default_factory=list)
    seq: dict = field(default_factory=dict)

    def doc(self, prefix: str, date: dt.date) -> str:
        key = (prefix, date.year, date.month)
        self.seq[key] = self.seq.get(key, 0) + 1
        return f"{prefix}{date.year + 543}{date.month:02d}-{self.seq[key]:04d}"

    def post(self, date, prefix, desc, entries, cc=""):
        doc = self.doc(prefix, date)
        tot_d = sum((e[1] for e in entries), D(0))
        tot_c = sum((e[2] for e in entries), D(0))
        assert tot_d == tot_c, (doc, tot_d, tot_c)
        for acct, dr, cr in entries:
            self.lines.append(Line(date, doc, acct, desc, q(dr), q(cr), cc))
        return doc


# Month profile: (sales, service, cogs_ratio, transport, consulting, marketing, utilities, travel)
PROFILE = {
    "2026-04": (9_600_000, 1_450_000, 0.52, 212_000, 150_000, 390_000, 96_000, 64_000),
    "2026-05": (9_950_000, 1_500_000, 0.52, 221_000, 150_000, 410_000, 101_000, 58_000),
    "2026-06": (10_120_000, 1_560_000, 0.51, 229_000, 160_000, 405_000, 99_500, 71_000),
    "2026-07": (10_380_000, 1_610_000, 0.52, 236_000, 150_000, 430_000, 104_000, 66_000),
    "2026-08": (10_020_000, 1_514_000, 0.52, 241_000, 150_000, 452_000, 102_300, 69_000),
    "2026-09": (10_860_000, 1_620_300, 0.53, 323_000, 480_000, 398_000, 108_700, 74_500),
}


def month_days(period: str) -> list[dt.date]:
    y, m = (int(x) for x in period.split("-"))
    d = dt.date(y, m, 1)
    out = []
    while d.month == m:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def split_amount(total: Decimal, n: int, rnd: random.Random) -> list[Decimal]:
    weights = [rnd.uniform(0.6, 1.4) for _ in range(n)]
    s = sum(weights)
    parts = [q(total * D(str(w / s))) for w in weights]
    parts[-1] = q(total - sum(parts[:-1]))
    return parts


def generate_month(book: Book, period: str, rnd: random.Random) -> None:
    sales, service, cogs_ratio, transport, consulting, marketing, utilities, travel = PROFILE[period]
    days = month_days(period)
    first, last = days[0], days[-1]
    # Sales invoices with 7% output VAT.
    for amt in split_amount(D(sales), 64, rnd):
        d = rnd.choice(days)
        cust = rnd.choice(CUSTOMERS)
        vat = q(amt * D("0.07"))
        book.post(d, "IV", f"ขายสินค้า {cust}", [("1130-01", amt + vat, 0), ("4100-01", 0, amt), ("2150-01", 0, vat)], "CC-100")
    for amt in split_amount(D(service), 18, rnd):
        d = rnd.choice(days)
        cust = rnd.choice(CUSTOMERS)
        vat = q(amt * D("0.07"))
        book.post(d, "IV", f"ค่าบริการติดตั้ง {cust}", [("1130-01", amt + vat, 0), ("4100-02", 0, amt), ("2150-01", 0, vat)], "CC-200")
    # Collections.
    for amt in split_amount(q(D(sales + service) * D("0.96")), 40, rnd):
        d = rnd.choice(days)
        book.post(d, "RV", f"รับชำระหนี้ {rnd.choice(CUSTOMERS)}", [("1120-01", amt, 0), ("1130-01", 0, amt)])
    # Purchases of stock with input VAT.
    purchases = q(D(sales) * D(str(cogs_ratio)) * D("1.02"))
    for amt in split_amount(purchases, 22, rnd):
        d = rnd.choice(days)
        vat = q(amt * D("0.07"))
        book.post(d, "PV", "ซื้อสินค้า บจก. วัตถุดิบภาคกลาง", [("1140-01", amt, 0), ("1150-01", vat, 0), ("2110-01", 0, amt + vat)], "CC-200")
    # Cost of sales at month end.
    cogs = q(D(sales) * D(str(cogs_ratio)))
    book.post(last, "JV", "บันทึกต้นทุนขายประจำเดือน", [("5100-01", cogs, 0), ("1140-01", 0, cogs)], "CC-200")
    # Payroll: salaries 2.1M, WHT 5%, SSO 750 x 90 heads each side.
    salaries = D("2140000.00")
    wht = q(salaries * D("0.05"))
    sso = D("67500.00")
    book.post(last - dt.timedelta(days=2), "PV", "จ่ายเงินเดือนพนักงาน", [
        ("5200-01", salaries, 0), ("2160-01", 0, wht), ("2170-02", 0, sso), ("1120-01", 0, salaries - wht - sso)], "CC-300")
    book.post(last - dt.timedelta(days=2), "JV", "เงินสมทบประกันสังคมส่วนนายจ้าง", [("5200-02", sso, 0), ("2170-02", 0, sso)], "CC-300")
    # Expenses paid through AP with VAT and WHT where Thai practice applies.
    def expense(acct, total, n, vendor, wht_rate, cc, prefix="PV", vat_rate="0.07"):
        for amt in split_amount(D(total), n, rnd):
            d = rnd.choice(days)
            vat = q(amt * D(vat_rate))
            w = q(amt * D(wht_rate))
            book.post(d, prefix, f"{COA[acct]} {vendor}", [
                (acct, amt, 0), ("1150-01", vat, 0), ("2160-01", 0, w), ("2110-01", 0, amt + vat - w)], cc)
    expense("5200-04", transport, 14, "บจก. ขนส่งด่วนไทย", "0.01", "CC-200")
    expense("5300-01", consulting, 2 if consulting > 200_000 else 1, "บจก. ที่ปรึกษาธุรกิจสยาม", "0.03", "CC-300")
    expense("5500-01", marketing, 6, "บจก. มีเดียพลัส", "0.02", "CC-100")
    expense("5400-02", utilities, 3, "บมจ. พลังงานไฟฟ้า", "0.00", "CC-300")
    expense("5400-01", 185_000, 1, "บจก. อาคารสาทรทาวเวอร์", "0.05", "CC-300")
    expense("5300-02", 35_000, 1, "สำนักงานสอบบัญชี ศรีสุข", "0.03", "CC-300")
    for amt in split_amount(D(travel), 9, rnd):
        d = rnd.choice(days)
        book.post(d, "PC", "เบิกค่าเดินทางพนักงาน", [("5700-01", amt, 0), ("1110-01", 0, amt)], "CC-100")
    book.post(last, "JV", "ค่าเสื่อมราคาอุปกรณ์ประจำเดือน", [("5600-01", D("128400.00"), 0), ("1290-01", 0, D("128400.00"))], "CC-300")
    book.post(last, "JV", "ดอกเบี้ยเงินกู้ธนาคาร", [("5800-01", D("41250.00"), 0), ("1120-01", 0, D("41250.00"))], "CC-300")
    book.post(last - dt.timedelta(days=1), "RV", "ดอกเบี้ยรับเงินฝาก", [("1120-01", D("3820.55"), 0), ("4200-01", 0, D("3820.55"))])
    # Pay suppliers.
    for amt in split_amount(q(purchases * D("0.9")), 12, rnd):
        d = rnd.choice(days)
        book.post(d, "PV", "จ่ายชำระหนี้ผู้ขาย", [("2110-01", amt, 0), ("1120-01", 0, amt)])
    _ = first


def build_book(periods: list[str], seed: int = 20260925) -> Book:
    rnd = random.Random(seed)
    book = Book()
    for p in periods:
        generate_month(book, p, rnd)
    book.lines.sort(key=lambda l: (l.date, l.doc, 0 if l.debit > 0 else 1, l.account))
    return book


# ── expected numbers (independent of the parser) ──────────────────────────

def pl_of(lines: list[Line]) -> dict:
    acc: dict[str, Decimal] = {}
    for l in lines:
        acc[l.account] = acc.get(l.account, D(0)) + l.debit - l.credit
    def s(pred, sign):
        return q(sum((v for k, v in acc.items() if pred(k)), D(0)) * sign)
    revenue = s(lambda k: k.startswith("41"), -1)
    other = s(lambda k: k.startswith("4") and not k.startswith("41"), -1)
    cogs = s(lambda k: k.startswith("51"), 1)
    opex = s(lambda k: k.startswith("5") and k[:2] in ("52", "53", "54", "55", "56", "57"), 1)
    fin = s(lambda k: k.startswith("58"), 1)
    tax = s(lambda k: k.startswith("59"), 1)
    net = q(revenue + other - cogs - opex - fin - tax)
    return {
        "revenue": str(revenue), "other_income": str(other), "cost_of_sales": str(cogs),
        "gross_profit": str(q(revenue - cogs)), "operating_expenses": str(opex),
        "finance_costs": str(fin), "income_tax": str(tax),
        "total_expenses": str(q(cogs + opex + fin + tax)), "net_profit": str(net),
        "debit_total": str(q(sum((l.debit for l in lines), D(0)))),
        "credit_total": str(q(sum((l.credit for l in lines), D(0)))),
        "rows": len(lines),
    }


def in_period(lines, period):
    return [l for l in lines if f"{l.date.year:04d}-{l.date.month:02d}" == period]


# ── writers ───────────────────────────────────────────────────────────────

def be(d: dt.date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


def _save(wb: Workbook, path: str) -> None:
    # Fixed metadata and zip entry times so the bytes are reproducible (the
    # package checksum covers sample-data/).
    import io
    import zipfile

    wb.properties.creator = "fixtures"
    wb.properties.created = dt.datetime(2026, 9, 25, 8, 0, 0)
    wb.properties.modified = dt.datetime(2026, 9, 25, 8, 0, 0)
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(2026, 9, 25, 8, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps 'modified' at save time
                import re as _re

                data = _re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                               rb"\g<1>2026-09-25T08:00:00Z\g<2>", data)
            out.writestr(zi, data)


def write_express_th(lines: list[Line], path: str, period_label: str, messy: bool = False) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append([COMPANY])
    ws.append(["รายงานบัญชีแยกประเภท (General Ledger)"])
    ws.append([f"ประจำงวด {period_label}"])
    ws.append([])
    header = ["วันที่", "เลขที่เอกสาร", "รหัสบัญชี", "ชื่อบัญชี", "คำอธิบาย", "ศูนย์ต้นทุน", "เดบิต", "เครดิต"]
    ws.append(header)
    ws["A5"].font = Font(bold=True)
    last_doc = None
    doc_dr = D(0)
    n = 0
    for l in lines:
        if messy and last_doc and l.doc != last_doc:
            ws.append([None, None, None, None, f"รวมเลขที่เอกสาร {last_doc}", None, float(doc_dr), float(doc_dr)])
            doc_dr = D(0)
        if messy and n and n % 180 == 0:
            ws.append([])
            ws.append([COMPANY, None, None, None, "หน้า " + str(n // 180 + 1)])
            ws.append(header)
        ws.append([be(l.date), l.doc, l.account, COA.get(l.account, ""), l.desc, l.cc,
                   float(l.debit) if l.debit else None, float(l.credit) if l.credit else None])
        last_doc = l.doc
        doc_dr += l.debit
        n += 1
    tot_d = sum((l.debit for l in lines), D(0))
    tot_c = sum((l.credit for l in lines), D(0))
    ws.append([None, None, None, None, "รวมทั้งสิ้น", None, float(tot_d), float(tot_c)])
    _save(wb, path)


def write_xero_en(lines, path, currency_override: dict | None = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "General Ledger"
    ws.append(["Date", "Account Code", "Account", "Description", "Reference", "Debit", "Credit", "Currency"])
    for i, l in enumerate(lines):
        cur = (currency_override or {}).get(i, l.currency)
        ws.append([dt.datetime(l.date.year, l.date.month, l.date.day), l.account, COA.get(l.account, ""),
                   l.desc, l.doc, float(l.debit) if l.debit else 0, float(l.credit) if l.credit else 0, cur])
        ws.cell(row=ws.max_row, column=1).number_format = "dd/mm/yyyy"
    _save(wb, path)


def write_sap(lines, path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Line Items"
    ws.append(["Posting Date", "Document Number", "G/L Account", "G/L Account Name", "Text", "Cost Center",
               "Amount in LC", "D/C Ind."])
    for l in lines:
        amt = l.debit or l.credit
        ws.append([l.date.strftime("%d.%m.%Y"), l.doc, l.account.replace("-", ""), COA.get(l.account, ""),
                   l.desc, l.cc, float(amt), "S" if l.debit else "H"])
    _save(wb, path)


def write_dc(lines, path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["วันที่ลงบัญชี", "Account Code", "ชื่อบัญชี", "Description", "Cost Center", "Amount (THB)", "Dr/Cr"])
    for l in lines:
        amt = l.debit or l.credit
        ws.append([be(l.date), l.account, COA.get(l.account, ""), l.desc, l.cc, float(amt), "D" if l.debit else "C"])
    _save(wb, path)


def write_signed_csv_cp874(lines, path) -> None:
    with open(path, "w", encoding="cp874", newline="") as f:
        w = csv.writer(f)
        w.writerow(["วันที่", "เลขที่", "รหัสบัญชี", "ชื่อบัญชี", "รายละเอียด", "จำนวนเงิน"])
        for l in lines:
            amt = l.debit if l.debit else -l.credit
            w.writerow([be(l.date), l.doc, l.account, COA.get(l.account, ""), l.desc, f"{amt:.2f}"])


def write_unknown_date_header(lines, path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Trans Dt", "Doc", "Acct Code", "Acct Name", "Narrative", "Dr Amt", "Cr Amt"])
    for l in lines:
        ws.append([be(l.date), l.doc, l.account, COA.get(l.account, ""), l.desc,
                   float(l.debit) if l.debit else None, float(l.credit) if l.credit else None])
    _save(wb, path)


def write_tb(lines, path, period_end_label: str) -> None:
    acc: dict[str, list[Decimal]] = {}
    for l in lines:
        a = acc.setdefault(l.account, [D(0), D(0)])
        a[0] += l.debit
        a[1] += l.credit
    wb = Workbook()
    ws = wb.active
    ws.title = "งบทดลอง"
    ws.append([COMPANY])
    ws.append([f"งบทดลอง ณ วันที่ {period_end_label}"])
    ws.append([])
    ws.append(["รหัสบัญชี", "ชื่อบัญชี", "เดบิต", "เครดิต"])
    for code in sorted(acc):
        dr, cr = acc[code]
        net = dr - cr
        ws.append([code, COA.get(code, ""), float(net) if net > 0 else None, float(-net) if net < 0 else None])
    _save(wb, path)


def write_pl(lines, path, period_label: str) -> None:
    acc: dict[str, Decimal] = {}
    for l in lines:
        acc[l.account] = acc.get(l.account, D(0)) + l.debit - l.credit
    wb = Workbook()
    ws = wb.active
    ws.title = "PL"
    ws.append([COMPANY])
    ws.append([f"งบกำไรขาดทุน สำหรับเดือน{period_label}"])
    ws.append([])
    ws.append(["รหัสบัญชี", "รายการ", "จำนวนเงิน"])
    ws.append([None, "รายได้", None])
    rev_total = D(0)
    for code in sorted(k for k in acc if k.startswith("4")):
        ws.append([code, COA[code], float(-acc[code])])
        rev_total += -acc[code]
    ws.append([None, "รวมรายได้", float(rev_total)])
    ws.append([None, "ค่าใช้จ่าย", None])
    exp_total = D(0)
    for code in sorted(k for k in acc if k.startswith("5")):
        ws.append([code, COA[code], float(acc[code])])
        exp_total += acc[code]
    ws.append([None, "รวมค่าใช้จ่าย", float(exp_total)])
    ws.append([None, "กำไรสุทธิ", float(rev_total - exp_total)])
    _save(wb, path)


def budget_numbers(periods: list[str]) -> dict[str, dict[int, Decimal]]:
    """Monthly budget per P&L account (round numbers, like a real budget)."""
    base = {
        "4100-01": 10_400_000, "4100-02": 1_550_000, "4200-01": 4_000,
        "5100-01": 5_400_000, "5200-01": 2_140_000, "5200-02": 67_500, "5200-04": 240_000,
        "5300-01": 150_000, "5300-02": 35_000, "5400-01": 185_000, "5400-02": 100_000,
        "5500-01": 450_000, "5600-01": 128_400, "5700-01": 70_000, "5800-01": 41_250,
    }
    out = {}
    for code, v in base.items():
        out[code] = {m: D(v) for m in range(1, 13)}
    return out


def write_budget(path: str, year_be: int) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append([COMPANY])
    ws.append([f"งบประมาณประจำปี {year_be}"])
    months = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    ws.append(["รหัสบัญชี", "ชื่อบัญชี"] + [f"{m} {str(year_be)[-2:]}" for m in months] + ["รวมทั้งปี"])
    for code, by_m in budget_numbers([]).items():
        vals = [float(by_m[m]) for m in range(1, 13)]
        ws.append([code, COA[code]] + vals + [sum(vals)])
    _save(wb, path)


def write_customer_list(path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["รหัสลูกค้า", "ชื่อลูกค้า", "ที่อยู่", "เบอร์โทร", "วงเงินเครดิต"])
    for i, c in enumerate(CUSTOMERS):
        ws.append([f"C{i + 1:04d}", c, "กรุงเทพมหานคร", "02-000-0000", 500000])
    _save(wb, path)


def write_bank_statement(path: str, lines) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["รายการเดินบัญชี ธนาคารตัวอย่าง"])
    ws.append(["วันที่", "รายการ", "ถอน", "ฝาก", "คงเหลือ"])
    bal = D("1500000.00")
    for l in lines:
        if l.account != "1120-01":
            continue
        bal += l.debit - l.credit
        ws.append([be(l.date), l.desc, float(l.credit) or None, float(l.debit) or None, float(bal)])
    _save(wb, path)


# ── cases ─────────────────────────────────────────────────────────────────

def main(out_dir: str) -> None:
    periods = list(PROFILE)
    book = build_book(periods)
    sep = in_period(book.lines, "2026-09")
    aug = in_period(book.lines, "2026-08")
    ytd = [l for l in book.lines if l.date >= dt.date(2026, 4, 1)]

    sample_dir = os.path.join(out_dir, "sample-data")
    fx = os.path.join(out_dir, "evals", "fixtures")
    os.makedirs(sample_dir, exist_ok=True)
    os.makedirs(fx, exist_ok=True)

    # Sample data shipped with the package (self-test at install).
    write_express_th(sep, os.path.join(sample_dir, "GL_2569-09.xlsx"), "กันยายน 2569")
    write_express_th(aug, os.path.join(sample_dir, "GL_2569-08.xlsx"), "สิงหาคม 2569")
    write_budget(os.path.join(sample_dir, "งบประมาณ_2569.xlsx"), 2569)

    cases = []

    def case(cid, files, expect, message="", defaults=None, answers=None, note=""):
        cases.append({"id": cid, "note": note, "files": files, "message": message,
                      "defaults": defaults or {}, "answers": answers or {}, "expect": expect})

    exp_sep = pl_of(sep)
    exp_aug = pl_of(aug)

    # 1-7: clean inputs — zero questions.
    write_express_th(sep, os.path.join(fx, "clean_express_sep.xlsx"), "กันยายน 2569")
    case("clean_express_th", ["clean_express_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         message="ปิดงบเดือนกันยายนให้หน่อย", note="Express-style Thai GL, title rows — the clean case: zero questions")
    write_xero_en(sep, os.path.join(fx, "clean_xero_sep.xlsx"))
    case("clean_xero_en", ["clean_xero_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         note="English headers, real Excel dates, period from the data alone")
    write_express_th(aug, os.path.join(fx, "clean_express_aug.xlsx"), "สิงหาคม 2569")
    case("clean_with_prior", ["clean_express_sep.xlsx", "clean_express_aug.xlsx"],
         {"findings": [], "pl": exp_sep, "prior_pl": exp_aug, "period": "2026-09"},
         message="ปิดงบกันยายน เทียบกับเดือนก่อนด้วย", note="variance vs prior period")
    write_budget(os.path.join(fx, "budget_2569.xlsx"), 2569)
    case("clean_with_budget", ["clean_express_sep.xlsx", "budget_2569.xlsx"],
         {"findings": [], "pl": exp_sep, "period": "2026-09", "budget_revenue": "11950000.00"},
         note="budget file with monthly columns")
    write_express_th(ytd, os.path.join(fx, "ytd_apr_sep.xlsx"), "เมษายน - กันยายน 2569")
    case("clean_ytd_trend", ["ytd_apr_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09", "trend_months": 6},
         message="ปิดงบเดือนกันยายน 2569", note="multi-month export: history feeds the trend, no question")
    write_express_th(sep, os.path.join(fx, "messy_express_sep.xlsx"), "กันยายน 2569", messy=True)
    case("messy_subtotals_page_headers", ["messy_express_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         message="ปิดงบกันยายน", note="subtotal lines per document, repeated page headers, a grand total row")
    write_tb(sep, os.path.join(fx, "tb_sep.xlsx"), "30 กันยายน 2569")
    case("trial_balance", ["tb_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         note="trial balance (account level, no dates)")
    write_pl(sep, os.path.join(fx, "pl_sep.xlsx"), "กันยายน 2569")
    case("pl_export", ["pl_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         note="P&L export with section and total rows")
    write_signed_csv_cp874(sep, os.path.join(fx, "signed_cp874_sep.csv"))
    case("csv_cp874_signed_remembered", ["signed_cp874_sep.csv"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         defaults={"dc_convention": {"จำนวนเงิน": "positive_debit"}},
         note="Thai CSV in CP874 with signed amounts; the sign convention was remembered at install")

    # Questions that must fire.
    case("csv_cp874_signed_asks", ["signed_cp874_sep.csv"], {"findings": ["ambiguous_debit_credit"], "period": "2026-09"},
         note="same file, nothing remembered: the sign convention is asked once")
    write_sap(sep, os.path.join(fx, "sap_sh_sep.xlsx"))
    case("sap_s_h_indicator", ["sap_sh_sep.xlsx"], {"findings": ["ambiguous_debit_credit"], "period": "2026-09"},
         note="SAP S/H indicator")
    case("sap_s_h_answered", ["sap_sh_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         answers={"ambiguous_debit_credit": {"S": "debit", "H": "credit"}}, note="after the answer, numbers match")
    write_dc(sep, os.path.join(fx, "dc_indicator_sep.xlsx"))
    case("dc_indicator", ["dc_indicator_sep.xlsx"], {"findings": ["ambiguous_debit_credit"], "period": "2026-09"},
         message="ปิดงบเดือนกันยายน", note="the mockup's D/C column")
    write_unknown_date_header(sep, os.path.join(fx, "unmapped_date_sep.xlsx"))
    case("unmapped_date_column", ["unmapped_date_sep.xlsx"], {"findings": ["unmapped_column"]},
         note="'Trans Dt' is not a known date header")
    case("unmapped_remembered", ["unmapped_date_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         defaults={"mapping": {"date": "Trans Dt", "debit": "Dr Amt", "credit": "Cr Amt", "account_code": "Acct Code"}},
         note="the install mapping covers it: silence")

    # period_mixed: 14 October rows in a September export (the card's example).
    oct_lines = build_book(["2026-09"], seed=77).lines
    # Whole documents only, so the October rows stay balanced among themselves.
    docs = {}
    for l in oct_lines:
        docs.setdefault(l.doc, []).append(l)
    oct_rows = []
    for doc, ls in docs.items():
        if len(oct_rows) + len(ls) > 14:
            continue
        for l in ls:
            oct_rows.append(Line(dt.date(2026, 10, 1 + len(oct_rows) % 3), doc.replace("256909", "256910"),
                                 l.account, l.desc, l.debit, l.credit, l.cc))
        if len(oct_rows) >= 12:
            break
    write_express_th(sep + oct_rows, os.path.join(fx, "period_mixed_sep.xlsx"), "กันยายน 2569")
    case("period_mixed_oct_rows", ["period_mixed_sep.xlsx"],
         {"findings": ["period_mixed"], "period": "2026-09", "excluded_rows": len(oct_rows)},
         message="ปิดงบเดือนกันยายน", note=f"{len(oct_rows)} October rows mixed into a September export")
    case("period_mixed_excluded", ["period_mixed_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         message="ปิดงบเดือนกันยายน", answers={"period_mixed": "exclude"}, note="answered: exclude → exact September numbers")
    case("period_mixed_remembered", ["period_mixed_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09",
                                                                "assumption_codes": ["period_mixed"]},
         message="ปิดงบเดือนกันยายน", defaults={"answers": {"period_mixed": "exclude"}},
         note="remembered policy applied silently and written as an assumption")

    both = aug + sep
    write_xero_en(both, os.path.join(fx, "aug_sep_mixed.xlsx"))
    case("period_undetermined", ["aug_sep_mixed.xlsx"], {"findings": ["period_undetermined"]},
         note="two full months, no month named anywhere")
    case("period_from_message", ["aug_sep_mixed.xlsx"], {"findings": [], "period": "2026-09", "pl": exp_sep,
                                                          "trend_months": 2},
         message="ขอรายงานเดือนกันยายน 2569", note="the user named the month; August becomes history, not a question")

    cur_override = {i: "USD" for i in range(5, 35, 3)}
    write_xero_en(sep, os.path.join(fx, "currency_mixed_sep.xlsx"), cur_override)
    case("currency_mixed", ["currency_mixed_sep.xlsx"], {"findings": ["currency_mixed"], "period": "2026-09"},
         note="ten USD rows among THB rows")

    write_customer_list(os.path.join(fx, "customer_list.xlsx"))
    case("wrong_document_type", ["customer_list.xlsx"], {"findings": ["missing_file", "wrong_document_type"]},
         note="a customer list where a GL was needed")
    write_bank_statement(os.path.join(fx, "bank_statement.xlsx"), sep)
    case("bank_statement_only", ["bank_statement.xlsx"], {"findings": ["missing_file", "wrong_document_type"]},
         note="a bank statement parses but is not a ledger")
    case("missing_gl_budget_only", ["budget_2569.xlsx"], {"findings": ["missing_file"]},
         note="only the budget arrived")

    # Postcompute: unbalanced (drop one credit line).
    unbalanced = list(sep)
    drop_idx = next(i for i, l in enumerate(unbalanced) if l.account == "2150-01" and l.credit > 0)
    dropped = unbalanced.pop(drop_idx)
    write_express_th(unbalanced, os.path.join(fx, "unbalanced_sep.xlsx"), "กันยายน 2569")
    case("unbalanced", ["unbalanced_sep.xlsx"], {"findings": ["unbalanced"], "period": "2026-09",
                                                  "imbalance": str(dropped.credit)},
         message="ปิดงบกันยายน", note=f"one output-VAT line of {dropped.doc} missing")

    # Duplicate: re-post a whole transport document under a new number.
    dup_src_doc = next(l.doc for l in sep if l.account == "5200-04")
    dup = [l for l in sep if l.doc == dup_src_doc]
    dup_copy = [Line(l.date, l.doc[:-4] + "9901", l.account, l.desc, l.debit, l.credit, l.cc) for l in dup]
    with_dup = sorted(sep + dup_copy, key=lambda l: (l.date, l.doc))
    write_express_th(with_dup, os.path.join(fx, "duplicate_sep.xlsx"), "กันยายน 2569")
    case("duplicate_suspect", ["duplicate_sep.xlsx"], {"findings": ["duplicate_suspect"], "period": "2026-09"},
         message="ปิดงบกันยายน", note=f"document {dup_src_doc} posted twice")
    case("duplicate_excluded", ["duplicate_sep.xlsx"], {"findings": [], "pl": exp_sep, "period": "2026-09"},
         message="ปิดงบกันยายน", answers={"duplicate_suspect": "exclude"}, note="excluding the copy gives exact numbers")

    # Materiality: a typo'd entry — marketing 4,500,000 instead of 45,000.
    typo = list(sep)
    idx = next(i for i, l in enumerate(typo) if l.account == "5500-01")
    src = typo[idx]
    typo_doc = [Line(src.date, src.doc[:-4] + "9902", "5500-01", "ค่าโฆษณา บจก. มีเดียพลัส", D("4500000.00"), D(0), "CC-100"),
                Line(src.date, src.doc[:-4] + "9902", "2110-01", "ค่าโฆษณา บจก. มีเดียพลัส", D(0), D("4500000.00"), "CC-100")]
    write_express_th(sorted(typo + typo_doc, key=lambda l: (l.date, l.doc)), os.path.join(fx, "materiality_sep.xlsx"), "กันยายน 2569")
    case("materiality_exceeded", ["materiality_sep.xlsx"], {"findings": ["materiality_exceeded"], "period": "2026-09"},
         message="ปิดงบกันยายน", note="one marketing entry of 4,500,000")

    # Prior period mismatch: 'prior' file is September of last year.
    ly = build_book(["2026-08"], seed=11).lines
    ly = [Line(dt.date(2025, 9, min(l.date.day, 30)), l.doc, l.account, l.desc, l.debit, l.credit, l.cc) for l in ly]
    write_express_th(ly, os.path.join(fx, "prior_wrong_year.xlsx"), "กันยายน 2568")
    case("prior_period_mismatch", ["clean_express_sep.xlsx", "prior_wrong_year.xlsx"],
         {"findings": ["prior_period_mismatch"], "period": "2026-09"},
         message="ปิดงบกันยายน เทียบเดือนก่อน", note="the 'previous month' file is September 2568")

    # Batching: five findings in one run (DC indicator + October rows + USD rows + unknown account names…)
    mixed = list(sep) + oct_rows
    wb = Workbook()
    ws = wb.active
    ws.append(["วันที่ลงบัญชี", "Account Code", "Description", "Amount (THB)", "Dr/Cr", "Currency", "Region"])
    for i, l in enumerate(mixed):
        amt = l.debit or l.credit
        # Every 7th date typed with a Gregorian year: a second year convention.
        date_text = f"{l.date.day:02d}/{l.date.month:02d}/{l.date.year}" if i % 7 == 0 else be(l.date)
        ws.append([date_text, l.account, l.desc, float(amt), "D" if l.debit else "C",
                   "USD" if i in cur_override else "THB", "BKK"])
    _save(wb, os.path.join(fx, "five_findings.xlsx"))
    case("batching_five_findings", ["five_findings.xlsx"],
         {"findings": ["ambiguous_debit_credit", "account_name_missing", "currency_mixed", "period_mixed", "year_convention_mixed"],
          "min_findings": 5},
         message="ปิดงบเดือนกันยายน", note="blocking + cosmetic findings in one pass (batching)")

    with open(os.path.join(out_dir, "evals", "cases.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "agent.market.eval.v1", "package": "sran.financial-report", "cases": cases},
                  f, ensure_ascii=False, indent=1)
    print(f"{len(cases)} cases, {len(os.listdir(fx))} fixtures, sample-data {sorted(os.listdir(sample_dir))}")


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "packages", "financial_report"))
