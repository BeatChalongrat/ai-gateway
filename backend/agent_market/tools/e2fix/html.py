"""HTML for the fixture documents — six layouts the way Thai invoices come.

modern   (cloud accounting style, Sarabun)      classic  (bordered Express style, Tahoma)
service  (contractor invoice with WHT, Angsana)  pos      (80 mm thermal, abbreviated tax invoice)
receipt  (hand-written style receipt, no table) english  (foreign supplier, USD)
Printed to PDF by headless Chrome (render.py), which shapes Thai correctly and
writes a real text layer — as the PDFs of the accounting systems do.
"""

from __future__ import annotations

import html as _h
from pathlib import Path

from .thai import bahttext, fmt_date, fmt_tax_id, money, thai_digits

FONT_DIR = (Path(__file__).resolve().parents[4] / "services" / "chat-edge-api" / "scripts" / "fonts").as_posix()

FONT_FACE = f"""
@font-face {{ font-family: 'Sarabun'; src: url('file:///{FONT_DIR}/Sarabun-Regular.ttf'); font-weight: 400; }}
@font-face {{ font-family: 'Sarabun'; src: url('file:///{FONT_DIR}/Sarabun-Bold.ttf'); font-weight: 700; }}
"""


def e(s) -> str:
    return _h.escape(str(s))


def _num(s: str, spec) -> str:
    return thai_digits(s) if spec.get("thai_digits") else s


def _m(v, spec) -> str:
    return money(v, thai=bool(spec.get("thai_digits")))


def _qty(q, spec) -> str:
    s = f"{q:,.0f}" if q == q.to_integral_value() else f"{q:,.2f}"
    return _num(s, spec)


def _tax(tid: str, spec) -> str:
    return fmt_tax_id(tid, spec.get("tax_style", "plain"))


def _branch(code, spec) -> str:
    if code is None:
        return ""
    if code == "00000":
        return "สำนักงานใหญ่"
    return _num(f"สาขาที่ {code}", spec)


def seller_tax_printed(spec, seller) -> str | None:
    """The seller tax ID as it appears on the page (None when not printed)."""
    if spec.get("omit_seller_tax_id") or not seller.get("tax_id"):
        return None
    tid = seller["tax_id"]
    if spec.get("corrupt_seller_tax_id"):
        tid = tid[:12] + str((int(tid[12]) + 3) % 10)
    return tid


