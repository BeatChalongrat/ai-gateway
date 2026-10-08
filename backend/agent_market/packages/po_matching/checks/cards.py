"""What a person decides in a round → clarification cards, in the order they are asked.

    pomatch_kind:<file.sheet>   a table whose headers fit more than one kind of document
    missing_file                no purchase order lines, or nothing billed
    ap_gr_practice              whether the company issues goods receipts — asked once, with the
                                answer the round's own files suggest, and remembered
    e2_*                        a document's unclear values (engine/e2/cards.py)
    e3_candidate:<pair>         a proposed link: this line of the document and that PO line; or
                                an invoice that may be one presented before
    pomatch_supplier:<inv:po>   the supplier cannot be told by tax ID, and the names only resemble
    pomatch_unit:<from>to>      two units of measure nothing converts

A company that does not issue goods receipts is asked nothing about a missing one. A link is made
only by a rule of the exact tier or by a person's answer; a skipped card never makes one: its lines
wait as "ต้องให้คนตัดสิน".
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.e2 import cards as E2C
from ....engine.e2.schema import FIELD_KINDS
from ....engine.e3 import cards as E3C
from ....engine.e3.match import MATCH, SEPARATE, UNDECIDED
from ....engine.findings import Finding, finding
from ....engine.thai import fmt_money
from . import book as B
from . import inputs as I
from . import links as L
from . import parties as P
from . import settings as S

KIND_OPTIONS = {"po": ("ใบสั่งซื้อ (PO)", "po", "รายการที่ตกลงซื้อ: สินค้า จำนวน และราคาต่อหน่วย"),
                "gr": ("ใบรับของ (GR)", "gr", "รายการที่รับเข้าจริง และวันที่รับ"),
                "invoice": ("ใบแจ้งหนี้หรือใบกำกับภาษี", "invoice", "รายการที่ผู้ขายเรียกเก็บ"),
                "unused": ("ไม่ใช้แผ่นงานนี้", "unused", "ตั้งไว้ก่อน ไม่นำมาตรวจในรอบนี้")}
COLUMNS = ["เอกสาร", "วันที่", "รายการ", "จำนวน", "หน่วย", "ราคาต่อหน่วย", "จำนวนเงิน", "ไฟล์", "แถว"]


def kind_card(u, t: dict) -> Finding:
    could = [k for k in (t.get("could_be") or list(I.KINDS)) if k in KIND_OPTIONS][:3]
    heads = list(t["columns"])
    rows = [[str(r.get(f) or "") for f in heads[:6]] for r in t["rows"][:4]]
    return finding(
        "pomatch_kind", "blocking", "preflight", "เอกสารชนิดใด",
        f"แผ่นงาน “{E3C.safe(t['sheet'])}” ในไฟล์ {E3C.safe(u.name)} มีหัวคอลัมน์ที่เป็นได้มากกว่าหนึ่งชนิดเอกสาร แผ่นงานนี้คืออะไร",
        [KIND_OPTIONS[k] for k in could] + [KIND_OPTIONS["unused"]], "unused", subject=f"{u.key}.{t['sheet_index']}",
        leaves_out=True, rememberable=False,
        evidence={"file": u.name, "sheet": t["sheet"], "columns": heads[:6], "rows": rows},
        facts={"file": u.name, "sheet": t["sheet"], "could_be": could})


def missing_card(rv) -> Finding | None:
    want = []
    if not rv.po:
        want.append({"role": "po", "label": "ใบสั่งซื้อ (PO) แบบมีรายการรายบรรทัด",
                     "why": "สิ่งที่ตกลงซื้อ จำนวน และราคาต่อหน่วยของทุกบรรทัดมาจากไฟล์นี้", "required": True,
                     "accept": [".xlsx", ".csv"]})
    if not rv.invoices and not any(d.status == "pending" for d in rv.docs):
        want.append({"role": "invoice", "label": "ใบกำกับภาษีหรือใบแจ้งหนี้จากผู้ขาย",
                     "why": "สิ่งที่ผู้ขายเรียกเก็บมาจากไฟล์นี้ ส่งเป็น PDF ไฟล์ e-Tax หรือตาราง Excel ก็ได้", "required": True,
                     "accept": [".pdf", ".xml", ".xlsx", ".csv"]})
    if not want:
        return None
    got = [[u.name, I.KIND_TH.get(k, k)] for u, _t, k in rv.tables] + [[d.upload.name, "ใบแจ้งหนี้ (เอกสาร)"] for d in rv.docs] + \
          [[u.name, "ไม่ได้ใช้"] for u, _why, _d in rv.others]
    names = " และ".join(w["label"] for w in want)
    return finding("missing_file", "blocking", "preflight", "ขาดไฟล์",
                   f"ยังไม่มี{names} ผมต้องมีทั้งใบสั่งซื้อและใบแจ้งหนี้ก่อนจึงจะตรวจได้ ใบรับของส่งมาด้วยได้ถ้ามี",
                   [("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้"), ("ยกเลิกรอบนี้", "cancel", "ปิดรอบนี้ไว้ก่อน")],
                   "wait", kind="request_files", subject="+".join(w["role"] for w in want), request=want,
                   facts={"missing": [w["role"] for w in want]},
                   evidence={"columns": ["ไฟล์ที่ได้รับ", "ใช้เป็น"], "rows": got or [["ยังไม่มีไฟล์", ""]]})


def practice_card(rv) -> Finding:
    if rv.gr_files:
        seen = f"รอบนี้มีไฟล์ใบรับของ {rv.gr_files} ไฟล์ จึงน่าจะออก"
    else:
        seen = "รอบนี้ไม่มีไฟล์ใบรับของ จึงน่าจะไม่ออก"
    return finding(
        "ap_gr_practice", "blocking", "preflight", "ใบรับของ",
        f"บริษัทของคุณออกใบรับของ (GR) เมื่อรับสินค้าหรือไม่ ({seen}) คำตอบนี้ถามครั้งเดียวและแก้ได้ในหน้าตั้งค่า agent",
        [(S.GR_PRACTICE_VALUE_TH[S.GR_YES], S.GR_YES, "สินค้าที่ถูกเรียกเก็บโดยยังไม่มีใบรับของ เป็นข้อยกเว้นที่ต้องติดตาม"),
         (S.GR_PRACTICE_VALUE_TH[S.GR_NO], S.GR_NO, "ไม่มีใบรับของเป็นเรื่องปกติ ตรวจ 2 ทาง และไม่ถามเรื่องนี้อีก")],
        rv.gr_guess, recommended=rv.gr_guess, rememberable=True,
        evidence={"columns": ["ไฟล์ที่ได้รับ", "ใช้เป็น"],
                  "rows": [[u.name, I.KIND_TH.get(k, k)] for u, _t, k in rv.tables] or [["ไม่มีไฟล์ตาราง", ""]]},
        facts={"gr_files": rv.gr_files, "guess": rv.gr_guess})


def invalid_cards(u, out: dict, dec) -> list:
    """A typed value that could not be used is asked once more (as the invoice agent does)."""
    fs = []
    label = E2C._doc_label(out)
    for name, (key, why) in sorted(dec.invalid.items()):
        if name == "fx_rate":
            what, kind, hint = "อัตราแลกเปลี่ยน", "rate", "พิมพ์อัตราเป็นบาทต่อ 1 หน่วย เช่น 36.25"
            opts = [E2C.exclude(), ("ยังไม่ทราบอัตรา", "reupload", "ตัดออกไว้ก่อน ส่งใหม่เมื่อทราบอัตรา")]
        else:
            what = E2C.FIELD_TH.get(name, "ค่าในเอกสาร")
            kind = E2C.INPUT_KIND.get(FIELD_KINDS.get(name, "text"), "text")
            hint = E2C.INPUT_HINT.get(kind, E2C.INPUT_HINT["text"])
            opts = [("ไม่มีในเอกสาร", "absent", "เอกสารไม่ได้พิมพ์ค่านี้"), E2C.exclude()]
        fs.append(finding("e2_typed_invalid", "blocking", "preflight", "พิมพ์ใหม่",
                          f"{label}: ค่า{what}ที่พิมพ์มาใช้ไม่ได้ ({why}) ช่วยพิมพ์ใหม่อีกครั้ง", opts, "exclude",
                          subject=f"{u.key}:{name}", input={"kind": kind, "hint": hint}, leaves_out=True,
                          evidence={"document": u.name, "answered_by": key}, facts={"field": name}))
    return fs


# ── links ────────────────────────────────────────────────────────────────

def _po_row(pl: B.PoLine) -> list:
    return [pl.label, E3C.date_th(pl.date.isoformat() if pl.date else None), E3C.safe(pl.description)[:80], B.qty_text(pl.qty),
            E3C.safe(pl.unit), "" if pl.unit_price is None else f"{pl.unit_price:,.2f}",
            "" if pl.amount is None else f"{pl.amount:,.2f}", E3C.safe(pl.source.file if pl.source else ""),
            pl.source.row if pl.source else None]


def _doc_row(doc: str, date, l) -> list:
    return [doc, E3C.date_th(date.isoformat() if date else None), E3C.safe(l.description)[:80], B.qty_text(l.qty), E3C.safe(l.unit),
            "" if getattr(l, "unit_price", None) is None else f"{l.unit_price:,.2f}",
            "" if getattr(l, "amount", None) is None else f"{l.amount:,.2f}", E3C.safe(l.source.file if l.source else ""),
            (l.source.row if l.source else None)]


def _unit(u) -> str:
    """A unit as a card names it: its Thai name when it has one ("pcs" is ชิ้น), else as written, quoted."""
    from . import units as U

    c = U.canonical(u)
    if not c:
        return ""
    return c if c in U.UNITS else f"“{E3C.safe(u)}”"


def _line_words(l) -> str:
    bits = [f"“{E3C.safe(l.description)[:60]}”" if l.description else "", f"รหัส “{E3C.safe(l.item_code)}”" if l.item_code else ""]
    if l.qty is not None:
        bits.append(f"{B.qty_text(l.qty)} {_unit(l.unit)}".strip())
    if getattr(l, "unit_price", None) is not None:
        bits.append(f"ราคาต่อหน่วย {fmt_money(l.unit_price)} บาท")
    return " ".join(b for b in bits if b)


def _why(c: dict) -> str:
    pct = int((Decimal(c["similarity"] or "0") * 100).to_integral_value()) if c.get("similarity") else None
    return {
        "po_price": "เอกสารระบุ PO เดียวกัน และราคาต่อหน่วยอยู่ในช่วงที่ตั้งไว้ แต่ไม่ได้ระบุบรรทัดหรือรหัสสินค้าของ PO",
        "supplier_item": "เอกสารไม่ได้ระบุเลขที่ PO แต่ผู้ขายและรหัสสินค้าตรงกัน",
        "po_similar": f"เอกสารระบุ PO เดียวกัน และชื่อรายการคล้ายกัน {pct}%",
        "supplier_similar": f"เอกสารไม่ได้ระบุเลขที่ PO แต่เป็นผู้ขายเดียวกันและชื่อรายการคล้ายกัน {pct}%",
    }.get(c["pass"], c.get("pass_label") or "")


def link_card(c: dict, rv, doc_label: str, doc_date, lines: dict, names: dict) -> Finding | None:
    pl = rv.po_by_id(c["left"][0]["id"])
    right = [lines[r["id"]] for r in c["right"] if r["id"] in lines]
    if pl is None or not right:
        return None
    l = right[0]
    other = f" (มีบรรทัดอื่นของ PO ที่อาจเป็นคู่ได้อีก {len(c['alternatives'])} บรรทัด)" if c.get("alternatives") else ""
    return finding(
        E3C.CODE_PAIR, "blocking", "preflight", "จับคู่กับ PO",
        f"{names['right']} {doc_label}: {_line_words(l)} กับ{names['left']} {pl.label}: {_line_words(pl)} — {_why(c)}{other} "
        f"เป็นรายการเดียวกันหรือไม่",
        [("เป็นรายการเดียวกัน", MATCH, "ตรวจบรรทัดนี้กับบรรทัดนี้ของ PO และบันทึกว่าคุณเป็นผู้ยืนยัน"),
         ("คนละรายการ", SEPARATE, "ไม่จับคู่ บรรทัดนี้จะถูกนับว่าไม่อยู่ใน PO"),
         ("ยังไม่แน่ใจ", UNDECIDED, "เก็บไว้ให้คนตัดสิน เอกสารนี้จะยังไม่ผ่าน")],
        UNDECIDED, subject=c["pair"], rememberable=False,
        evidence={"columns": COLUMNS, "rows": [_po_row(pl)] + [_doc_row(f"{names['right']} {doc_label}", doc_date, x) for x in right]},
        facts={"pass": c["pass"], "tier": c["tier"], "similarity": c.get("similarity")})


def duplicate_card(c: dict, inv: B.Invoice) -> Finding:
    e = c["left"][0]
    why = {"same_amount_date": "ผู้ขายและยอดรวมตรงกัน วันที่ใกล้กัน แต่เลขที่ต่างกัน",
           "similar_number": "ผู้ขายและยอดรวมตรงกัน เลขที่คล้ายกัน",
           "same_number_other_total": "ผู้ขายและเลขที่ตรงกัน แต่ยอดรวมไม่ตรงกัน"}.get(c["pass"], c.get("pass_label") or "")
    total = "" if inv.total is None else f" ยอดรวม {fmt_money(inv.total)} บาท"
    return finding(
        E3C.CODE_PAIR, "blocking", "preflight", "ใบแจ้งหนี้ซ้ำ",
        f"ใบแจ้งหนี้{inv.label}{total} กับใบแจ้งหนี้เลขที่ {E3C.safe(e.get('ref'))} ลงวันที่ {E3C.date_th(e.get('date'))} "
        f"ยอดรวม {E3C.money(e['amount'])} บาท ที่ส่งมาก่อนหน้านี้ — {why} เป็นใบแจ้งหนี้ฉบับเดียวกันหรือไม่",
        [("ฉบับเดียวกัน (ซ้ำ)", MATCH, "ฉบับนี้ติดเงื่อนไขใบแจ้งหนี้ซ้ำ และไม่นับยอดสะสมอีก"),
         ("คนละฉบับ", SEPARATE, "ตรวจฉบับนี้ตามปกติ"),
         ("ยังไม่แน่ใจ", UNDECIDED, "เก็บไว้ให้คนตัดสิน ฉบับนี้จะยังไม่ผ่าน")],
        UNDECIDED, subject=c["pair"], rememberable=False,
        evidence={"columns": ["ใบแจ้งหนี้", "วันที่", "เลขที่", "ยอดรวม", "ไฟล์"],
                  "rows": [["ส่งมาก่อน", E3C.date_th(e.get("date")), E3C.safe(e.get("ref")), f"{Decimal(e['amount']):,.2f}",
                            E3C.safe(e.get("file"))],
                           ["ฉบับนี้", E3C.date_th(inv.date.isoformat() if inv.date else None), E3C.safe(inv.number),
                            "" if inv.total is None else f"{inv.total:,.2f}", E3C.safe(inv.name)]]},
        facts={"pass": c["pass"], "tier": c["tier"], "similarity": c.get("similarity")})


def supplier_card(inv: B.Invoice, po_key: str, s: dict) -> Finding:
    a, b = inv.supplier, s["po_supplier"]
    return finding(
        "pomatch_supplier", "blocking", "preflight", "ผู้ขาย",
        f"ใบแจ้งหนี้{inv.label} ส่วน PO {E3C.safe(s['po'])} สั่งซื้อจาก “{E3C.safe(b['name'])}” — "
        f"{s['how_label']} เป็นผู้ขายรายเดียวกันหรือไม่",
        [("ผู้ขายรายเดียวกัน", P.SAME, "ตรวจต่อ และบันทึกว่าคุณเป็นผู้ยืนยัน"),
         ("คนละราย", P.DIFFERENT, "ใบแจ้งหนี้นี้ติดเงื่อนไขผู้ขายไม่ตรง"),
         ("ยังไม่แน่ใจ", P.UNSURE, "เก็บไว้ให้คนตัดสิน ใบแจ้งหนี้นี้จะยังไม่ผ่าน")],
        P.UNSURE, subject=f"{inv.key}:{po_key}", rememberable=False,
        evidence={"columns": ["เอกสาร", "ชื่อผู้ขาย", "เลขประจำตัวผู้เสียภาษี"],
                  "rows": [["ใบแจ้งหนี้", E3C.safe(a.name), a.tax_id or "ไม่ระบุ"], ["ใบสั่งซื้อ", E3C.safe(b["name"]), b["tax_id"] or "ไม่ระบุ"]]},
        facts={"how": s["how"]})


def unit_card(frm: str, pl: B.PoLine, key: str) -> Finding:
    from . import units as U

    a, b = U.canonical(frm), U.canonical(pl.unit)
    item = f" ของ {E3C.safe(pl.description)[:50]}" if pl.description else ""
    return finding(
        "pomatch_unit", "blocking", "preflight", "หน่วยนับ",
        f"PO สั่งเป็น “{b}” แต่ใบแจ้งหนี้เรียกเก็บเป็น “{a}”{item} — 1 {b} เท่ากับกี่ {a} (ตั้งการแปลงหน่วยไว้ในหน้าตั้งค่า agent ได้ "
        f"จะไม่ต้องถามอีก)",
        [("ยังไม่ทราบ ให้คนตรวจ", "unknown", "บรรทัดนี้จะรอคนตัดสิน ไม่เทียบปริมาณและราคา"),
         ("เป็นหน่วยเดียวกัน", "same", f"1 {b} เท่ากับ 1 {a}")],
        "unknown", subject=key[len("pomatch_unit:"):], rememberable=False,
        input={"kind": "text", "hint": f"พิมพ์จำนวน {a} ต่อ 1 {b} เช่น 12 หรือพิมพ์เต็ม เช่น 1 {b} = 12 {a}"},
        evidence={"columns": COLUMNS, "rows": [_po_row(pl)]}, facts={"from": a, "to": b})


def findings(rv) -> list:
    from .review import unit_factor, unit_key

    out = [kind_card(u, t) for u, t in rv.waiting_kind]
    if out:
        return out                      # what each table is comes before anything else can be judged
    m = missing_card(rv)
    if m is not None:
        return [m] + [f for d in rv.docs for f in d.findings]
    if not rv.practice_known:
        out.append(practice_card(rv))
    for d in rv.docs:
        out += d.findings
    asked = set()

    def waiting(res):
        return [c for c in res["groups"]["suspect"] if c["kind"] == "candidate" and c["decision"] is None]

    gr_by_id = {g.id: g for g in rv.gr}
    for k in sorted(rv.links_gr):
        for c in waiting(rv.links_gr[k]):
            g = gr_by_id.get(c["right"][0]["id"])
            f = link_card(c, rv, E3C.safe(g.gr) if g else "", g.date if g else None, gr_by_id, L.NAMES_GR)
            if f is not None and f.key not in asked:
                asked.add(f.key)
                out.append(f)
    for inv in rv.invoices:
        for c in waiting(rv.dup.get(inv.key) or {"groups": {"suspect": []}}):
            f = duplicate_card(c, inv)
            if f.key not in asked:
                asked.add(f.key)
                out.append(f)
        lines = {l.id: l for l in inv.lines}
        for c in waiting(rv.links_inv.get(inv.key) or {"groups": {"suspect": []}}):
            f = link_card(c, rv, inv.label, inv.date, lines, L.NAMES_INV)
            if f is not None and f.key not in asked:
                asked.add(f.key)
                out.append(f)
        for po_key, s in sorted((rv.supplier_of.get(inv.key) or {}).items()):
            if s["verdict"] == P.UNSURE and s["how"] != "unknown":
                out.append(supplier_card(inv, po_key, s))
        for l in inv.lines:
            hit = rv.po_of.get(l.id)
            if not hit or not l.unit or not hit[0].unit:
                continue
            factor, how = unit_factor(rv, l.unit, hit[0])
            key = unit_key(l.unit, hit[0])
            if factor is None and how == "unknown" and key not in asked and key not in rv.answers:
                asked.add(key)
                out.append(unit_card(l.unit, hit[0], key))
    return out
