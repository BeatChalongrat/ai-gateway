"""Deterministic generator for the bank reconciliation agent's sample data and eval set.

    python -m agent_market.tools.make_bank_fixtures

One fictional current account ("บริษัท สยามตัวอย่างการค้า จำกัด", กระแสรายวัน) kept two ways — the
company's ledger of the account and the bank's statement of it — written out in the shapes the
files really come in: statements in five layouts (ถอน/ฝาก with Buddhist-era dates and no cheque
column; newest row first; English headers and real date cells; a CP874 CSV whose columns are
เดบิต/เครดิต; a CSV with one signed amount), ledgers as an Express-style account ledger with title
rows and an opening line, a bare เดบิต/เครดิต sheet that does not say whose it is, and a general
ledger that holds several accounts; and the list of items carried in from the month before.

The layouts are modelled on what Thai banks' business portals export; they were written from
their column names, not copied from a real customer's statement.

The expected numbers in evals/cases.json come from the scenario itself (each line says what it
truly is: matched, an outstanding cheque, a fee…) and plain addition here — never from the
package — so the eval compares two independent code paths. The same command always writes
byte-identical data.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
from dataclasses import dataclass
from decimal import Decimal

from openpyxl import Workbook

from .make_fixtures import _save

D = Decimal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packages", "bank_reconciliation")
COMPANY = "บริษัท สยามตัวอย่างการค้า จำกัด"
BANK = "ธนาคารกสิกรไทย"
ACCOUNT_NO = "123-4-56789-0"
ACCOUNT_NAME = "บจก. สยามตัวอย่างการค้า"
GL_CODE = "1120-01"
GL_NAME = "เงินฝากกระแสรายวัน ธ.กสิกรไทย"
BANK_SIDE = ("outstanding", "transit", "bank_error")
BOOK_SIDE = ("fee", "interest", "wht", "nsf", "dd", "unrecorded", "book_error")
TYPE_OF = {"outstanding": "outstanding_cheque", "outstanding_payment": "outstanding_payment", "transit": "deposit_in_transit",
           "fee": "bank_fee", "interest": "interest", "wht": "wht_interest", "nsf": "returned_cheque", "dd": "direct_debit"}


@dataclass
class Bk:
    """A row of the company's ledger. amount: money in > 0."""
    day: int
    doc: str
    text: str
    amount: str
    chq: str = ""
    month: int = 9
    year: int = 2026
    tag: str = ""          # what it truly is: "" has a partner · outstanding · transit · unknown · duplicate · outside

    @property
    def date(self):
        return dt.date(self.year, self.month, self.day)


@dataclass
class Bn:
    """A row of the bank's statement. amount: money in > 0."""
    day: int
    text: str
    amount: str
    chq: str = ""
    month: int = 9
    year: int = 2026
    tag: str = ""          # "" has a partner · fee · interest · wht · nsf · dd · unrecorded · unknown · outside

    @property
    def date(self):
        return dt.date(self.year, self.month, self.day)


def be(d: dt.date, sep: str = "/", short: bool = False) -> str:
    y = d.year + 543
    return f"{d.day:02d}{sep}{d.month:02d}{sep}{str(y)[-2:] if short else y}"


def f(v) -> float | None:
    return float(v) if v else None


# ── the month ────────────────────────────────────────────────────────────

def september() -> tuple[list, list]:
    book = [
        Bk(1, "RV6909-001", "รับชำระ บจก.เอเชีย คลาวด์ โซลูชั่นส์", "125000.00"),
        Bk(2, "PV6909-001", "จ่ายค่าเช่าสำนักงาน", "-45000.00", chq="10001201"),
        Bk(3, "PV6909-002", "จ่ายค่าวัสดุสำนักงาน ร้านเครื่องเขียนสมใจ", "-8560.00", chq="10001202"),
        Bk(5, "RV6909-002", "รับชำระ หจก.สยามการพิมพ์", "32100.00"),
        Bk(8, "PV6909-003", "โอนจ่ายเงินเดือนพนักงาน", "-286400.00"),
        Bk(10, "PV6909-004", "จ่ายค่าไฟฟ้า การไฟฟ้านครหลวง", "-12347.85", chq="10001203"),
        Bk(15, "RV6909-003", "รับชำระ บจก.ไทยเทรดดิ้ง", "58850.00"),
        Bk(18, "PV6909-005", "โอนจ่าย บจก.ซัพพลายพลัส", "-21400.00"),
        Bk(22, "RV6909-004", "รับชำระ บมจ.กรุงเทพโลจิสติกส์", "214000.00"),
        Bk(25, "PV6909-006", "จ่ายค่าซ่อมบำรุงเครื่องปรับอากาศ", "-17655.00", chq="10001204"),
        Bk(28, "PV6909-007", "จ่ายค่าบริการทำความสะอาด", "-9630.00", chq="10001205", tag="outstanding"),
        Bk(29, "PV6909-008", "จ่ายค่าที่ปรึกษากฎหมาย", "-32100.00", chq="10001206", tag="outstanding"),
        Bk(30, "RV6909-005", "นำฝากเช็ครับ บจก.นอร์ทสตาร์", "40660.00", tag="transit"),
    ]
    bank = [
        Bn(1, "โอนเงินเข้า จาก บจก.เอเชีย คลาวด์", "125000.00"),
        Bn(4, "เช็ค 10001201", "-45000.00", chq="10001201"),
        Bn(5, "เช็ค 10001202", "-8560.00", chq="10001202"),
        Bn(5, "โอนเงินเข้า จาก หจก.สยามการพิมพ์", "32100.00"),
        Bn(8, "โอนเงินออก จ่ายเงินเดือน", "-286400.00"),
        Bn(12, "เช็ค 10001203", "-12347.85", chq="10001203"),
        Bn(15, "โอนเงินเข้า จาก บจก.ไทยเทรดดิ้ง", "58850.00"),
        Bn(18, "โอนเงินออก ไป บจก.ซัพพลายพลัส", "-21400.00"),
        Bn(20, "ค่าธรรมเนียมโอนเงิน", "-35.00", tag="fee"),
        Bn(22, "โอนเงินเข้า จาก บมจ.กรุงเทพโลจิสติกส์", "214000.00"),
        Bn(28, "เช็ค 10001204", "-17655.00", chq="10001204"),
        Bn(30, "ดอกเบี้ยรับ", "1250.00", tag="interest"),
        Bn(30, "ภาษีหัก ณ ที่จ่าย", "-12.50", tag="wht"),
        Bn(30, "ค่าธรรมเนียมรักษาบัญชี", "-200.00", tag="fee"),
    ]
    return book, bank


OPENING = D("1500000.00")


# ── statements ───────────────────────────────────────────────────────────

def _running(lines: list, opening: Decimal):
    bal = opening
    for l in sorted(lines, key=lambda x: x.date):
        bal += D(l.amount)
        yield l, bal


