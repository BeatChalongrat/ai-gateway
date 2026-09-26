"""A held-out E2 set (AM2 Phase C): layouts and label wordings the engine was not built on.

    python -m agent_market.tools.e2fix.holdout [out_dir]     (from backend/)

The Phase A pack drove the engine's development, so passing it proves less
than it seems. These six documents use new templates and other ways Thai
invoices say the same things (รวมมูลค่าสินค้า, จำนวนเงินทั้งสิ้น, Grand Total,
VATABLE, ยอดโอนสุทธิ, ภาษีหัก ณ ที่จ่าย 1%, TAX INVOICE (ABB.)). They are
scored exactly like the pack (tests/e2kit.py) and reported as they come out —
not tuned against.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from decimal import Decimal as D
from pathlib import Path

from . import expected, render
from .html import FONT_FACE, bahttext, e, fmt_date, money
from .specs import ORG, SUPPLIERS, _lines, _totals
from .thai import make_tax_id

NEW = {
    "S9": {"name": "บริษัท พรีเมียร์ อิเล็คทริค จำกัด", "print_name": "บริษัท พรีเมียร์ อิเล็คทริค จำกัด",
           "legal_form": "company_limited", "core": "พรีเมียร์ อิเล็คทริค", "tax_id": make_tax_id("010556412233"),
           "branch": "00000", "address": "55 ซอยสุขุมวิท 71 แขวงคลองตันเหนือ เขตวัฒนา กรุงเทพฯ 10110", "tel": "02-381-9900"},
    "S10": {"name": "บริษัท สยามโลจิสติกส์ เอ็กซ์เพรส จำกัด", "print_name": "บริษัท สยามโลจิสติกส์ เอ็กซ์เพรส จำกัด",
            "legal_form": "company_limited", "core": "สยามโลจิสติกส์ เอ็กซ์เพรส", "tax_id": make_tax_id("010555207716"),
            "branch": "00003", "address": "9/19 ถนนเทพารักษ์ อำเภอบางพลี จังหวัดสมุทรปราการ 10540", "tel": "02-750-1122"},
    "S11": {"name": "บริษัท ฟู้ดมาร์ท เอ็กซ์ตร้า จำกัด", "print_name": "บริษัท ฟู้ดมาร์ท เอ็กซ์ตร้า จำกัด",
            "legal_form": "company_limited", "core": "ฟู้ดมาร์ท เอ็กซ์ตร้า", "tax_id": make_tax_id("010554811029"),
            "branch": "00041", "address": "สาขาพระราม 9", "tel": ""},
}
SUPPLIERS.update(NEW)

CSS_A = """
body { font-family: 'Leelawadee UI', 'Tahoma', sans-serif; font-size: 10pt; color: #111; }
.band { background: #243b55; color: #fff; padding: 10px 14px; display: flex; justify-content: space-between; }
.band h1 { margin: 0; font-size: 15pt; }
.two { display: flex; gap: 18px; margin-top: 10px; }
.two > div { flex: 1; border: 1px solid #ccc; padding: 6px 10px; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th { border-bottom: 2px solid #243b55; text-align: left; padding: 4px; }
td { padding: 4px; border-bottom: 1px solid #eee; }
.r { text-align: right; }
.sum { width: 46%; margin-left: auto; margin-top: 8px; }
.sum td { border: none; padding: 2px 4px; }
.big td { font-weight: bold; font-size: 12pt; border-top: 1px solid #243b55; }
"""


def layout_flow(spec, seller, buyer) -> str:
    """H1: a cloud-invoice layout with its own label wording."""
    t = spec["totals"]
    rows = "".join(f"<tr><td>{i}</td><td>{e(l['description'])}</td><td class='r'>{int(l['quantity'])}</td>"
                   f"<td class='r'>{money(l['unit_price'])}</td><td class='r'>{money(l['amount'])}</td></tr>"
                   for i, l in enumerate(spec["lines"], 1))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONT_FACE}@page {{ size: A4; margin: 12mm; }}{CSS_A}</style></head><body>
<div class="band"><div><h1>{e(seller['print_name'])}</h1>{e(seller['address'])}<br>Tax ID: {e(seller['tax_id'])} (สำนักงานใหญ่)</div>
<div style="text-align:right"><h1>ใบกำกับภาษี</h1>TAX INVOICE<br>ต้นฉบับ</div></div>
<div class="two"><div><b>ลูกค้า</b><br>{e(buyer['name'])}<br>{e(buyer['address'])}<br>Tax ID: {e(buyer['tax_id'])} สำนักงานใหญ่</div>
<div>เลขที่เอกสาร: {e(spec['number'])}<br>วันที่ออก: {e(fmt_date(spec['date'], 'th_abbr'))}<br>ครบกำหนดชำระ: {e(fmt_date(spec['due'], 'th_abbr'))}</div></div>
<table><tr><th>#</th><th>คำอธิบาย</th><th class="r">จำนวน</th><th class="r">ราคา</th><th class="r">มูลค่า</th></tr>{rows}</table>
<table class="sum"><tr><td>รวมมูลค่าสินค้า</td><td class="r">{money(t['subtotal'])}</td></tr>
<tr><td>ภาษีมูลค่าเพิ่ม 7%</td><td class="r">{money(t['vat_amount'])}</td></tr>
<tr class="big"><td>จำนวนเงินทั้งสิ้น</td><td class="r">{money(t['total'])}</td></tr></table>
<p>({e(bahttext(t['total']))})</p></body></html>"""


def layout_bilingual(spec, seller, buyer) -> str:
    """H2 / H3: an English-first bilingual tax invoice (Sub Total / Discount / Grand Total)."""
    t = spec["totals"]
    rows = "".join(f"<tr><td>{e(l['code'])}</td><td>{e(l['description'])}</td><td class='r'>{int(l['quantity'])}</td>"
                   f"<td class='r'>{money(l['unit_price'])}</td><td class='r'>{money(l['amount'])}</td></tr>"
                   for l in spec["lines"])
    disc = f"<tr><td>Discount / ส่วนลด</td><td class='r'>{money(t['discount'])}</td></tr>" if t["discount"] else ""
    after = f"<tr><td>Net Amount / มูลค่าหลังหักส่วนลด</td><td class='r'>{money(t['subtotal'])}</td></tr>" if t["discount"] else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONT_FACE}@page {{ size: A4; margin: 14mm; }}
body {{ font-family: Arial, 'Tahoma', sans-serif; font-size: 9.5pt; }}
h1 {{ font-size: 16pt; margin: 0; }} table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
th, td {{ border: 1px solid #999; padding: 3px 5px; }} .r {{ text-align: right; }} .sum td {{ border: none; }}</style></head><body>
<table class="sum"><tr><td><b>{e(seller['name'])}</b><br>{e(seller['address'])}<br>Tel {e(seller['tel'])}<br>
Tax ID No. {e(seller['tax_id'])} Branch: {'Head Office' if seller['branch'] == '00000' else 'Branch ' + seller['branch']}</td>
<td style="text-align:right"><h1>TAX INVOICE / ใบกำกับภาษี</h1>Original / ต้นฉบับ</td></tr></table>
<table class="sum"><tr><td>Customer / ลูกค้า: {e(buyer['name'])}<br>Address: {e(buyer['address'])}<br>Tax ID No. {e(buyer['tax_id'])} Branch: Head Office</td>
<td>Invoice No. / เลขที่: {e(spec['number'])}<br>Date / วันที่: {e(fmt_date(spec['date'], 'slash_ce'))}<br>Due Date: {e(fmt_date(spec['due'], 'slash_ce'))}</td></tr></table>
<table><tr><th>Code</th><th>Description / รายการ</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>{rows}</table>
<table class="sum" style="width:50%;margin-left:auto"><tr><td>Sub Total / รวมเงิน</td><td class="r">{money(t['sum_lines'])}</td></tr>{disc}{after}
<tr><td>VAT 7% / ภาษีมูลค่าเพิ่ม</td><td class="r">{money(t['vat_amount'])}</td></tr>
<tr><td><b>Grand Total / ยอดรวมทั้งสิ้น</b></td><td class="r"><b>{money(t['total'])}</b></td></tr></table>
<p>Amount in words: ({e(bahttext(t['total']))})</p></body></html>"""


def layout_transport(spec, seller, buyer) -> str:
    """H5: transport services with 1 % WHT and a "ยอดโอนสุทธิ" net line."""
    t = spec["totals"]
    rows = "".join(f"<tr><td>{e(l['description'])}</td><td class='r'>{int(l['quantity'])}</td><td class='r'>{money(l['unit_price'])}</td>"
                   f"<td class='r'>{money(l['amount'])}</td></tr>" for l in spec["lines"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONT_FACE}@page {{ size: A4; margin: 12mm; }}
body {{ font-family: 'Sarabun'; font-size: 11pt; }} table {{ width: 100%; border-collapse: collapse; }}
.it td, .it th {{ border-bottom: 1px solid #ccc; padding: 3px; }} .r {{ text-align: right; }}</style></head><body>
<p style="font-size:14pt"><b>{e(seller['print_name'])}</b> &nbsp; สาขาที่ {e(seller['branch'])}<br>{e(seller['address'])}<br>
เลขประจำตัวผู้เสียภาษีอากร {e(seller['tax_id'])}</p>
<h2 style="text-align:center">ใบแจ้งหนี้/ใบกำกับภาษี</h2>
<p>ชื่อลูกค้า {e(buyer['name'])} (สำนักงานใหญ่) เลขประจำตัวผู้เสียภาษีอากร {e(buyer['tax_id'])}<br>
เลขที่ {e(spec['number'])} &nbsp; ลงวันที่ {e(fmt_date(spec['date'], 'th_full'))}</p>
<table class="it"><tr><th>รายละเอียดบริการ</th><th class="r">จำนวน</th><th class="r">หน่วยละ</th><th class="r">จำนวนเงิน</th></tr>{rows}</table>
<table style="width:55%;margin-left:auto"><tr><td>รวมเป็นเงิน</td><td class="r">{money(t['subtotal'])}</td></tr>
<tr><td>ภาษีมูลค่าเพิ่ม 7%</td><td class="r">{money(t['vat_amount'])}</td></tr>
<tr><td>รวมทั้งสิ้น</td><td class="r">{money(t['total'])}</td></tr>
<tr><td>ภาษีหัก ณ ที่จ่าย 1%</td><td class="r">{money(t['wht_amount'])}</td></tr>
<tr><td><b>ยอดชำระสุทธิ</b></td><td class="r"><b>{money(t['net_payable'])}</b></td></tr></table></body></html>"""


def layout_pos2(spec, seller, buyer) -> str:
    """H6: another POS slip style (VATABLE / TOTAL / CASH / CHANGE)."""
    t = spec["totals"]
    items = "".join(f"<div class='row'><span>{int(l['quantity'])} {e(l['description'])}</span><span>{money(l['amount'])}</span></div>"
                    for l in spec["lines"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONT_FACE}@page {{ size: 80mm 140mm; margin: 3mm; }}
body {{ font-family: 'Courier New', 'Tahoma', monospace; font-size: 8.4pt; width: 72mm; }}
.c {{ text-align: center; }} .row {{ display: flex; justify-content: space-between; }}</style></head><body>
<div class="c"><b>{e(seller['print_name'])}</b><br>{e(seller['address'])} สาขาที่ {e(seller['branch'])}<br>
TAX ID {e(seller['tax_id'])}<br>TAX INVOICE (ABB.)<br>ใบกำกับภาษีอย่างย่อ</div>
<div>No. {e(spec['number'])}</div><div>{e(fmt_date(spec['date'], 'slash_ce'))} {e(spec.get('time', ''))}</div><hr>{items}<hr>
<div class="row"><span>VATABLE</span><span>{money(t['subtotal'])}</span></div>
<div class="row"><span>VAT 7.00%</span><span>{money(t['vat_amount'])}</span></div>
<div class="row"><b>TOTAL</b><b>{money(t['total'])}</b></div>
<div class="row"><span>CASH</span><span>500.00</span></div><div class="row"><span>CHANGE</span><span>{money(D('500') - t['total'])}</span></div>
<div class="c">THANK YOU</div></body></html>"""


d = dt.date
HOLD = {
    "h01_flow_layout": {"layout": "flow", "fn": layout_flow, "seller": "S9", "buyer": ORG, "number": "INV-6909-0102",
                        "date": d(2026, 9, 9), "due": d(2026, 10, 9), "doc_type": "full_tax_invoice", "title": "ใบกำกับภาษี",
                        "lines": _lines(("", "เบรกเกอร์ 3 เฟส 100A", 2, "", "4850.00"), ("", "สายไฟ THW 16 sq.mm (ม้วน)", 3, "", "3990.00"))},
    "h02_bilingual": {"layout": "bilingual", "fn": layout_bilingual, "seller": "S10", "buyer": ORG, "number": "SL26-09-0456",
                      "date": d(2026, 9, 14), "due": d(2026, 10, 14), "doc_type": "full_tax_invoice", "title": "TAX INVOICE",
                      "lines": _lines(("FR-BKK", "Freight Bangkok - Chonburi", 4, "", "3200.00"), ("HD-01", "Handling fee", 4, "", "350.00"))},
    "h03_bilingual_discount": {"layout": "bilingual", "fn": layout_bilingual, "seller": "S9", "buyer": ORG, "number": "INV-6909-0117",
                               "date": d(2026, 9, 19), "due": d(2026, 10, 19), "doc_type": "full_tax_invoice", "title": "TAX INVOICE",
                               "discount": "1250.00",
                               "lines": _lines(("LED-40", "โคมไฟ LED 40W", 25, "", "690.00"), ("SW-3", "สวิตช์ 3 ทาง", 30, "", "85.00"))},
    "h05_transport_wht1": {"layout": "transport", "fn": layout_transport, "seller": "S10", "buyer": ORG, "number": "TR2569/0877",
                           "date": d(2026, 9, 26), "due": None, "doc_type": "full_tax_invoice", "title": "ใบแจ้งหนี้/ใบกำกับภาษี",
                           "wht_rate": "1",
                           "lines": _lines(("", "ค่าขนส่งสินค้าเที่ยวเดียว 6 ล้อ", 5, "", "2800.00"))},
    "h06_pos_vatable": {"layout": "pos2", "fn": layout_pos2, "seller": "S11", "buyer": None, "number": "0041-26092-01877",
                        "date": d(2026, 9, 27), "time": "11:42", "doc_type": "abbreviated_tax_invoice", "title": "TAX INVOICE (ABB.)",
                        "vat_mode": "inclusive",
                        "lines": _lines(("", "ข้าวหอมมะลิ 5 กก.", 1, "", "189.00"), ("", "น้ำมันพืช 1 ลิตร", 2, "", "59.00"))},
}


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "e2_holdout"
    for sub in ("docs", "html", "expected"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    index = []
    rendered = {}
    for fid, s in HOLD.items():
        spec = {**s, "id": fid, "date_style": "iso", "tax_style": "plain", "class": "digital", "expect_questions": [],
                "purpose": s["fn"].__doc__.strip().splitlines()[0]}
        spec["totals"] = _totals(spec["lines"], discount=D(s.get("discount", "0")), vat_mode=s.get("vat_mode", "exclusive"),
                                 wht_rate=s.get("wht_rate"))
        seller = SUPPLIERS[s["seller"]]
        hp = out / "html" / f"{fid}.html"
        hp.write_text(s["fn"](spec, seller, s["buyer"]), encoding="utf-8", newline="\n")
        render.chrome_pdf(hp, out / "docs" / f"{fid}.pdf")
        rendered[fid] = spec
        spec2 = {k: v for k, v in spec.items() if k != "fn"}
        spec2["layout"] = "english" if s["layout"] == "bilingual" else ("pos" if s["layout"] == "pos2" else "modern")
        exp = expected.build(spec2, {"files": [f"docs/{fid}.pdf"], "org": ORG})
        if s["layout"] == "pos2":
            exp["checks"]["line_arithmetic"] = "skip"      # this slip prints no unit price: nothing to multiply
        (out / "expected" / f"{fid}.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        index.append({"id": fid, "class": "digital", "files": [f"docs/{fid}.pdf"]})
    # H4: the flow layout scanned at 300 DPI
    fid = "h04_flow_scan300"
    render.scan(out / "docs" / "h01_flow_layout.pdf", out / "docs" / f"{fid}.pdf", 300, 0.5, 7, 0.45, 80, 21)
    exp = json.loads((out / "expected" / "h01_flow_layout.json").read_text(encoding="utf-8"))
    exp.update(id=fid, files=[f"docs/{fid}.pdf"], **{"class": "scan300"}, rules=["no_silent_errors"],
               questions={"must": [], "may": True, "allowed_extra": [], "max": None}, source_fixture="h01_flow_layout")
    (out / "expected" / f"{fid}.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    index.append({"id": fid, "class": "scan300", "files": [f"docs/{fid}.pdf"]})
    render.cleanup_profile(out / "docs")
    for f in index:
        f["sha256"] = {p: hashlib.sha256((out / p).read_bytes()).hexdigest() for p in f["files"]}
    (out / "index.json").write_text(json.dumps({"schema": "e2.fixtures.v1", "fixtures": index}, ensure_ascii=False, indent=1),
                                    encoding="utf-8", newline="\n")
    (out / "org.json").write_text(json.dumps({"name": ORG["name"], "tax_id": ORG["tax_id"], "branch": ORG["branch"]},
                                             ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(index)} holdout fixtures -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