def _page(css: str, body: str, *, size: str = "A4", margin: str = "12mm") -> str:
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><style>
{FONT_FACE}
@page {{ size: {size}; margin: {margin}; }}
html, body {{ margin: 0; padding: 0; }}
{css}
</style></head><body>{body}</body></html>"""


# ── modern ──────────────────────────────────────────────────────────────────

MODERN_CSS = """
body { font-family: 'Sarabun', sans-serif; font-size: 10.5pt; color: #222; }
.page { position: relative; }
.pb { page-break-after: always; }
.top { display: flex; justify-content: space-between; align-items: flex-start; }
.logo { width: 54px; height: 54px; background: #1f6f8b; color: #fff; font-weight: 700; font-size: 18pt;
        display: flex; align-items: center; justify-content: center; border-radius: 6px; margin-right: 12px; }
.seller { display: flex; }
.seller .name { font-size: 13pt; font-weight: 700; }
.title { text-align: right; }
.title h1 { font-size: 16pt; margin: 0; color: #1f6f8b; white-space: nowrap; }
.seller { max-width: 58%; }
.title .en { font-size: 10pt; color: #555; letter-spacing: 1px; }
.title .orig { display: inline-block; margin-top: 4px; border: 1px solid #1f6f8b; color: #1f6f8b; padding: 1px 10px; }
.meta { margin-top: 6px; border: 1px solid #cfd8dc; border-radius: 4px; padding: 4px 8px; text-align: left; }
.meta td { padding: 1px 6px; }
.cust { margin-top: 14px; border: 1px solid #cfd8dc; border-radius: 4px; padding: 6px 10px; }
.cust .lbl { color: #1f6f8b; font-weight: 700; }
table.items { width: 100%; border-collapse: collapse; margin-top: 12px; }
table.items th { background: #1f6f8b; color: #fff; font-weight: 700; padding: 5px 6px; font-size: 10pt; }
table.items td { padding: 4px 6px; border-bottom: 1px solid #e3e8ea; }
td.r, th.r { text-align: right; } td.c, th.c { text-align: center; }
.bottom { display: flex; justify-content: space-between; margin-top: 12px; }
.words { width: 55%; padding: 6px 10px; background: #f3f7f8; border-radius: 4px; }
table.tot { width: 40%; border-collapse: collapse; }
table.tot td { padding: 3px 6px; }
table.tot tr.grand td { font-weight: 700; font-size: 12pt; border-top: 2px solid #1f6f8b; }
.notes { margin-top: 12px; font-size: 9.5pt; color: #444; }
.sign { display: flex; justify-content: space-around; margin-top: 36px; text-align: center; }
.sign div { width: 30%; border-top: 1px dotted #777; padding-top: 4px; }
.pageno { text-align: right; font-size: 9pt; color: #777; margin-bottom: 2px; }
.wm { position: fixed; top: 38%; left: 18%; font-size: 90pt; color: rgba(200,0,0,0.12); transform: rotate(-28deg); }
.etax { margin-top: 6px; font-size: 9pt; color: #0b7a3b; border: 1px dashed #0b7a3b; padding: 3px 8px; }
.cont { text-align: right; color: #777; font-size: 9.5pt; margin-top: 6px; }
"""


def _modern_header(spec, seller, page=None, pages=None) -> str:
    title = e(spec["title"])
    mark = spec.get("copy_mark") or "ต้นฉบับ"
    tid = seller_tax_printed(spec, seller)
    tax_line = f"เลขประจำตัวผู้เสียภาษีอากร {e(_num(_tax(tid, spec), spec) if spec.get('thai_digits') else _tax(tid, spec))} &nbsp; {e(_branch(seller['branch'], spec))}" if tid else e(_branch(seller["branch"], spec))
    due = f"<tr><td>ครบกำหนด</td><td>{e(fmt_date(spec['due'], spec['date_style']))}</td></tr>" if spec.get("due") else ""
    number = spec["number"]
    pageno = f'<div class="pageno">หน้า {e(_num(f"{page}/{pages}", spec))}</div>' if pages and pages > 1 else ""
    initials = seller["core"][:2]
    return f"""
{pageno}<div class="top">
  <div class="seller"><div class="logo">{e(initials)}</div><div>
    <div class="name">{e(seller['print_name'])}</div>
    <div>{e(_num(seller['address'], spec))}</div>
    <div>โทร {e(_num(seller['tel'], spec))}</div>
    <div>{tax_line}</div></div></div>
  <div class="title"><h1>{title}</h1><div class="en">{e(spec.get('title_en', ''))}</div>
    <div class="orig">{e(mark)}</div>
    <table class="meta"><tr><td>เลขที่</td><td>{e(number)}</td></tr>
      <tr><td>วันที่</td><td>{e(fmt_date(spec['date'], spec['date_style']))}</td></tr>{due}</table></div>
</div>"""


def _cust_block(spec, buyer) -> str:
    if not buyer:
        return ""
    return f"""<div class="cust"><span class="lbl">ลูกค้า / Customer</span><br>
  {e(buyer['name'])}<br>{e(_num(buyer['address'], spec))}<br>
  เลขประจำตัวผู้เสียภาษีอากร {e(_num(_tax(buyer['tax_id'], spec), spec) if spec.get('thai_digits') else _tax(buyer['tax_id'], spec))} &nbsp; {e(_branch(buyer['branch'], spec))}</div>"""


def _modern_rows(spec, lines, start) -> str:
    rows = []
    for i, l in enumerate(lines, start=start):
        rows.append(f"<tr><td class='c'>{e(_num(str(i), spec))}</td><td>{e(l['code'])}</td><td>{e(l['description'])}</td>"
                    f"<td class='r'>{e(_qty(l['quantity'], spec))}</td><td class='c'>{e(l['unit'])}</td>"
                    f"<td class='r'>{e(_m(l['unit_price'], spec))}</td><td class='r'>{e(_m(l['amount'], spec))}</td></tr>")
    return "".join(rows)


def _modern_totals(spec) -> str:
    t = spec["totals"]
    rows = [("รวมเป็นเงิน", t["sum_lines"])]
    if t["discount"]:
        rows += [("ส่วนลด", t["discount"]), ("มูลค่าหลังหักส่วนลด", t["subtotal"])]
    if t["vat_mode"] == "exclusive":
        rows.append((f"ภาษีมูลค่าเพิ่ม {_num('7', spec)}%", t["vat_amount"]))
    body = "".join(f"<tr><td>{e(k)}</td><td class='r'>{e(_m(v, spec))}</td></tr>" for k, v in rows)
    body += f"<tr class='grand'><td>จำนวนเงินรวมทั้งสิ้น</td><td class='r'>{e(_m(t['total'], spec))}</td></tr>"
    if t.get("wht_amount") is not None:
        body += (f"<tr><td>หักภาษี ณ ที่จ่าย {_num(str(t['wht_rate']), spec)}%</td><td class='r'>{e(_m(t['wht_amount'], spec))}</td></tr>"
                 f"<tr class='grand'><td>ยอดชำระสุทธิ</td><td class='r'>{e(_m(t['net_payable'], spec))}</td></tr>")
    return f"<table class='tot'>{body}</table>"


def modern(spec, seller, buyer) -> str:
    head = ("<tr><th class='c'>ลำดับ</th><th>รหัส</th><th>รายการ</th><th class='r'>จำนวน</th><th class='c'>หน่วย</th>"
            "<th class='r'>ราคาต่อหน่วย</th><th class='r'>จำนวนเงิน</th></tr>")
    lines = spec["lines"]
    pages = spec.get("pages", 1)
    split = spec.get("rows_page1", len(lines))
    chunks = [lines[:split], lines[split:]] if pages == 2 else [lines]
    wm = f'<div class="wm">{e(spec["copy_mark"])}</div>' if spec.get("copy_mark") else ""
    etax = ('<div class="etax">e-Tax Invoice — เอกสารนี้ได้จัดทำและนำส่งข้อมูลให้แก่กรมสรรพากรด้วยวิธีการทางอิเล็กทรอนิกส์ '
            '(ไฟล์ XML ตามมาตรฐาน ขมธอ. 3-2560 แนบอยู่ในไฟล์ PDF นี้)</div>') if spec.get("etax") else ""
    out = []
    start = 1
    for pi, chunk in enumerate(chunks, start=1):
        last = pi == len(chunks)
        body = _modern_header(spec, seller, pi, len(chunks)) + etax
        if pi == 1:
            body += _cust_block(spec, buyer)
        body += f"<table class='items'>{head}{_modern_rows(spec, chunk, start)}</table>"
        start += len(chunk)
        if not last:
            body += "<div class='cont'>ยอดยกไปหน้าถัดไป</div>"
        else:
            note = spec.get("notes", "")
            if spec["doc_type"] == "invoice" and spec["layout"] == "modern":
                note = (note + " " if note else "") + "ใบกำกับภาษีจะออกให้เมื่อได้รับชำระเงินแล้ว"
            words = bahttext(spec["totals"].get("net_payable") or spec["totals"]["total"])
            body += (f"<div class='bottom'><div class='words'>({e(words)})</div>{_modern_totals(spec)}</div>"
                     f"<div class='notes'>{e(note)}</div>"
                     "<div class='sign'><div>ผู้รับสินค้า / Received by</div><div>ผู้มีอำนาจลงนาม / Authorized signature</div></div>")
        cls = "page pb" if not last else "page"
        out.append(f"<div class='{cls}'>{body}</div>")
    return _page(MODERN_CSS, wm + "".join(out))


# ── classic ─────────────────────────────────────────────────────────────────

CLASSIC_CSS = """
body { font-family: Tahoma, sans-serif; font-size: 9.5pt; color: #000; }
.hd { text-align: center; }
.hd .name { font-size: 14pt; font-weight: bold; }
.box { border: 1px solid #000; }
.ttl { text-align: center; font-size: 13pt; font-weight: bold; margin: 8px 0 4px; }
.grid { display: flex; }
.grid .l { width: 62%; border: 1px solid #000; padding: 4px 6px; }
.grid .r { width: 38%; border: 1px solid #000; border-left: none; padding: 4px 6px; }
table.it { width: 100%; border-collapse: collapse; margin-top: 6px; }
table.it th, table.it td { border: 1px solid #000; padding: 3px 5px; }
table.it th { background: #e8e8e8; }
.num { text-align: right; }
table.tt { width: 100%; border-collapse: collapse; }
table.tt td { border: 1px solid #000; padding: 3px 5px; }
.sg { display: flex; justify-content: space-between; margin-top: 40px; text-align: center; }
.sg div { width: 30%; border-top: 1px solid #000; padding-top: 3px; }
"""


def classic(spec, seller, buyer) -> str:
    t = spec["totals"]
    tid = seller_tax_printed(spec, seller)
    tax = f"เลขประจำตัวผู้เสียภาษี {e(_tax(tid, spec))} ({e(_branch(seller['branch'], spec))})" if tid else ""
    rows = "".join(
        f"<tr><td>{e(l['code'])}</td><td>{e(l['description'])}</td><td class='num'>{e(_qty(l['quantity'], spec))} {e(l['unit'])}</td>"
        f"<td class='num'>{e(_m(l['unit_price'], spec))}</td><td class='num'>{e(_m(l['amount'], spec))}</td></tr>"
        for l in spec["lines"])
    tot = [("รวมเงิน", t["sum_lines"])]
    if t["discount"]:
        tot += [("หักส่วนลด", t["discount"]), ("ยอดหลังหักส่วนลด", t["subtotal"])]
    tot += [("ภาษีมูลค่าเพิ่ม 7%", t["vat_amount"]), ("ยอดรวมสุทธิ", t["total"])]
    words = bahttext(t["total"])
    tot_rows = "".join(
        (f"<tr><td rowspan='{len(tot)}' style='width:60%;vertical-align:bottom'>ตัวอักษร: ({e(words)})</td>" if i == 0 else "<tr>")
        + f"<td>{e(k)}</td><td class='num'>{e(_m(v, spec))}</td></tr>" for i, (k, v) in enumerate(tot))
    return _page(CLASSIC_CSS, f"""
<div class="hd"><div class="name">{e(seller['name'])}</div><div>{e(seller['address'])} โทร. {e(seller['tel'])}</div><div>{tax}</div></div>
<div class="ttl">{e(spec['title'])} &nbsp; {e(spec.get('title_en', ''))} &nbsp; (ต้นฉบับ)</div>
<div class="grid"><div class="l">
  <div>นามผู้ซื้อ: {e(buyer['name'])}</div><div>ที่อยู่: {e(buyer['address'])}</div>
  <div>เลขประจำตัวผู้เสียภาษี: {e(_tax(buyer['tax_id'], spec))} &nbsp; {e(_branch(buyer['branch'], spec))}</div></div>
  <div class="r"><div>เลขที่ใบกำกับภาษี: {e(spec['number'])}</div><div>วันที่: {e(fmt_date(spec['date'], spec['date_style']))}</div>
  <div>เงื่อนไขการชำระเงิน: 30 วัน</div><div>วันครบกำหนด: {e(fmt_date(spec['due'], spec['date_style']))}</div></div></div>
<table class="it"><tr><th>รหัสสินค้า</th><th>รายการ</th><th>จำนวน</th><th>หน่วยละ</th><th>จำนวนเงิน</th></tr>{rows}</table>
<table class="tt">{tot_rows}</table>
<div class="sg"><div>ผู้รับของ</div><div>ผู้ส่งของ</div><div>ผู้มีอำนาจลงนาม</div></div>""")


# ── service invoice with WHT (Angsana New) ──────────────────────────────────

SERVICE_CSS = """
body { font-family: 'Angsana New', serif; font-size: 16pt; line-height: 1.05; }
.h { display: flex; justify-content: space-between; }
.h .t { text-align: right; font-size: 22pt; font-weight: bold; }
table { width: 100%; border-collapse: collapse; }
.it td, .it th { border: 1px solid #333; padding: 1px 6px; }
.r { text-align: right; }
.sum td { padding: 1px 6px; }
.wht { border: 1px solid #333; margin-top: 8px; padding: 2px 8px; }
"""


def service(spec, seller, buyer) -> str:
    t = spec["totals"]
    tid = seller_tax_printed(spec, seller)
    rows = "".join(f"<tr><td>{i}</td><td>{e(l['description'])}</td><td class='r'>{e(_qty(l['quantity'], spec))}</td>"
                   f"<td class='r'>{e(_m(l['unit_price'], spec))}</td><td class='r'>{e(_m(l['amount'], spec))}</td></tr>"
                   for i, l in enumerate(spec["lines"], 1))
    return _page(SERVICE_CSS, f"""
<div class="h"><div><b>{e(seller['print_name'])}</b><br>{e(seller['address'])}<br>
  เลขประจำตัวผู้เสียภาษี {e(_tax(tid, spec))} {e(_branch(seller['branch'], spec))}</div>
  <div class="t">{e(spec['title'])}<br><span style="font-size:14pt">{e(spec['title_en'])}</span></div></div>
<p>ผู้ว่าจ้าง: {e(buyer['name'])}<br>ที่อยู่: {e(buyer['address'])}<br>
เลขประจำตัวผู้เสียภาษี {e(_tax(buyer['tax_id'], spec))} {e(_branch(buyer['branch'], spec))}<br>
เลขที่ {e(spec['number'])} &nbsp;&nbsp; วันที่ {e(fmt_date(spec['date'], spec['date_style']))} &nbsp;&nbsp; กำหนดชำระ {e(fmt_date(spec['due'], spec['date_style']))}</p>
<table class="it"><tr><th>ที่</th><th>รายละเอียด</th><th>จำนวน</th><th>ราคา/หน่วย</th><th>จำนวนเงิน (บาท)</th></tr>{rows}</table>
<table class="sum"><tr><td style="width:60%">({e(bahttext(t['total']))})</td><td>รวมเป็นเงิน</td><td class="r">{e(_m(t['subtotal'], spec))}</td></tr>
<tr><td></td><td>ภาษีมูลค่าเพิ่ม 7%</td><td class="r">{e(_m(t['vat_amount'], spec))}</td></tr>
<tr><td></td><td><b>รวมทั้งสิ้น</b></td><td class="r"><b>{e(_m(t['total'], spec))}</b></td></tr></table>
<div class="wht">หักภาษี ณ ที่จ่าย {e(str(t['wht_rate']))}% ของค่าบริการ {e(_m(t['subtotal'], spec))} บาท = {e(_m(t['wht_amount'], spec))} บาท
<br><b>ยอดชำระสุทธิ {e(_m(t['net_payable'], spec))} บาท</b> ({e(bahttext(t['net_payable']))})</div>
<p>กรุณาออกหนังสือรับรองการหักภาษี ณ ที่จ่าย (50 ทวิ) ในนาม {e(seller['name'])}</p>
<p style="margin-top:30px">ลงชื่อ ............................................. ผู้รับจ้าง</p>""")


# ── POS thermal receipt ─────────────────────────────────────────────────────

POS_CSS = """
body { font-family: Tahoma, sans-serif; font-size: 8.6pt; width: 72mm; }
.c { text-align: center; } .r { text-align: right; }
.row { display: flex; justify-content: space-between; }
.dash { border-top: 1px dashed #000; margin: 4px 0; }
.big { font-size: 11pt; font-weight: bold; }
"""


def pos(spec, seller, buyer) -> str:
    t = spec["totals"]
    items = "".join(
        f"<div>{e(l['description'])}</div><div class='row'><span>&nbsp;&nbsp;{e(_qty(l['quantity'], spec))} x {e(_m(l['unit_price'], spec))}</span>"
        f"<span>{e(_m(l['amount'], spec))}</span></div>" for l in spec["lines"])
    pay = spec.get("payment", "เงินสด")
    change = ""
    if spec.get("cash"):
        from decimal import Decimal as D
        change = (f"<div class='row'><span>{e(pay)}</span><span>{e(_m(D(spec['cash']), spec))}</span></div>"
                  f"<div class='row'><span>ทอน</span><span>{e(_m(D(spec['cash']) - t['total'], spec))}</span></div>")
    else:
        change = f"<div class='row'><span>{e(pay)}</span><span>{e(_m(t['total'], spec))}</span></div>"
    vat_rows = "" if t["vat_mode"] == "none" else (
        f"<div class='row'><span>มูลค่าสินค้า</span><span>{e(_m(t['subtotal'], spec))}</span></div>"
        f"<div class='row'><span>VAT 7%</span><span>{e(_m(t['vat_amount'], spec))}</span></div>")
    vat_note = "" if t["vat_mode"] == "none" else '<div class="c">(VAT INCLUDED)</div>'
    return _page(POS_CSS, f"""
<div class="c big">{e(seller['print_name'])}</div>
<div class="c">{e(_branch(seller['branch'], spec))} {e(seller['address'])}</div>
<div class="c">TAX ID : {e(_tax(seller['tax_id'], spec))}</div>
<div class="c">{e(spec['title'])} / {e(spec['title_en'])}</div>
{vat_note}
<div class="dash"></div>
<div>เลขที่ {e(spec['number'])}</div><div>POS ID : {e(spec.get('pos_id', ''))}</div>
<div class="dash"></div>{items}<div class="dash"></div>
<div class="row"><span>รวม {len(spec['lines'])} รายการ</span><span></span></div>
<div class="row big"><span>ยอดรวม</span><span>{e(_m(t['total'], spec))}</span></div>
{vat_rows}
{change}<div class="dash"></div>
<div class="c">{e(fmt_date(spec['date'], spec['date_style']))} {e(spec.get('time', ''))}</div>
<div class="c">ขอบคุณที่ใช้บริการ</div>""", size="80mm 150mm", margin="3mm")


# ── receipt (no table) ──────────────────────────────────────────────────────

RECEIPT_CSS = """
body { font-family: 'Sarabun', sans-serif; font-size: 12pt; }
.h { text-align: center; } .h .n { font-size: 15pt; font-weight: 700; }
.t { text-align: center; font-size: 16pt; font-weight: 700; margin: 10px 0; letter-spacing: 2px; }
.meta { text-align: right; }
.line { margin: 8px 0; border-bottom: 1px dotted #666; }
.amt { font-size: 14pt; font-weight: 700; }
"""


def receipt(spec, seller, buyer) -> str:
    t = spec["totals"]
    l = spec["lines"][0]
    return _page(RECEIPT_CSS, f"""
<div class="h"><div class="n">{e(seller['print_name'])}</div><div>{e(seller['address'])} โทร {e(seller['tel'])}</div>
<div>เลขประจำตัวผู้เสียภาษี {e(_tax(seller['tax_id'], spec))}</div></div>
<div class="t">{e(spec['title'])}</div>
<div class="meta">เลขที่ {e(spec['number'])}<br>วันที่ {e(fmt_date(spec['date'], spec['date_style']))}</div>
<div class="line">ได้รับเงินจาก {e(buyer['name'])}</div>
<div class="line">ที่อยู่ {e(buyer['address'])}</div>
<div class="line">เป็นค่า {e(l['description'])}</div>
<div class="line amt">จำนวนเงิน {e(_m(t['total'], spec))} บาท</div>
<div class="line">({e(bahttext(t['total']))})</div>
<div>ชำระโดย [x] เงินสด [ ] เช็ค [ ] โอนเงิน</div>
<p style="margin-top:40px;text-align:right">ลงชื่อ ................................ ผู้รับเงิน<br>(นางสมใจ ใจดี)</p>""", size="A5 landscape")


# ── English, foreign supplier ──────────────────────────────────────────────

EN_CSS = """
body { font-family: Arial, sans-serif; font-size: 10pt; }
h1 { font-size: 20pt; margin: 0 0 10px; }
table { width: 100%; border-collapse: collapse; }
.it th { border-bottom: 2px solid #333; text-align: left; padding: 4px; }
.it td { border-bottom: 1px solid #ddd; padding: 4px; }
.r { text-align: right !important; }
.tot { width: 45%; margin-left: auto; margin-top: 10px; }
.tot td { padding: 3px; }
"""


def english(spec, seller, buyer) -> str:
    t = spec["totals"]
    rows = "".join(f"<tr><td>{e(l['description'])}</td><td class='r'>{e(_qty(l['quantity'], spec))}</td>"
                   f"<td class='r'>{e(_m(l['unit_price'], spec))}</td><td class='r'>{e(_m(l['amount'], spec))}</td></tr>"
                   for l in spec["lines"])
    return _page(EN_CSS, f"""
<table><tr><td><h1>INVOICE</h1><b>{e(seller['name'])}</b><br>{e(seller['address'])}<br>{e(seller.get('foreign_reg', ''))}<br>Tel {e(seller['tel'])}</td>
<td class="r" style="vertical-align:top">Invoice No: {e(spec['number'])}<br>Invoice Date: {e(fmt_date(spec['date'], 'en'))}<br>
Due Date: {e(fmt_date(spec['due'], 'en'))}<br>Currency: USD</td></tr></table>
<p><b>Bill To</b><br>{e(buyer['name'])}<br>{e(buyer['address'])}<br>Tax ID {e(buyer['tax_id'])} (Head Office)</p>
<table class="it"><tr><th>Description</th><th class="r">Qty</th><th class="r">Unit Price (USD)</th><th class="r">Amount (USD)</th></tr>{rows}</table>
<table class="tot"><tr><td>Subtotal</td><td class="r">USD {e(_m(t['subtotal'], spec))}</td></tr>
<tr><td>Tax (0%)</td><td class="r">USD 0.00</td></tr>
<tr><td><b>Total Due</b></td><td class="r"><b>USD {e(_m(t['total'], spec))}</b></td></tr></table>
<p>All amounts are in US Dollars. Payment by wire transfer to DBS Bank, SWIFT DBSSSGSG.<br>
Services supplied from Singapore; the customer accounts for any Thai VAT under self-assessment (P.P.36).</p>""")


LAYOUTS = {"modern": modern, "classic": classic, "service": service, "pos": pos, "receipt": receipt, "english": english}