def write_kbank(path: str, lines: list, opening: Decimal, *, drop: tuple = ()) -> dict:
    """ถอนเงิน/ฝากเงิน, the date as 01-09-69, the cheque number only inside the text.
    ``drop``: rows (by position) left out of the file while the running balance still counts them —
    a statement with a page missing."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append([f"{BANK} จำกัด (มหาชน)"])
    ws.append(["รายการเดินบัญชีเงินฝากกระแสรายวัน"])
    ws.append([f"ชื่อบัญชี: {ACCOUNT_NAME}", None, None, f"เลขที่บัญชี {ACCOUNT_NO}"])
    rows = list(_running(lines, opening))
    first, last = rows[0][0].date.replace(day=1), rows[-1][0].date
    ws.append([f"ระหว่างวันที่ {be(first)} ถึง {be(last)}"])
    ws.append([])
    ws.append(["วันที่", "เวลา", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ", "ช่องทาง"])
    ws.append([be(first, "-", True), None, "ยอดยกมา", None, None, float(opening), None])
    at = {}
    for i, (l, bal) in enumerate(rows):
        if i in drop:
            continue
        a = D(l.amount)
        ws.append([be(l.date, "-", True), f"{9 + i % 8:02d}:{(i * 7) % 60:02d}", l.text, f(-a if a < 0 else 0), f(a if a > 0 else 0),
                   float(bal), "K BIZ" if "โอน" in l.text else "สาขา"])
        at[id(l)] = ws.max_row
    _save(wb, path)
    return at


def write_scb(path: str, lines: list, opening: Decimal) -> None:
    """The newest row first; a cheque column; dates as 30/09/2569."""
    wb = Workbook()
    ws = wb.active
    ws.title = "รายการเดินบัญชี"
    ws.append(["ธนาคารไทยพาณิชย์ จำกัด (มหาชน) — รายการเดินบัญชี"])
    ws.append([f"เลขที่บัญชี {ACCOUNT_NO}  ชื่อบัญชี: {ACCOUNT_NAME}"])
    ws.append([])
    ws.append(["วันที่", "เวลา", "รายการ", "เลขที่เช็ค", "ถอน", "ฝาก", "คงเหลือ", "ช่องทาง"])
    for i, (l, bal) in enumerate(reversed(list(_running(lines, opening)))):
        a = D(l.amount)
        ws.append([be(l.date), "10:00", l.text, l.chq or None, f(-a if a < 0 else 0), f(a if a > 0 else 0), float(bal), "ENET"])
    _save(wb, path)


def write_bbl(path: str, lines: list, opening: Decimal) -> None:
    """English headers, real date cells, a B/F and a C/F line."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append(["Bangkok Bank Public Company Limited"])
    ws.append(["STATEMENT OF ACCOUNT"])
    ws.append([f"Account No. {ACCOUNT_NO}", None, "Account Name: SIAM TUAYANG TRADING CO., LTD."])
    ws.append([])
    ws.append(["Date", "Particulars", "Cheque No.", "Withdrawal", "Deposit", "Balance", "Via"])
    rows = list(_running(lines, opening))
    ws.append([None, "BALANCE B/F", None, None, None, float(opening), None])
    for l, bal in rows:
        a = D(l.amount)
        ws.append([dt.datetime(l.date.year, l.date.month, l.date.day), l.text, l.chq or None, f(-a if a < 0 else 0),
                   f(a if a > 0 else 0), float(bal), "BR"])
        ws.cell(row=ws.max_row, column=1).number_format = "dd/mm/yyyy"
    ws.append([None, "BALANCE C/F", None, None, None, float(rows[-1][1]), None])
    _save(wb, path)


def write_ktb_csv(path: str, lines: list, opening: Decimal) -> None:
    """CP874, เดบิต/เครดิต as the bank means them (a debit takes money out)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["รายการเดินบัญชี ธนาคารกรุงไทย จำกัด (มหาชน)"])
    w.writerow([f"เลขที่บัญชี {ACCOUNT_NO}"])
    w.writerow(["วันที่", "รายการ", "เลขที่เช็ค", "เดบิต", "เครดิต", "ยอดคงเหลือ", "สาขา"])
    for l, bal in _running(lines, opening):
        a = D(l.amount)
        w.writerow([be(l.date), l.text, l.chq, f"{-a:,.2f}" if a < 0 else "", f"{a:,.2f}" if a > 0 else "", f"{bal:,.2f}", "0012"])
    with open(path, "wb") as fh:
        fh.write(out.getvalue().encode("cp874"))


def write_signed_csv(path: str, lines: list, opening: Decimal) -> None:
    """One amount column: a withdrawal is a negative number."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["Statement"])
    w.writerow([f"Account No. {ACCOUNT_NO}"])
    w.writerow(["วันที่", "รายละเอียด", "จำนวนเงิน", "ยอดคงเหลือ"])
    for l, bal in _running(lines, opening):
        w.writerow([be(l.date), l.text, f"{D(l.amount):.2f}", f"{bal:.2f}"])
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"))


# ── ledgers ──────────────────────────────────────────────────────────────

def write_ledger(path: str, lines: list, opening: Decimal, *, balance: bool = True, month_label: str = "กันยายน 2569") -> dict:
    """An account ledger as Express prints it: title rows, an opening line, เดบิต/เครดิต, a running balance."""
    wb = Workbook()
    ws = wb.active
    ws.title = "GL"
    ws.append([COMPANY])
    ws.append([f"บัญชีแยกประเภท — {GL_CODE} {GL_NAME}"])
    ws.append([f"ประจำเดือน {month_label}"])
    ws.append([])
    heads = ["วันที่", "เลขที่เอกสาร", "เลขที่เช็ค", "คำอธิบาย", "เดบิต", "เครดิต"] + (["คงเหลือ"] if balance else [])
    ws.append(heads)
    first = sorted(lines, key=lambda x: x.date)[0].date.replace(day=1)
    ws.append([be(first), None, None, "ยอดยกมา", f(opening if opening > 0 else 0) if not balance else None,
               f(-opening if opening < 0 else 0) if not balance else None] + ([float(opening)] if balance else []))
    at = {}
    for l, bal in _running(lines, opening):
        a = D(l.amount)
        ws.append([be(l.date), l.doc, l.chq or None, l.text, f(a if a > 0 else 0), f(-a if a < 0 else 0)] + ([float(bal)] if balance else []))
        at[id(l)] = ws.max_row
    ws.append([None, None, None, "รวม", float(sum((D(l.amount) for l in lines if D(l.amount) > 0), D(0))),
               float(sum((-D(l.amount) for l in lines if D(l.amount) < 0), D(0)))])
    _save(wb, path)
    return at


def write_bare(path: str, lines: list, opening: Decimal) -> None:
    """เดบิต/เครดิต and nothing that says whether the bank or the company wrote it."""
    wb = Workbook()
    ws = wb.active
    ws.append(["วันที่", "รายละเอียด", "เดบิต", "เครดิต", "คงเหลือ"])
    for l, bal in _running(lines, opening):
        a = D(l.amount)
        ws.append([be(l.date), l.text, f(a if a > 0 else 0), f(-a if a < 0 else 0), float(bal)])
    _save(wb, path)


