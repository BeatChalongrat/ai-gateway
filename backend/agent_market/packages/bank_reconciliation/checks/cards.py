"""What a person decides in a reconciliation → clarification cards, in the order they are asked.

    bankrec_side:<file>      a file whose columns are only เดบิต/เครดิต and whose side nothing
                             else says: the bank's, the company's, or not used (a skipped card
                             sets the file aside — it is then on the round's "not included" list)
    missing_file             a side with no file at all
    bankrec_account          a ledger that holds several accounts: which one is this bank account
    bankrec_closing:<side>   a balance the file does not show: typed by the person
    bankrec_carried          the opening balances differ and no list of items carried in came
    e3_confirm_all / e3_candidate / e3_unmatched   the match itself (engine/e3/cards.py)

A pair is matched only by a rule of the exact tier or by a person's answer; a skipped card never
matches one. A large row without a partner that nobody classifies stays out of both adjusted
balances, so the reconciliation does not tie until a person has said what it is.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.e3 import cards as E3C
from ....engine.findings import Finding, finding
from ....engine.thai import fmt_money
from . import classify as C

SIDE_OPTIONS = [("รายการเดินบัญชีของธนาคาร", "bank", "เดบิตคือเงินออกจากบัญชี เครดิตคือเงินเข้า"),
                ("สมุดบัญชีของบริษัท", "book", "เดบิตคือเงินเข้าบัญชีธนาคาร เครดิตคือเงินออก"),
                ("ไม่ใช้ไฟล์นี้", "unused", "ตั้งไฟล์นี้ไว้ก่อน ไม่นำมากระทบยอดในรอบนี้")]
BANK_ROW = {"book": "บันทึกเพิ่มในสมุดบัญชี", "bank_error": "ธนาคารบันทึกผิด", "review": "ยังไม่ทราบ ให้คนตรวจ"}
BOOK_ROW = {"timing": "ธนาคารยังไม่ลงรายการ", "book_error": "บันทึกบัญชีผิด", "review": "ยังไม่ทราบ ให้คนตรวจ"}
SPECIFIC = ("bank_fee", "interest", "wht_interest", "returned_cheque", "direct_debit")


def side_card(u) -> Finding:
    d = u.data
    rows = [[r["date_raw"], E3C.safe(r["description"])[:60], r["a"] or "", r["b"] or "", r["balance"] or ""] for r in d["rows"][:5]]
    return finding(
        "bankrec_side", "blocking", "preflight", "ไฟล์ของฝั่งใด",
        f"ไฟล์ {E3C.safe(u.name)} มีคอลัมน์เดบิตและเครดิต แต่ไม่มีอะไรบอกว่าเป็นของธนาคารหรือของบริษัท "
        f"(เดบิตของธนาคารคือเงินออก เดบิตของบริษัทคือเงินเข้า) ไฟล์นี้คืออะไร",
        SIDE_OPTIONS, "unused", subject=u.key, leaves_out=True, rememberable=False,
        evidence={"file": u.name, "sheet": d["sheet"], "columns": ["วันที่", "รายการ", "เดบิต", "เครดิต", "คงเหลือ"], "rows": rows},
        facts={"file": u.name, "layout": d["layout"]})


def missing_card(rv) -> Finding | None:
    want = []
    if not rv.bank_files:
        want.append({"role": "bank", "label": "รายการเดินบัญชีจากธนาคาร (Statement)",
                     "why": "ยอดคงเหลือและรายการฝั่งธนาคารทุกรายการมาจากไฟล์นี้", "required": True, "accept": [".xlsx", ".csv"]})
    if not rv.book_files:
        want.append({"role": "book", "label": "สมุดบัญชีเงินฝากธนาคารของบริษัท (บัญชีแยกประเภทของบัญชีนี้)",
                     "why": "ยอดคงเหลือและรายการฝั่งบริษัททุกรายการมาจากไฟล์นี้", "required": True, "accept": [".xlsx", ".csv"]})
    if not want:
        return None
    names = " และ".join(w["label"] for w in want)
    unused = [u.name for u, why in rv.others if why != "same_file"]
    extra = f" (ไฟล์ {E3C.safe(', '.join(unused[:3]))} ไม่ได้ใช้: ไม่พบหัวคอลัมน์ของรายการเดินบัญชีหรือสมุดบัญชี)" if unused else ""
    return finding("missing_file", "blocking", "preflight", "ขาดไฟล์",
                   f"ยังไม่มี{names}{extra} ผมต้องมีทั้งสองฝั่งก่อนจึงจะกระทบยอดได้",
                   [("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้"),
                    ("ยกเลิกรอบนี้", "cancel", "ปิดรอบนี้ไว้ก่อน")], "wait", kind="request_files",
                   subject="+".join(w["role"] for w in want), request=want, facts={"missing": [w["role"] for w in want]},
                   evidence={"columns": ["ไฟล์ที่ได้รับ", "ใช้เป็น"], "rows": _received(rv)})


def _received(rv) -> list:
    from .review import SIDE_TH

    rows = [[u.name, SIDE_TH["bank"]] for u in rv.bank_files] + [[u.name, SIDE_TH["book"]] for u in rv.book_files]
    rows += [[u.name, "รายการค้างยกมา"] for u in rv.carried_files] + [[u.name, "ไม่ได้ใช้"] for u, _ in rv.others]
    return rows or [["ยังไม่มีไฟล์", ""]]


def _where(cell: dict | None) -> str:
    c = cell or {}
    return " · ".join(str(x) for x in (c.get("file"), c.get("sheet"), f"แถว {c['row']}" if c.get("row") else "", c.get("cell")) if x)


def account_card(rv) -> Finding:
    cands = rv.gl_candidates[:4]
    options = [(f"{c['code']} {E3C.safe(c['name'])}".strip()[:60], c["code"], f"{c['rows']} รายการในไฟล์") for c in cands]
    number = f" เลขที่ {rv.account['number']}" if rv.account.get("number") else ""
    return finding(
        "bankrec_account", "blocking", "preflight", "บัญชีใด",
        f"สมุดบัญชีที่ส่งมามีหลายบัญชี บัญชีใดคือบัญชีเงินฝากธนาคาร{number} ที่จะกระทบยอด "
        f"(ตั้งรหัสบัญชีไว้ในหน้าตั้งค่า agent ได้ จะไม่ต้องถามอีก)",
        options, cands[0]["code"], rememberable=False,
        evidence={"columns": ["รหัสบัญชี", "ชื่อบัญชี", "จำนวนรายการ"], "rows": [[c["code"], c["name"], c["rows"]] for c in cands]},
        facts={"candidates": [c["code"] for c in cands]})


def closing_card(side: str, rv) -> Finding:
    from .review import SIDE_TH

    b = rv.balances[side]
    return finding(
        "bankrec_closing", "blocking", "preflight", "ยอดคงเหลือ",
        f"{SIDE_TH[side]}ที่ส่งมาไม่มียอดคงเหลือและไม่มียอดยกมา ยอดคงเหลือ ณ วันที่ {E3C.date_th(rv.end)} ตาม{SIDE_TH[side]}คือเท่าไร "
        f"(รายการของงวดนี้รวมสุทธิ {fmt_money(b.movement)} บาท)",
        [("ยังไม่ทราบ ทำเท่าที่มี", "unknown", "งบกระทบยอดจะแสดงว่ายังไม่ลงตัว"),
         ("ยอดยกมาเป็นศูนย์", "zero", "บัญชีนี้เริ่มใช้ในงวดนี้ ยอดสิ้นงวดคือผลรวมรายการของงวด")],
        "unknown", subject=side, rememberable=False, input={"kind": "money", "hint": "พิมพ์ยอดคงเหลือ เช่น 1,250,000.00"},
        evidence={"file": ", ".join(u.name for u in (rv.bank_files if side == "bank" else rv.book_files))},
        facts={"side": side, "movement": f"{b.movement:.2f}"})


def carried_card(rv, gap: Decimal) -> Finding:
    bank, book = rv.balances["bank"], rv.balances["book"]
    return finding(
        "bankrec_carried", "blocking", "preflight", "รายการยกมา",
        f"ยอดยกมาต้นงวดตามธนาคาร {fmt_money(bank.opening)} บาท ต่างจากยอดยกมาตามบัญชี {fmt_money(book.opening)} บาท "
        f"อยู่ {fmt_money(abs(gap))} บาท แต่ยังไม่มีรายการค้างยกมาจากเดือนก่อนมาอธิบาย",
        [("ไม่มีไฟล์นี้ ทำเท่าที่มี", "proceed_without", "งบกระทบยอดจะแสดงผลต่างของยอดยกมาไว้ และยังไม่ลงตัว"),
         ("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้")],
        "wait", kind="request_files", subject="carried",
        request=[{"role": "carried", "label": "รายการค้างยกมาจากเดือนก่อน",
                  "why": "เช็คค้างจ่ายและเงินฝากระหว่างทางของเดือนก่อนอธิบายผลต่างของยอดยกมา — ใช้ชีต “รายการค้างยกไป” "
                         "ในงบกระทบยอดของเดือนก่อนที่ agent นี้ทำได้เลย",
                  "required": False, "accept": [".xlsx", ".csv"]}],
        evidence={"columns": ["ฝั่ง", "ยอดยกมาต้นงวด", "อ่านจาก"],
                  "rows": [["ธนาคาร", f"{bank.opening:,.2f}", _where(bank.opening_cell)],
                           ["บัญชี", f"{book.opening:,.2f}", _where(book.opening_cell)]]},
        facts={"opening_bank": f"{bank.opening:.2f}", "opening_book": f"{book.opening:.2f}", "gap": f"{gap:.2f}"})


def lone_card(rv):
    """The card for a large row without a partner, in the words of a bank reconciliation."""
    def make(row: dict, names: dict, moment: str) -> Finding | None:
        amount = Decimal(row["amount"])
        if row["side"] == "right":
            ln = _line(rv, row["id"])
            guess = C.of_bank_row(ln.text if ln else row.get("name") or "", amount)
            return E3C.lone_card(
                row, names, moment, default="review",
                question=f"รายการเดินบัญชี: {E3C.line(_shown(rv, row))} ไม่พบในสมุดบัญชี "
                         f"(ดูจากข้อความ น่าจะเป็น{C.TYPE_TH[guess]}) รายการนี้คืออะไร",
                options=[(BANK_ROW["book"], "book", f"นับเป็น{C.TYPE_TH[guess]} และร่างรายการปรับปรุงให้ตรวจก่อนบันทึก"),
                         (BANK_ROW["bank_error"], "bank_error", "ปรับยอดฝั่งธนาคาร แล้วแจ้งธนาคารให้แก้"),
                         (BANK_ROW["review"], "review", "ยังไม่นับเป็นรายการกระทบยอด งบกระทบยอดจะยังไม่ลงตัว")])
        ln = _line(rv, row["id"])
        guess = C.of_book_row(ln.cheque if ln else "", amount, ln.carried_kind if ln else None)
        return E3C.lone_card(
            row, names, moment, default="review",
            question=f"สมุดบัญชี: {E3C.line(_shown(rv, row))} ไม่พบในรายการเดินบัญชีของงวดนี้ รายการนี้คืออะไร",
            options=[(BOOK_ROW["timing"], "timing", f"นับเป็น{C.TYPE_TH[guess]} ยกไปงวดหน้า"),
                     (BOOK_ROW["book_error"], "book_error", "ร่างรายการกลับรายการให้ตรวจก่อนบันทึก"),
                     (BOOK_ROW["review"], "review", "ยังไม่นับเป็นรายการกระทบยอด งบกระทบยอดจะยังไม่ลงตัว")])
    return make


def _line(rv, rid: str):
    for ln in rv.book + rv.carried + rv.bank:
        if ln.id == rid:
            return ln
    return None


def _shown(rv, row: dict) -> dict:
    """The row with the line as the file wrote it (the engine compares a cleaned text)."""
    ln = _line(rv, row["id"])
    return dict(row, name=ln.text if ln else row.get("name"))


def as_written(rv, result: dict) -> dict:
    """The match with every row's text as its file wrote it — what a person reads on a card and in
    the working paper (the engine compares the text without the words about how money moved)."""
    def fix(r):
        return _shown(rv, r)

    def both(e):
        return dict(e, left=[fix(r) for r in e["left"]], right=[fix(r) for r in e["right"]])
    g = result["groups"]
    return dict(result, groups={
        "matched": [both(m) for m in g["matched"]], "missing": [fix(r) for r in g["missing"]],
        "extra": [fix(r) for r in g["extra"]], "suspect": [both(s) for s in g["suspect"]]})


def findings(rv) -> list:
    out = [side_card(u) for u in rv.waiting_for_side]
    if out:
        return out                      # which file is which comes before anything else can be judged
    m = missing_card(rv)
    if m is not None:
        return [m]
    if rv.waiting_for_account:
        return [account_card(rv)]
    if not rv.ready:
        return []
    for side in ("bank", "book"):
        if rv.balances[side].closing is None and f"bankrec_closing:{side}" not in rv.answers:
            out.append(closing_card(side, rv))
    bank, book = rv.balances["bank"], rv.balances["book"]
    if bank.opening is not None and book.opening is not None and not rv.carried_files \
            and bank.opening != book.opening and rv.answers.get("bankrec_carried") != "proceed_without":
        out.append(carried_card(rv, bank.opening - book.opening))
    out += E3C.findings(as_written(rv, rv.match), rv.answers, rv_names(), tolerance=rv.tolerance, lone=lone_card(rv))
    return out


def rv_names() -> dict:
    from .review import NAMES

    return NAMES