def write_gl(path: str, lines: list, opening: Decimal) -> None:
    """A general ledger: the bank account among others."""
    wb = Workbook()
    ws = wb.active
    ws.title = "GL"
    ws.append([COMPANY])
    ws.append(["สมุดบัญชีแยกประเภททั่วไป ประจำเดือน กันยายน 2569"])
    ws.append([])
    ws.append(["รหัสบัญชี", "ชื่อบัญชี", "วันที่", "เลขที่เอกสาร", "เลขที่เช็ค", "คำอธิบาย", "เดบิต", "เครดิต", "คงเหลือ"])
    ws.append([GL_CODE, GL_NAME, be(dt.date(2026, 9, 1)), None, None, "ยอดยกมา", None, None, float(opening)])
    for l, bal in _running(lines, opening):
        a = D(l.amount)
        ws.append([GL_CODE, GL_NAME, be(l.date), l.doc, l.chq or None, l.text, f(a if a > 0 else 0), f(-a if a < 0 else 0), float(bal)])
    other = [("1110-01", "เงินสด", 3, "JV6909-010", "เบิกเงินสดย่อย", "5000.00", ""),
             ("1130-01", "ลูกหนี้การค้า", 1, "RV6909-001", "รับชำระ บจก.เอเชีย คลาวด์ โซลูชั่นส์", "", "125000.00"),
             ("1130-01", "ลูกหนี้การค้า", 5, "RV6909-002", "รับชำระ หจก.สยามการพิมพ์", "", "32100.00"),
             ("5400-01", "ค่าเช่าสำนักงาน", 2, "PV6909-001", "จ่ายค่าเช่าสำนักงาน", "45000.00", "")]
    for code, name, day, doc, text, dr, cr in other:
        ws.append([code, name, be(dt.date(2026, 9, day)), doc, None, text, f(D(dr)) if dr else None, f(D(cr)) if cr else None, None])
    _save(wb, path)


def write_carried(path: str, items: list) -> list:
    wb = Workbook()
    ws = wb.active
    ws.title = "รายการค้างยกไป"
    ws.append(["รายการค้างยกไป"])
    ws.append(["รายการที่ยังค้าง ณ สิ้นเดือนก่อน"])
    ws.append([])
    ws.append(["วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงิน", "ประเภท"])
    at = []
    for date, ref, text, amount, kind in items:
        ws.append([be(date), ref, text, float(D(amount)), kind])
        at.append(ws.max_row)
    _save(wb, path)
    return at


def write_pdf_stub(path: str) -> None:
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n% not a document: a file that only calls itself a PDF\n%%EOF\n")


STATEMENT_CSS = """
body { font-family: 'Sarabun', sans-serif; font-size: 10pt; color: #111; }
h1 { font-size: 14pt; margin: 0; } h2 { font-size: 11pt; margin: 2px 0 8px; font-weight: 400; }
.meta { margin-bottom: 8px; }
table { width: 100%; border-collapse: collapse; }
thead { display: table-header-group; }
th { border-bottom: 1.5px solid #333; padding: 4px 6px; text-align: left; font-size: 9.5pt; }
td { border-bottom: 1px solid #ddd; padding: 3px 6px; }
th.r, td.r { text-align: right; }
tr { page-break-inside: avoid; }
tr.break { page-break-after: always; }
"""


def _chrome(html_text: str, path: str) -> None:
    from pathlib import Path

    from pypdf import PdfReader, PdfWriter

    from .e2fix import render

    hp = Path(path).with_suffix(".html")
    hp.write_text(html_text, encoding="utf-8", newline="\n")
    raw = Path(path).with_suffix(".raw.pdf")
    render.chrome_pdf(hp, raw)
    w = PdfWriter(clone_from=PdfReader(str(raw)))
    w.add_metadata({"/Producer": "fixture (Chrome print)", "/Creator": "agent_market.tools.make_bank_fixtures",
                    "/CreationDate": "D:20260926000000+07'00'", "/ModDate": "D:20260926000000+07'00'"})
    with open(path, "wb") as f:
        w.write(f)
    raw.unlink()
    hp.unlink()
    prof = Path(path).parent / ".chrome-profile"
    if prof.exists():
        import shutil

        shutil.rmtree(prof, ignore_errors=True)


def write_pdf_statement(path: str, lines: list, opening: Decimal, *, break_after: int = 9) -> None:
    """The statement as the PDF a bank's portal hands out: a heading, the account, and the table
    over two pages with its header repeated on the second."""
    import html as _h

    from .e2fix.html import _page

    rows = list(_running(lines, opening))
    first, last = rows[0][0].date.replace(day=1), rows[-1][0].date
    body = [f"<h1>{_h.escape(BANK)} จำกัด (มหาชน)</h1><h2>รายการเดินบัญชีเงินฝากกระแสรายวัน</h2>",
            f"<div class='meta'>ชื่อบัญชี: {_h.escape(ACCOUNT_NAME)} &nbsp;&nbsp;&nbsp; เลขที่บัญชี {ACCOUNT_NO}<br>"
            f"ระหว่างวันที่ {be(first)} ถึง {be(last)}</div>",
            "<table><thead><tr><th>วันที่</th><th>รายการ</th><th class='r'>ถอนเงิน</th><th class='r'>ฝากเงิน</th>"
            "<th class='r'>ยอดคงเหลือ</th><th>ช่องทาง</th></tr></thead><tbody>",
            f"<tr><td>{be(first)}</td><td>ยอดยกมา</td><td class='r'></td><td class='r'></td><td class='r'>{opening:,.2f}</td><td></td></tr>"]
    for i, (l, bal) in enumerate(rows):
        a = D(l.amount)
        cls = " class='break'" if i + 1 == break_after else ""
        body.append(f"<tr{cls}><td>{be(l.date)}</td><td>{_h.escape(l.text)}</td><td class='r'>{(f'{-a:,.2f}' if a < 0 else '')}</td>"
                    f"<td class='r'>{(f'{a:,.2f}' if a > 0 else '')}</td><td class='r'>{bal:,.2f}</td>"
                    f"<td>{'K BIZ' if 'โอน' in l.text else 'สาขา'}</td></tr>")
    body.append("</tbody></table>")
    _chrome(_page(STATEMENT_CSS, "".join(body)), path)


def write_pdf_letter(path: str) -> None:
    """A PDF with a text layer and no statement in it."""
    from .e2fix.html import _page

    _chrome(_page(STATEMENT_CSS, f"<h1>{BANK} จำกัด (มหาชน)</h1><p>เรียน ผู้ถือบัญชี {ACCOUNT_NAME}</p>"
                                 "<p>ธนาคารขอแจ้งการปรับปรุงระบบบริการธนาคารทางอินเทอร์เน็ตในวันที่ 15 ตุลาคม 2569 "
                                 "ระหว่างเวลา 01:00 น. ถึง 04:00 น. ขออภัยในความไม่สะดวก</p>"), path)


def write_pdf_scan(path: str, of: str) -> None:
    """The statement's first page as a picture wrapped in a PDF: no text layer."""
    from .e2fix import render

    page = render.rasterize(__import__("pathlib").Path(of), 150)[0].convert("L")
    page.save(path, "PDF", resolution=150.0)


# ── what each case must come to ──────────────────────────────────────────

def money(v) -> str:
    return f"{D(v):.2f}"


def row(name: str, n: int) -> str:
    """A row in an answer key: the kit puts the id of the file where its name stands."""
    return "{" + name + "}" + f".0.{n}"


def one(lines: list, **want):
    got = [l for l in lines if all(getattr(l, k) == v for k, v in want.items())]
    assert len(got) == 1, (want, got)
    return got[0]


def truth(book: list, bank: list, *, opening_bank=OPENING, opening_book=OPENING, carried_open=(), posting=D("0"),
          start=dt.date(2026, 9, 1), end=dt.date(2026, 9, 30)) -> dict:
    """The statement by plain addition from what each line truly is."""
    inside = lambda l: start <= l.date <= end   # noqa: E731
    bank_bal = opening_bank + sum((D(l.amount) for l in bank if inside(l)), D(0))
    book_bal = opening_book + sum((D(l.amount) for l in book if inside(l)), D(0))
    adj_bank = bank_bal + sum((D(l.amount) for l in book if inside(l) and l.tag in BANK_SIDE), D(0)) + sum(
        (D(a) for a in carried_open), D(0)) - sum((D(l.amount) for l in bank if inside(l) and l.tag == "bank_error"), D(0))
    adj_book = book_bal + sum((D(l.amount) for l in bank if inside(l) and l.tag in BOOK_SIDE), D(0)) + posting - sum(
        (D(l.amount) for l in book if inside(l) and l.tag == "book_error"), D(0))
    return {"bank_balance": money(bank_bal), "book_balance": money(book_bal), "adjusted_bank": money(adj_bank),
            "adjusted_book": money(adj_book), "difference": money(adj_bank - adj_book), "tied": adj_bank == adj_book}


def main(argv: list | None = None) -> None:
    with_pdf = "--no-pdf" not in (argv or [])          # the PDFs need the host's Chrome; without it the ones on disk are kept
    sample = os.path.join(PKG, "sample-data")
    fx = os.path.join(PKG, "evals", "fixtures")
    os.makedirs(sample, exist_ok=True)
    os.makedirs(fx, exist_ok=True)
    book, bank = september()
    clean = truth(book, bank)
    assert clean["tied"] and clean["adjusted_bank"] == "1538519.65", clean

    # the sample a new install is tested with: it ties and asks nothing
    write_kbank(os.path.join(sample, "statement_2569-09.xlsx"), bank, OPENING)
    write_ledger(os.path.join(sample, "สมุดบัญชีเงินฝาก_2569-09.xlsx"), book, OPENING)

    cases = []

    def case(cid, files, expect, *, note, answers=None, defaults=None, message=""):
        cases.append({"id": cid, "note": note, "files": files, "message": message, "answers": answers or {},
                      "defaults": defaults or {}, "expect": expect})

    items_clean = {"outstanding_cheque": 2, "deposit_in_transit": 1, "bank_fee": 2, "interest": 1, "wht_interest": 1}
    base = {**clean, "gate": "tied", "asked": [], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0}, "by_person": 0,
            "suspect": 0, "items": items_clean, "journal": 3, "review": [], "not_included": [], "period": "2026-09"}

    # 1-5 — the same month in five statement layouts: each ties, none asks
    write_kbank(os.path.join(fx, "kbank_sep.xlsx"), bank, OPENING)
    write_ledger(os.path.join(fx, "ledger_sep.xlsx"), book, OPENING)
    case("clean_kbank_layout", ["kbank_sep.xlsx", "ledger_sep.xlsx"], base,
         note="ถอนเงิน/ฝากเงิน, dates as 01-09-69 (Buddhist era, two digits), cheque numbers only inside the text: the clean month. "
              "Six pairs by the day and amount, four cheques by number and amount presented two or three days later. Interest and "
              "the 1% withheld on it in one drafted entry.")
    write_scb(os.path.join(fx, "scb_sep.xlsx"), bank, OPENING)
    case("newest_row_first", ["scb_sep.xlsx", "ledger_sep.xlsx"], base,
         note="The newest row first, a cheque column, dates as 30/09/2569: the opening balance is the one before the oldest row.")
    write_bbl(os.path.join(fx, "bbl_sep.xlsx"), bank, OPENING)
    case("english_headers_date_cells", ["bbl_sep.xlsx", "ledger_sep.xlsx"], base,
         note="Date / Particulars / Withdrawal / Deposit / Balance, real date cells, BALANCE B/F and C/F lines.")
    write_ktb_csv(os.path.join(fx, "ktb_sep.csv"), bank, OPENING)
    case("csv_cp874_bank_debit_credit", ["ktb_sep.csv", "ledger_sep.xlsx"], base,
         note="A CP874 CSV whose columns are เดบิต/เครดิต. The heading says รายการเดินบัญชี, so a debit is money out — the "
              "opposite of the ledger's debit in the other file.")
    write_signed_csv(os.path.join(fx, "signed_sep.csv"), bank, OPENING)
    case("one_signed_amount_column", ["signed_sep.csv", "ledger_sep.xlsx"], base,
         note="One amount column, a withdrawal written as a negative number.")

    # 6 — a ledger without a running balance: the closing balance is the opening line plus the month
    write_ledger(os.path.join(fx, "ledger_sep_no_balance.xlsx"), book, OPENING, balance=False)
    case("ledger_without_balance_column", ["kbank_sep.xlsx", "ledger_sep_no_balance.xlsx"], base,
         note="The ledger has no balance column; its ยอดยกมา is written in the debit column.")

    # 7 — a general ledger with several accounts
    write_gl(os.path.join(fx, "gl_sep.xlsx"), book, OPENING)
    case("general_ledger_several_accounts", ["kbank_sep.xlsx", "gl_sep.xlsx"], base,
         note="The ledger file is the whole general ledger. One account's name says it is a bank account; its rows are used, "
              "the receivable and the rent account are not.")

    # 8 — THE CASE THAT DOES NOT TIE: a statement with rows missing
    write_kbank(os.path.join(fx, "kbank_sep_page_missing.xlsx"), bank, OPENING, drop=(7, 8))
    kept = [l for i, l in enumerate(sorted(bank, key=lambda x: x.date)) if i not in (7, 8)]
    gone = [l for i, l in enumerate(sorted(bank, key=lambda x: x.date)) if i in (7, 8)]
    assert [g.amount for g in gone] == ["-21400.00", "-35.00"], gone
    # the balance column still counts the rows that are not there; the ledger's payment of 21,400.00 finds no partner and
    # reads as outstanding; the fee of 35.00 is never seen
    t = truth([Bk(**{**b.__dict__, "tag": "outstanding" if b.doc == "PV6909-005" else b.tag}) for b in book], kept,
              opening_bank=OPENING + sum((D(g.amount) for g in gone), D(0)))
    assert t["difference"] == "-21435.00", t
    case("does_not_tie_statement_rows_missing", ["kbank_sep_page_missing.xlsx", "ledger_sep.xlsx"],
         {**t, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท", "asked": [], "matched": 9,
          "tiers": {"exact": 9, "amount_date": 0, "fuzzy": 0}, "by_person": 0, "suspect": 0,
          "items": {"outstanding_cheque": 2, "outstanding_payment": 1, "deposit_in_transit": 1, "bank_fee": 1, "interest": 1,
                    "wht_interest": 1},
          "journal": 2, "review": ["statement_gap"], "not_included": [], "period": "2026-09", "success": False},
         note="Two rows of the statement are not in the file (a page missing) while its running balance still counts them. The "
              "reconciliation must NOT be called a success: it says ยังไม่ลงตัว ต่างกัน 21,435.00 บาท, names the break in the "
              "running balance, and still delivers the working paper marked as a draft.")

    # October: September's open items come in
    oct_book = [
        Bk(2, "RV6910-001", "รับชำระ บจก.ไทยเทรดดิ้ง", "96300.00", month=10),
        Bk(5, "PV6910-001", "จ่ายค่าเช่าสำนักงาน", "-45000.00", chq="10001207", month=10),
        Bk(9, "PV6910-002", "โอนจ่ายเงินเดือนพนักงาน", "-291750.00", month=10),
        Bk(16, "RV6910-002", "รับชำระ บจก.เอเชีย คลาวด์ โซลูชั่นส์", "149800.00", month=10),
        Bk(27, "PV6910-003", "จ่ายค่าโฆษณา", "-26750.00", chq="10001208", month=10, tag="outstanding"),
    ]
    oct_bank = [
        Bn(1, "ฝากเช็ค บจก.นอร์ทสตาร์", "40660.00", month=10),                       # September's deposit in transit
        Bn(2, "เช็ค 10001205", "-9630.00", chq="10001205", month=10),              # September's outstanding cheque
        Bn(2, "โอนเงินเข้า จาก บจก.ไทยเทรดดิ้ง", "96300.00", month=10),
        Bn(7, "เช็ค 10001207", "-45000.00", chq="10001207", month=10),
        Bn(9, "โอนเงินออก จ่ายเงินเดือน", "-291750.00", month=10),
        Bn(16, "โอนเงินเข้า จาก บจก.เอเชีย คลาวด์", "149800.00", month=10),
        Bn(31, "ค่าธรรมเนียมรักษาบัญชี", "-200.00", month=10, tag="fee"),
    ]
    # the books carry September's drafted entries (fees, interest, tax) as posted: both open at the adjusted balance less
    # what the bank had not shown
    open_book = D(clean["adjusted_book"])
    open_bank = D(clean["bank_balance"])
    carried = [(dt.date(2026, 9, 28), "10001205", "จ่ายค่าบริการทำความสะอาด", "9630.00", "เช็คค้างจ่าย"),
               (dt.date(2026, 9, 29), "10001206", "จ่ายค่าที่ปรึกษากฎหมาย", "32100.00", "เช็คค้างจ่าย"),
               (dt.date(2026, 9, 30), "RV6909-005", "นำฝากเช็ครับ บจก.นอร์ทสตาร์", "40660.00", "เงินฝากระหว่างทาง")]
    at_bank = write_kbank(os.path.join(fx, "kbank_oct.xlsx"), oct_bank, open_bank)
    write_ledger(os.path.join(fx, "ledger_oct.xlsx"), oct_book, open_book, month_label="ตุลาคม 2569")
    at_carried = write_carried(os.path.join(fx, "carried_from_sep.xlsx"), carried)
    deposit = row("carried_from_sep.xlsx", at_carried[2]) + "~" + row("kbank_oct.xlsx", at_bank[id(one(oct_bank, amount="40660.00"))])
    span = {"start": dt.date(2026, 10, 1), "end": dt.date(2026, 10, 31)}
    t = truth(oct_book, oct_bank, opening_bank=open_bank, opening_book=open_book, carried_open=["-32100.00"], **span)
    assert t["tied"], t
    case("items_carried_in_and_cleared", ["kbank_oct.xlsx", "ledger_oct.xlsx", "carried_from_sep.xlsx"],
         {**t, "gate": "tied", "asked": ["e3_candidate"], "matched": 6, "tiers": {"exact": 5, "amount_date": 1, "fuzzy": 0},
          "by_person": 1, "suspect": 0, "items": {"outstanding_cheque": 2, "bank_fee": 1}, "journal": 1, "review": [],
          "not_included": [], "period": "2026-10", "carried_cleared": 2, "carried_open": 1},
         answers={f"e3_candidate:{deposit}": "match"},
         note="October. Three items come in from September's list: the cheque 10001205 is presented (by number and amount, a "
              "rule), the deposit of 40,660.00 reaches the bank a day later under another text (a person confirms it), the "
              "cheque 10001206 is still outstanding and is carried on.")
    t = truth(oct_book, oct_bank, opening_bank=open_bank, opening_book=open_book, **span)
    # without the list: the cheque and the deposit the bank shows are taken for things the books never recorded
    t9 = truth(oct_book, [Bn(**{**b.__dict__, "tag": "unrecorded" if b.text in ("ฝากเช็ค บจก.นอร์ทสตาร์", "เช็ค 10001205") else b.tag})
                          for b in oct_bank], opening_bank=open_bank, opening_book=open_book, **span)
    assert t9["difference"] == "1070.00", t9
    case("does_not_tie_opening_unexplained", ["kbank_oct.xlsx", "ledger_oct.xlsx"],
         {**t9, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 1,070.00 บาท", "asked": ["bankrec_carried"], "matched": 4,
          "tiers": {"exact": 4, "amount_date": 0, "fuzzy": 0}, "by_person": 0, "suspect": 0,
          "items": {"outstanding_cheque": 1, "bank_fee": 1, "unrecorded_receipt": 1, "unrecorded_payment": 1}, "journal": 3,
          "review": ["opening_unexplained"], "not_included": [], "period": "2026-10", "success": False},
         answers={"bankrec_carried": "proceed_without"},
         note="October without September's list. The opening balances differ by 1,070.00 and nothing explains it; the person "
              "says there is no list. Not a success: ยังไม่ลงตัว ต่างกัน 1,070.00 บาท, and the next step asks for the list.")

    # candidates: a person confirms
    cand_book = [Bk(**b.__dict__) for b in book] + [
        Bk(11, "PV6909-009", "โอนจ่ายค่าขนส่ง บจก.ด่วนทั่วไทย", "-6420.00"),
        Bk(19, "PV6909-010", "โอนจ่ายค่าอินเทอร์เน็ต", "-1819.00"),
    ]
    cand_bank = [Bn(**b.__dict__) for b in bank] + [
        Bn(13, "โอนเงินออก", "-6420.00"),                        # two days later, the same amount
        Bn(19, "โอนเงินออก", "-1819.50"),                        # the same day, fifty satang more
    ]
    t = truth(cand_book, cand_bank, posting=D("-0.50"))
    assert t["tied"], t
    at_bank = write_kbank(os.path.join(fx, "kbank_sep_candidates.xlsx"), cand_bank, OPENING)
    at_book = write_ledger(os.path.join(fx, "ledger_sep_candidates.xlsx"), cand_book, OPENING)
    pair_a = (row("ledger_sep_candidates.xlsx", at_book[id(one(cand_book, doc="PV6909-009"))]) + "~"
              + row("kbank_sep_candidates.xlsx", at_bank[id(one(cand_bank, amount="-6420.00"))]))
    pair_b = (row("ledger_sep_candidates.xlsx", at_book[id(one(cand_book, doc="PV6909-010"))]) + "~"
              + row("kbank_sep_candidates.xlsx", at_bank[id(one(cand_bank, amount="-1819.50"))]))
    case("candidates_confirmed_by_a_person", ["kbank_sep_candidates.xlsx", "ledger_sep_candidates.xlsx"],
         {**t, "gate": "tied", "asked": ["e3_candidate", "e3_candidate"], "matched": 12,
          "tiers": {"exact": 10, "amount_date": 2, "fuzzy": 0}, "by_person": 2, "suspect": 0,
          "items": {**items_clean, "posting_error": 1}, "journal": 4, "review": [], "not_included": [], "period": "2026-09"},
         answers={f"e3_candidate:{pair_a}": "match", f"e3_candidate:{pair_b}": "match"},
         note="Two payments the rules will not match by themselves: one reaches the bank two days later, one differs by fifty "
              "satang. Each is a question; the person confirms both. The pairs record the tier and that a person confirmed; "
              "the fifty satang become a drafted entry.")
    ts = truth(cand_book, [Bn(**{**b.__dict__, "tag": "unknown" if b.amount in ("-6420.00", "-1819.50") else b.tag}) for b in cand_bank])
    case("candidates_skipped_stay_suspect", ["kbank_sep_candidates.xlsx", "ledger_sep_candidates.xlsx"],
         {"bank_balance": ts["bank_balance"], "book_balance": ts["book_balance"], "adjusted_bank": clean["adjusted_bank"] and
          money(D(ts["bank_balance"]) + D("40660.00") - D("9630.00") - D("32100.00")),
          "adjusted_book": money(D(ts["book_balance"]) + D("1250.00") - D("12.50") - D("235.00")),
          "difference": "-0.50", "tied": False, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 0.50 บาท",
          "asked": ["e3_candidate", "e3_candidate"], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0},
          "by_person": 0, "suspect": 2, "items": items_clean, "journal": 3, "review": ["match_unconfirmed", "match_unconfirmed"],
          "not_included": [], "period": "2026-09", "success": False},
         note="The same files, both cards skipped. A skipped card never matches a pair: the four rows stay suspect, in neither "
              "adjusted balance, each pair an exception for a person — and the fifty satang show as the difference.")

    # a large row without a partner
    big_bank = [Bn(**b.__dict__) for b in bank] + [Bn(24, "เงินโอนเข้า", "75000.00", tag="unknown")]
    at_bank = write_kbank(os.path.join(fx, "kbank_sep_large.xlsx"), big_bank, OPENING)
    large = row("kbank_sep_large.xlsx", at_bank[id(one(big_bank, amount="75000.00"))])
    tu = truth(book, big_bank)
    case("large_row_without_partner_skipped", ["kbank_sep_large.xlsx", "ledger_sep.xlsx"],
         {**tu, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 75,000.00 บาท", "asked": ["e3_unmatched"], "matched": 10,
          "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0}, "by_person": 0, "suspect": 0,
          "items": {**items_clean, "unclassified": 1}, "journal": 3, "review": ["unclassified_item"], "not_included": [],
          "period": "2026-09", "success": False},
         note="75,000.00 came into the account and the ledger does not have it. From 50,000.00 up a person is asked what a row "
              "is; the card is skipped, so nobody has said — the row is in neither adjusted balance and the round does not tie.")
    tk = truth(book, [Bn(**{**b.__dict__, "tag": "unrecorded" if b.amount == "75000.00" else b.tag}) for b in big_bank])
    assert tk["tied"], tk
    case("large_row_without_partner_answered", ["kbank_sep_large.xlsx", "ledger_sep.xlsx"],
         {**tk, "gate": "tied", "asked": ["e3_unmatched"], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0},
          "by_person": 0, "suspect": 0, "items": {**items_clean, "unrecorded_receipt": 1}, "journal": 4, "review": [],
          "not_included": [], "period": "2026-09"},
         answers={f"e3_unmatched:{large}": "book"},
         note="The same files; the person says the receipt is to be recorded. It becomes a book-side item with a drafted entry "
              "through a suspense account, and the reconciliation ties.")

    # three cheques deposited as one line
    grp_book = [Bk(**b.__dict__) for b in book if b.doc != "RV6909-003"] + [
        Bk(15, "RV6909-003A", "รับเช็ค บจก.ไทยเทรดดิ้ง", "20000.00"),
        Bk(15, "RV6909-003B", "รับเช็ค หจก.รุ่งเรืองพาณิชย์", "26850.00"),
        Bk(16, "RV6909-003C", "รับเช็ค ร้านมิตรไมตรี", "12000.00"),
    ]
    grp_bank = [Bn(**{**b.__dict__, "text": "ฝากเช็ค 3 ฉบับ", "day": 16}) if b.amount == "58850.00" else Bn(**b.__dict__) for b in bank]
    t = truth(grp_book, grp_bank)
    assert t["tied"], t
    at_bank = write_kbank(os.path.join(fx, "kbank_sep_group.xlsx"), grp_bank, OPENING)
    at_book = write_ledger(os.path.join(fx, "ledger_sep_group.xlsx"), grp_book, OPENING)
    grp = ("+".join(row("ledger_sep_group.xlsx", at_book[id(one(grp_book, doc=d))]) for d in ("RV6909-003A", "RV6909-003B", "RV6909-003C"))
           + "~" + row("kbank_sep_group.xlsx", at_bank[id(one(grp_bank, amount="58850.00"))]))
    case("three_cheques_one_deposit", ["kbank_sep_group.xlsx", "ledger_sep_group.xlsx"],
         {**t, "gate": "tied", "asked": ["e3_candidate"], "matched": 10, "tiers": {"exact": 9, "amount_date": 1, "fuzzy": 0},
          "by_person": 1, "suspect": 0, "items": items_clean, "journal": 3, "review": [], "not_included": [], "period": "2026-09",
          "shapes": {"N:1": 1}},
         answers={f"e3_candidate:{grp}": "match"},
         note="Three cheques recorded one by one, deposited as one line of 58,850.00: many rows to one, proposed and confirmed.")

    # a file that does not say whose it is
    write_bare(os.path.join(fx, "bare_sep.xlsx"), book, OPENING)
    bare = truth([Bk(**b.__dict__) for b in book], bank)
    case("file_that_does_not_say_whose_it_is", ["kbank_sep.xlsx", "bare_sep.xlsx"],
         {**bare, "gate": "tied", "asked": ["bankrec_side", "e3_confirm_all"], "matched": 10,
          "tiers": {"exact": 6, "amount_date": 4, "fuzzy": 0}, "by_person": 4, "suspect": 0,
          "items": {"outstanding_payment": 2, "deposit_in_transit": 1, "bank_fee": 2, "interest": 1, "wht_interest": 1},
          "journal": 3, "review": [], "not_included": [], "period": "2026-09"},
         answers={"bankrec_side:{bare_sep.xlsx:key}": "book", "e3_confirm_all": "match"},
         note="เดบิต/เครดิต and nothing else. The agent does not guess which side wrote it (the signs are opposite): it asks, "
              "and the person says it is the ledger of the company. This ledger has no cheque numbers, so the four cheques "
              "cannot be matched by a rule: they are proposed together and confirmed by one answer that names each pair.")
    case("file_set_aside_waits_for_the_ledger", ["kbank_sep.xlsx", "bare_sep.xlsx"],
         {"asked": ["bankrec_side", "missing_file"], "stops_at": "missing_file"},
         note="The same card skipped: the file is set aside, never read as one side or the other, and the agent asks for the "
              "ledger. Nothing is reconciled from a guess.")
    case("file_set_aside_is_on_the_list", ["kbank_sep.xlsx", "ledger_sep.xlsx", "bare_sep.xlsx"],
         {**base, "asked": ["bankrec_side"], "not_included": ["bare_sep.xlsx"], "can_restore": ["bare_sep.xlsx"]},
         note="Three files; the one that does not say whose it is is skipped. The round is reconciled from the other two, and "
              "the file is on the list of what the round did not include, with what bringing it back takes.")

    # the same cheque on the other side of the cut-off
    cut_book = [Bk(**b.__dict__) for b in book if b.doc != "PV6909-006"] + [
        Bk(2, "PV6910-000", "จ่ายค่าซ่อมบำรุงเครื่องปรับอากาศ", "-17655.00", chq="10001204", month=10, tag="outside")]
    cut_bank = [Bn(**{**b.__dict__, "tag": "unrecorded"}) if b.chq == "10001204" else Bn(**b.__dict__) for b in bank]
    t = truth(cut_book, cut_bank)
    assert t["tied"], t
    write_ledger(os.path.join(fx, "ledger_sep_cutoff.xlsx"), cut_book, OPENING)
    case("recorded_after_the_cut_off", ["kbank_sep.xlsx", "ledger_sep_cutoff.xlsx"],
         {**t, "gate": "tied", "asked": [], "matched": 9, "tiers": {"exact": 9, "amount_date": 0, "fuzzy": 0}, "by_person": 0,
          "suspect": 0, "items": {**items_clean, "unrecorded_payment": 1}, "journal": 4, "review": ["cut_off"], "not_included": [],
          "period": "2026-09", "outside_period": 1},
         note="The bank paid cheque 10001204 on 28 September; the ledger records it on 2 October. The October row is matched with "
              "nothing (cut-off); the September payment is a book-side item; the pair across the cut-off is an exception.")

    # a cheque older than six months among the items carried in
    old = [(dt.date(2026, 2, 12), "10000977", "จ่ายค่าอบรมพนักงาน", "4280.00", "เช็คค้างจ่าย")]
    write_carried(os.path.join(fx, "carried_stale.xlsx"), old)
    t = truth(book, bank, opening_book=OPENING - D("4280.00"), carried_open=["-4280.00"])
    assert t["tied"], t
    write_ledger(os.path.join(fx, "ledger_sep_stale.xlsx"), book, OPENING - D("4280.00"))
    case("stale_cheque_carried_in", ["kbank_sep.xlsx", "ledger_sep_stale.xlsx", "carried_stale.xlsx"],
         {**t, "gate": "tied", "asked": [], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0}, "by_person": 0,
          "suspect": 0, "items": {**items_clean, "outstanding_cheque": 3}, "journal": 3, "review": ["stale_cheque"],
          "not_included": [], "period": "2026-09", "stale": 1, "carried_cleared": 0, "carried_open": 1},
         note="A cheque of 12 February is still not presented at 30 September: 230 days, stale. It still reconciles (it is "
              "outstanding), and it is an exception: cancel or reissue.")

    # the same payment recorded twice
    dup_book = [Bk(**b.__dict__) for b in book] + [Bk(3, "PV6909-002", "จ่ายค่าวัสดุสำนักงาน ร้านเครื่องเขียนสมใจ", "-8560.00",
                                                     chq="10001202", tag="duplicate")]
    t = truth(dup_book, bank)
    assert t["difference"] == "8560.00", t
    write_ledger(os.path.join(fx, "ledger_sep_twice.xlsx"), dup_book, OPENING)
    case("does_not_tie_recorded_twice", ["kbank_sep.xlsx", "ledger_sep_twice.xlsx"],
         {**t, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 8,560.00 บาท", "asked": [], "matched": 10,
          "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0}, "by_person": 0, "suspect": 1, "items": items_clean, "journal": 3,
          "review": ["possible_duplicate"], "not_included": [], "period": "2026-09", "success": False},
         note="Cheque 10001202 is in the ledger twice; the bank paid it once. The second row is not taken for an outstanding "
              "cheque: it is suspect, and the round does not tie by its amount.")

    # the install's own tolerance
    tol_book = [Bk(**b.__dict__) for b in book] + [Bk(6, "PV6909-011", "โอนจ่ายค่าประกันภัย", "-14980.00")]
    tol_bank = [Bn(**b.__dict__) for b in bank] + [Bn(11, "โอนเงินออก", "-14980.00")]          # five days later
    at_bank = write_kbank(os.path.join(fx, "kbank_sep_window.xlsx"), tol_bank, OPENING)
    at_book = write_ledger(os.path.join(fx, "ledger_sep_window.xlsx"), tol_book, OPENING)
    late = (row("ledger_sep_window.xlsx", at_book[id(one(tol_book, doc="PV6909-011"))]) + "~"
            + row("kbank_sep_window.xlsx", at_bank[id(one(tol_bank, amount="-14980.00"))]))
    t = truth([Bk(**{**b.__dict__, "tag": "outstanding" if b.doc == "PV6909-011" else b.tag}) for b in tol_book],
              [Bn(**{**b.__dict__, "tag": "unrecorded" if b.amount == "-14980.00" else b.tag}) for b in tol_bank])
    assert t["tied"], t
    case("outside_the_default_window", ["kbank_sep_window.xlsx", "ledger_sep_window.xlsx"],
         {**t, "gate": "tied", "asked": [], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0}, "by_person": 0,
          "suspect": 0, "items": {**items_clean, "outstanding_payment": 1, "unrecorded_payment": 1}, "journal": 4, "review": [],
          "not_included": [], "period": "2026-09"},
         note="A payment reaches the bank five days after the ledger's date. The default window is three days: the two rows are "
              "not offered as a pair; each is a reconciling item on its own side.")
    t = truth(tol_book, tol_bank)
    assert t["tied"], t
    case("the_installs_own_window", ["kbank_sep_window.xlsx", "ledger_sep_window.xlsx"],
         {**t, "gate": "tied", "asked": ["e3_candidate"], "matched": 11, "tiers": {"exact": 10, "amount_date": 1, "fuzzy": 0},
          "by_person": 1, "suspect": 0, "items": items_clean, "journal": 3, "review": [], "not_included": [], "period": "2026-09",
          "window": 7},
         defaults={"match_date_window_days": 7},
         answers={f"e3_candidate:{late}": "match"},
         note="The same files with the install's window set to seven days: the pair is proposed, a person confirms it.")

    # the fuzzy tier, forced (AM6): a name written another way, an amount short of a fee
    fz_book = [Bk(**b.__dict__) for b in book] + [
        Bk(9, "RV6909-006", "รับชำระ บจก.สยามพรีซิชั่นพาร์ท", "74900.00"),
        Bk(11, "PV6909-012", "จ่ายค่าขนส่งสินค้า", "-30000.00", chq="10001209"),
    ]
    fz_bank = [Bn(**b.__dict__) for b in bank] + [
        Bn(19, "โอนเงินเข้า จาก สยามพรีซิชั่น พาร์ทส์", "74900.00"),          # ten days later, the name written another way
        Bn(14, "เช็ค 10001209 รวมค่าธรรมเนียมเรียกเก็บ", "-30015.00", chq="10001209"),   # the cheque, and the bank's fee in one line
    ]
    t = truth(fz_book, fz_bank, posting=D("-15.00"))
    assert t["tied"], t
    at_bank = write_kbank(os.path.join(fx, "kbank_sep_fuzzy.xlsx"), fz_bank, OPENING)
    at_book = write_ledger(os.path.join(fx, "ledger_sep_fuzzy.xlsx"), fz_book, OPENING)
    name_pair = (row("ledger_sep_fuzzy.xlsx", at_book[id(one(fz_book, doc="RV6909-006"))]) + "~"
                 + row("kbank_sep_fuzzy.xlsx", at_bank[id(one(fz_bank, amount="74900.00"))]))
    fee_pair = (row("ledger_sep_fuzzy.xlsx", at_book[id(one(fz_book, doc="PV6909-012"))]) + "~"
                + row("kbank_sep_fuzzy.xlsx", at_bank[id(one(fz_bank, amount="-30015.00"))]))
    case("fuzzy_a_name_written_another_way_and_an_amount_short_of_a_fee", ["kbank_sep_fuzzy.xlsx", "ledger_sep_fuzzy.xlsx"],
         {**t, "gate": "tied", "asked": ["e3_candidate", "e3_candidate"], "matched": 12,
          "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 2}, "by_person": 2, "suspect": 0,
          "items": {**items_clean, "posting_error": 1}, "journal": 4, "review": [], "not_included": [], "period": "2026-09",
          "passes": {"fuzzy_name": 1, "ref_only": 1}},
         answers={f"e3_candidate:{name_pair}": "match", f"e3_candidate:{fee_pair}": "match"},
         note="Two pairs only the fuzzy tier finds. A customer's transfer reaches the bank ten days after the ledger's date, its "
              "name written another way (พาร์ท / พาร์ทส์): outside the date window, the names resemble. A cheque is presented with "
              "the bank's fee in the same line: the cheque number is the same, the amount is 15.00 more. Each is a question; the "
              "person confirms both, and the 15.00 becomes a drafted entry.")
    tf = truth([Bk(**{**b.__dict__, "tag": "unknown" if b.doc in ("RV6909-006", "PV6909-012") else b.tag}) for b in fz_book],
               [Bn(**{**b.__dict__, "tag": "unknown" if b.amount in ("74900.00", "-30015.00") else b.tag}) for b in fz_bank])
    case("fuzzy_pairs_nobody_confirms", ["kbank_sep_fuzzy.xlsx", "ledger_sep_fuzzy.xlsx"],
         {"bank_balance": tf["bank_balance"], "book_balance": tf["book_balance"],
          "adjusted_bank": money(D(tf["bank_balance"]) + D("40660.00") - D("9630.00") - D("32100.00")),
          "adjusted_book": money(D(tf["book_balance"]) + D("1250.00") - D("12.50") - D("235.00")),
          "difference": "-15.00", "tied": False, "gate": "not_tied", "what": "ยังไม่ลงตัว ต่างกัน 15.00 บาท",
          "asked": ["e3_candidate", "e3_candidate"], "matched": 10, "tiers": {"exact": 10, "amount_date": 0, "fuzzy": 0},
          "by_person": 0, "suspect": 2, "items": items_clean, "journal": 3, "review": ["match_unconfirmed", "match_unconfirmed"],
          "not_included": [], "period": "2026-09", "success": False},
         note="The same files, both cards skipped: a fuzzy pair is never matched by default. Four rows stay suspect, in neither "
              "adjusted balance, and the round does not tie by the 15.00 nobody explained.")

    # a statement as the PDF the bank issued (AM6)
    if with_pdf:
        write_pdf_statement(os.path.join(fx, "statement_sep.pdf"), bank, OPENING)
        write_pdf_letter(os.path.join(fx, "letter_from_the_bank.pdf"))
        write_pdf_scan(os.path.join(fx, "statement_sep_scan.pdf"), os.path.join(fx, "statement_sep.pdf"))
    write_pdf_stub(os.path.join(fx, "broken.pdf"))
    case("statement_as_a_pdf", ["statement_sep.pdf", "ledger_sep.xlsx"], {**base, "from_pdf": True},
         note="The same month as the PDF a bank's portal hands out: two pages, the table's header printed on both, a text "
              "layer that lost some Thai marks. Read from where its words stand, it is the same statement: ten pairs, seven "
              "reconciling items, it ties — and every row is cited by its page and line.")
    case("pdf_that_is_a_scan_is_not_read_and_says_so", ["statement_sep_scan.pdf", "kbank_sep.xlsx", "ledger_sep.xlsx"],
         {**base, "not_included": ["statement_sep_scan.pdf"], "not_included_code": "scanned_pdf"},
         note="A scan of the statement beside the two files: it has no text layer, nothing is read from it by guesswork, the "
              "round says which file it did not use and why.")
    case("pdf_that_is_no_statement", ["letter_from_the_bank.pdf", "kbank_sep.xlsx", "ledger_sep.xlsx"],
         {**base, "not_included": ["letter_from_the_bank.pdf"], "not_included_code": "unrecognised_sheet"},
         note="A letter from the bank as a PDF: it has words and no statement table.")
    case("pdf_that_cannot_be_opened", ["broken.pdf", "kbank_sep.xlsx", "ledger_sep.xlsx"],
         {**base, "not_included": ["broken.pdf"], "not_included_code": "unreadable"},
         note="A file that only calls itself a PDF.")
    case("statement_only_as_a_scan_asks_for_the_statement", ["statement_sep_scan.pdf", "ledger_sep.xlsx"],
         {"asked": ["missing_file"], "stops_at": "missing_file"},
         note="Only the scan and the ledger: there is no statement to reconcile with. The round asks for it and waits.")

    with open(os.path.join(PKG, "evals", "cases.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({
            "schema": "agent.market.eval.v1", "package": "sran.bank-reconciliation",
            "note": "Files are in evals/fixtures, written by tools/make_bank_fixtures.py. Every expected value comes from the "
                    "scenario (what each line truly is) and plain addition in that generator, never from this package. "
                    "Answers: a key names a row as {file}.sheet.row; unanswered cards take their default, as a skipped card "
                    "does. Checked on every case, whatever it says: every row of both files is in exactly one of the four "
                    "groups; every pair below the exact tier was confirmed by a person; a round whose adjusted balances "
                    "differ is never a success; every drafted entry balances; the working paper passed its own quality gate; "
                    "the same input gives the same computed.json, byte for byte.",
            "defaults": {"company": COMPANY, "bank_name": BANK, "bank_account_number": ACCOUNT_NO, "bank_account_name": ACCOUNT_NAME},
            "today": "2026-11-02",
            "cases": cases,
        }, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{len(cases)} cases · fixtures in {fx} · sample data in {sample}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
