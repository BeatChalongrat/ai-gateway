"""compute — the bank reconciliation statement, and whether it ties.

Deterministic, Decimal only. The same review as preflight (checks/review.py) with the answers in
it; the pairs come from the E3 engine, the rest is addition:

    balance per bank statement                      balance per books
    + deposits in transit                           + interest and receipts not yet recorded
    − outstanding cheques and payments              − fees, tax withheld, returned cheques,
    ± the bank's errors                               direct debits, payments not yet recorded
    = adjusted bank balance                         ± posting errors
                                                    = adjusted book balance

THE GATE: the two adjusted balances are equal to the satang, or the round is not a success. It
then says "ยังไม่ลงตัว ต่างกัน X บาท", why as far as the files show, and what to do next — and the
working paper is still delivered, marked as a draft that does not tie. Nothing is forced to tie: a
row nobody has classified is in neither adjusted balance.

computed.json (schema ``bank.reconciliation.v1``):
  account, company, period, as_of        whose account, which month, cut off when
  balances{bank, book}                   opening and closing, the cell each was read from, breaks
                                         in the statement's running balance
  match                                  the E3 result: the four groups, tier and pass of every
                                         pair, who confirmed it
  items[]                                every reconciling item: side, type, amount, age, evidence
  statement                              the lines above with their totals, the difference, tied
  gate{passed, code, what, next}         read by the runner: not passed → the round is not a success
  journal[]                              drafted entries for the book side — drafts, nothing is posted
  ageing, carried_in, carried_out, cutoff
  run_exceptions[]                       what a person must decide or look at (open ones block sign-off)
  not_included[]                         files of the round that were not used, and how to bring one back
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from ....engine import thai as TH
from ....engine.e3 import cards as E3C
from ..checks import classify as C
from ..checks.cards import as_written
from ..checks.review import NAMES, SIDE_TH, ZERO, Review, account_settings, line_of, review

SCHEMA = "bank.reconciliation.v1"
SCRIPT = "packages/bank_reconciliation/scripts/reconcile.py"
OUTSTANDING = ("outstanding_cheque", "outstanding_payment", "deposit_in_transit")
ACCOUNTS = {
    "bank_fee": "ค่าธรรมเนียมธนาคาร", "interest": "ดอกเบี้ยรับ", "wht_interest": "ภาษีเงินได้ถูกหัก ณ ที่จ่าย",
    "returned_cheque": "ลูกหนี้การค้า (เช็คคืน)", "direct_debit": "ค่าใช้จ่ายหรือเจ้าหนี้ที่หักบัญชี (ระบุบัญชี)",
    "unrecorded_receipt": "บัญชีพักรอตรวจสอบ", "unrecorded_payment": "บัญชีพักรอตรวจสอบ",
    "posting_error": "บัญชีคู่ของรายการที่บันทึกคลาดเคลื่อน (ระบุบัญชี)", "book_error": "บัญชีคู่ของรายการที่บันทึกผิด (ระบุบัญชี)",
}
REASON_TH = {
    "match_unconfirmed": "คู่ที่เสนอยังไม่มีคนยืนยัน", "possible_duplicate": "อาจบันทึกซ้ำ", "unclassified_item": "รายการที่ยังไม่ได้จัดประเภท",
    "cut_off": "บันทึกข้ามงวด", "stale_cheque": "เช็คค้างนานเกิน 6 เดือน", "aged_item": "รายการค้างนานเกิน 60 วัน",
    "statement_gap": "ยอดคงเหลือในรายการเดินบัญชีไม่ต่อเนื่อง", "row_without_date": "แถวที่อ่านวันที่ไม่ได้",
    "posting_error": "บันทึกบัญชีคลาดเคลื่อน", "bank_error": "ธนาคารบันทึกคลาดเคลื่อน", "unusual_wht_rate": "อัตราภาษีหัก ณ ที่จ่ายไม่ใช่อัตราปกติ",
    "opening_unexplained": "ยอดยกมาต้นงวดยังไม่มีคำอธิบาย", "balance_unknown": "ไม่ทราบยอดคงเหลือ",
}
CHOICES = {
    "match_unconfirmed": [("เป็นรายการเดียวกัน", "match"), ("คนละรายการ", "separate")],
    "possible_duplicate": [("บันทึกซ้ำ ต้องกลับรายการ", "duplicate"), ("ไม่ซ้ำ เป็นคนละรายการ", "distinct")],
    "unclassified_item": [("เป็นรายการกระทบยอด", "accept"), ("ต้องแก้ไขก่อน", "fix")],
    "cut_off": [("บันทึกถูกงวดแล้ว", "accept"), ("ต้องปรับให้อยู่ในงวดนี้", "adjust")],
    "stale_cheque": [("ยกเลิกเช็คและกลับรายการ", "cancel"), ("ออกเช็คใหม่ให้ผู้รับ", "reissue"), ("ยังคงค้างไว้", "keep")],
    "statement_gap": [("ขอรายการเดินบัญชีฉบับเต็มจากธนาคาร", "ask_bank"), ("ตรวจแล้ว ใช้ได้", "accept")],
    "row_without_date": [("แก้วันที่ในไฟล์แล้วส่งใหม่", "fix"), ("ไม่ใช่รายการ ข้ามได้", "ignore")],
    "opening_unexplained": [("จะส่งรายการค้างยกมา", "send"), ("ตรวจยอดยกมาแล้ว", "accept")],
    "balance_unknown": [("จะส่งไฟล์ที่มียอดคงเหลือ", "send")],
}


def f2(v) -> str:
    return f"{Decimal(v):.2f}"


def _item(n: int, side: str | None, kind: str, ln, effect: Decimal | None, *, by: str = "rule", key: str | None = None,
          end: _dt.date, sources: list | None = None, note: str = "") -> dict:
    age = (end - ln.date).days
    return {"id": f"i{n:03d}", "side": side, "type": kind, "type_label": C.TYPE_TH[kind],
            "date": ln.date.isoformat(), "ref": ln.ref, "description": ln.text, "amount": f2(ln.amount),
            "effect": None if effect is None else f2(effect), "age_days": age, "bucket": C.bucket(age),
            "bucket_label": C.BUCKET_TH[C.bucket(age)], "carried": ln.side == "carried", "row_id": ln.id, "from": ln.side,
            "sources": sources or [ln.source()], "decided_by": by, "decision_key": key, "note": note}


def items_of(rv: Review) -> list[dict]:
    g = rv.match["groups"]
    said = E3C.lone_answers(rv.answers)
    ask = rv.tolerance.ask_above
    out: list[dict] = []
    n = lambda: len(out) + 1   # noqa: E731
    for row in g["missing"]:                                   # the ledger has it, the statement does not
        ln = line_of(rv, row["id"])
        a = said.get(ln.id)
        kind = C.of_book_row(ln.cheque, ln.amount, ln.carried_kind)
        key = f"{E3C.CODE_LONE}:{ln.id}" if a is not None else None
        if a == "book_error":
            out.append(_item(n(), C.BOOK_SIDE, "book_error", ln, -ln.amount, by="person", key=key, end=rv.end))
        elif a == "timing":
            out.append(_item(n(), C.BANK_SIDE, kind, ln, ln.amount, by="person", key=key, end=rv.end))
        elif a is not None or abs(ln.amount) >= ask:
            out.append(_item(n(), None, "unclassified", ln, None, by="person" if a is not None else "rule", key=key, end=rv.end,
                             note=f"น่าจะเป็น{C.TYPE_TH[kind]} แต่ยังไม่มีคนยืนยัน"))
        else:
            out.append(_item(n(), C.BANK_SIDE, kind, ln, ln.amount, end=rv.end))
    extra = [line_of(rv, r["id"]) for r in g["extra"]]          # the statement has it, the ledger does not
    interest_on = {}
    for ln in extra:
        if C.of_bank_row(ln.text, ln.amount) == "interest":
            interest_on[ln.date] = interest_on.get(ln.date, ZERO) + ln.amount
    for ln in extra:
        a = said.get(ln.id)
        kind = C.of_bank_row(ln.text, ln.amount)
        if kind == "unrecorded_payment" and C.is_tax_on(ln.text, ln.amount, interest_on.get(ln.date, ZERO)):
            kind = "wht_interest"
        key = f"{E3C.CODE_LONE}:{ln.id}" if a is not None else None
        if a == "bank_error":
            out.append(_item(n(), C.BANK_SIDE, "bank_error", ln, -ln.amount, by="person", key=key, end=rv.end))
        elif a == "book":
            out.append(_item(n(), C.BOOK_SIDE, kind, ln, ln.amount, by="person", key=key, end=rv.end))
        elif a is not None or abs(ln.amount) >= ask:
            out.append(_item(n(), None, "unclassified", ln, None, by="person" if a is not None else "rule", key=key, end=rv.end,
                             note=f"น่าจะเป็น{C.TYPE_TH[kind]} แต่ยังไม่มีคนยืนยัน"))
        else:
            out.append(_item(n(), C.BOOK_SIDE, kind, ln, ln.amount, end=rv.end))
    for m in g["matched"]:                                     # a confirmed pair whose amounts differ
        diff = Decimal(m["difference"])                         # the statement's amount − the ledger's
        if diff == ZERO:
            continue
        first = line_of(rv, m["right"][0]["id"])
        it = _item(n(), C.BOOK_SIDE, "posting_error", first, diff, by=m["confirmed"], key=m["decision_key"], end=rv.end,
                   sources=[{k: r[k] for k in ("file", "sheet", "row", "cells")} for r in m["left"] + m["right"]],
                   note=f"ธนาคาร {TH.fmt_money(Decimal(m['right_total']))} บาท · สมุดบัญชี {TH.fmt_money(Decimal(m['left_total']))} บาท")
        it.update(amount=f2(diff), pair=m["pair"])
        out.append(it)
    return out


def _lines(items: list[dict], side: str) -> list[dict]:
    order = (["deposit_in_transit", "outstanding_cheque", "outstanding_payment", "bank_error"] if side == C.BANK_SIDE else
             ["interest", "unrecorded_receipt", "wht_interest", "bank_fee", "returned_cheque", "direct_debit", "unrecorded_payment",
              "posting_error", "book_error"])
    out = []
    for kind in order:
        mine = [i for i in items if i["side"] == side and i["type"] == kind]
        if mine:
            total = sum((Decimal(i["effect"]) for i in mine), ZERO)
            out.append({"type": kind, "label": C.TYPE_TH[kind], "count": len(mine), "amount": f2(total),
                        "sign": "บวก" if total >= ZERO else "หัก", "items": [i["id"] for i in mine]})
    return out


def statement_of(rv: Review, items: list[dict]) -> dict:
    out = {}
    for side in (C.BANK_SIDE, C.BOOK_SIDE):
        b = rv.balances[side]
        lines = _lines(items, side)
        adj = sum((Decimal(x["amount"]) for x in lines), ZERO)
        out[side] = {"balance": None if b.closing is None else f2(b.closing), "balance_from": b.closing_from,
                     "balance_cell": b.closing_cell, "lines": lines, "adjustments": f2(adj),
                     "adjusted": None if b.closing is None else f2(b.closing + adj)}
    a, k = out["bank"]["adjusted"], out["book"]["adjusted"]
    out["difference"] = None if a is None or k is None else f2(Decimal(a) - Decimal(k))
    out["tied"] = out["difference"] == "0.00"
    return out


def gate_of(rv: Review, st: dict, items: list[dict]) -> dict:
    """Whether the reconciliation ties — and when it does not, how far off, why as far as the files
    show, and what to do next."""
    if st["tied"]:
        return {"passed": True, "code": "tied", "difference": "0.00", "what": "ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชีเท่ากัน",
                "next": "", "reasons": []}
    reasons, nexts = [], []
    g = rv.match["groups"]
    for side in ("bank", "book"):
        if rv.balances[side].closing is None:
            reasons.append(f"ไม่ทราบยอดคงเหลือสิ้นงวดตาม{SIDE_TH[side]}")
            nexts.append(f"ส่ง{SIDE_TH[side]}ที่มียอดคงเหลือหรือยอดยกมา หรือพิมพ์ยอดคงเหลือเมื่อ agent ถาม")
    loose = [i for i in items if i["type"] == "unclassified"]
    if loose:
        total = sum((abs(Decimal(i["amount"])) for i in loose), ZERO)
        reasons.append(f"มี {len(loose)} รายการที่ไม่มีคู่และยังไม่มีคนบอกว่าเป็นอะไร รวม {TH.fmt_money(total)} บาท")
        nexts.append("ดูชีต “รายการกระทบยอด” ส่วนที่ยังไม่ได้จัดประเภท แล้วเริ่มรอบใหม่และตอบคำถามของรายการเหล่านั้น")
    dup = [s for s in g["suspect"] if s["kind"] == "possible_duplicate"]
    if dup:
        reasons.append(f"มี {len(dup)} รายการที่อาจบันทึกซ้ำ")
        nexts.append("ตรวจรายการที่อาจบันทึกซ้ำในชีต “ข้อยกเว้น” ถ้าซ้ำจริงให้กลับรายการในสมุดบัญชี แล้วส่งไฟล์ใหม่")
    gap = opening_gap(rv)
    if gap is not None and gap != ZERO:
        reasons.append(f"ยอดยกมาต้นงวดของสองฝั่งต่างกัน {TH.fmt_money(abs(gap))} บาท หลังหักรายการค้างยกมาที่ส่งมาแล้ว")
        nexts.append("ส่งรายการค้างยกมาจากเดือนก่อน (ชีต “รายการค้างยกไป” ของงบกระทบยอดเดือนก่อน) มาพร้อมกับไฟล์ทั้งสอง")
    if rv.balances["bank"].gaps:
        reasons.append(f"ยอดคงเหลือในรายการเดินบัญชีไม่ต่อเนื่อง {len(rv.balances['bank'].gaps)} จุด อาจมีรายการขาดหาย")
        nexts.append("ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร")
    if rv.unread:
        reasons.append(f"มี {len(rv.unread)} แถวที่มีจำนวนเงินแต่อ่านวันที่ไม่ได้ จึงไม่ได้นำมาใช้")
        nexts.append("แก้วันที่ของแถวเหล่านั้นในไฟล์ แล้วส่งใหม่")
    if not reasons:
        reasons.append("ไฟล์ที่ส่งมาไม่ได้บอกสาเหตุ: ตรวจว่ารายการเดินบัญชีและสมุดบัญชีเป็นของบัญชีเดียวกันและงวดเดียวกัน")
        nexts.append("ตรวจยอดยกมาและยอดคงเหลือของทั้งสองไฟล์ แล้วส่งไฟล์ของงวดเดียวกันมาใหม่")
    diff = st["difference"]
    what = "ยังไม่ลงตัว" + (f" ต่างกัน {TH.fmt_money(abs(Decimal(diff)))} บาท" if diff is not None else ": " + reasons[0])
    return {"passed": False, "code": "not_tied", "difference": diff, "what": what, "reasons": reasons,
            "next": nexts[0], "next_steps": nexts}


def opening_gap(rv: Review) -> Decimal | None:
    """The opening balances, once the items carried in are counted: what nothing explains."""
    bank, book = rv.balances["bank"].opening, rv.balances["book"].opening
    if bank is None or book is None:
        return None
    return bank + sum((ln.amount for ln in rv.carried), ZERO) - book


# ── drafted entries ──────────────────────────────────────────────────────

def journal_of(rv: Review, items: list[dict]) -> list[dict]:
    """One drafted entry per book-side item; interest and the tax withheld on it, of the same day,
    in one entry. Every entry balances. Drafts: a person reads and posts them."""
    code = rv.gl_code or account_settings(rv.defaults)["gl_code"]
    bank_name = " ".join(x for x in ("เงินฝากธนาคาร", rv.account.get("bank"), rv.account.get("number")) if x)
    mine = [i for i in items if i["side"] == C.BOOK_SIDE]
    out, used = [], set()

    def entry(date: str, memo: str, parts: list, ids: list, note: str = "") -> None:
        lines = []
        for account, amount, acc_code in parts:            # amount > 0 is a debit
            lines.append({"account": account, "code": acc_code, "debit": f2(amount) if amount > ZERO else "0.00",
                          "credit": f2(-amount) if amount < ZERO else "0.00"})
        out.append({"id": f"ร่าง-{len(out) + 1:02d}", "date": date, "memo": memo, "lines": lines, "items": ids, "draft": True,
                    "note": note})

    for i in mine:
        if i["id"] in used:
            continue
        amount = Decimal(i["effect"])
        if i["type"] == "interest":
            tax = [t for t in mine if t["type"] == "wht_interest" and t["date"] == i["date"] and t["id"] not in used]
            withheld = sum((-Decimal(t["effect"]) for t in tax), ZERO)
            parts = [(bank_name, amount - withheld, code)]
            note = ""
            if withheld > ZERO:
                parts.append((ACCOUNTS["wht_interest"], withheld, ""))
                rate = (withheld * 100 / amount).quantize(Decimal("0.01"))
                note = f"ภาษีหัก ณ ที่จ่าย {TH.fmt_money(rate)}% ของดอกเบี้ย — เก็บหนังสือรับรองการหักภาษีจากธนาคาร"
                if not any(abs(rate - r) <= Decimal("0.05") for r in C.WHT_RATES):
                    note += " (อัตรานี้ไม่ใช่ 1% หรือ 15% ตรวจกับธนาคาร)"
            parts.append((ACCOUNTS["interest"], -amount, ""))
            used.update([i["id"]] + [t["id"] for t in tax])
            entry(i["date"], "บันทึกดอกเบี้ยรับจากธนาคาร" + (" และภาษีที่ถูกหัก" if withheld > ZERO else ""), parts,
                  [i["id"]] + [t["id"] for t in tax], note)
            continue
        if i["type"] == "wht_interest" and any(t["type"] == "interest" and t["date"] == i["date"] for t in mine):
            continue                                        # entered with its interest
        used.add(i["id"])
        other = ACCOUNTS[i["type"]]
        memo = {"book_error": "กลับรายการที่บันทึกผิด", "posting_error": "ปรับรายการที่บันทึกยอดคลาดเคลื่อน"}.get(
            i["type"], f"บันทึก{i['type_label']}ตามรายการเดินบัญชี")
        entry(i["date"], f"{memo}: {E3C.safe(i['description'])[:80]}".rstrip(": "),
              [(bank_name, amount, code), (other, -amount, "")], [i["id"]],
              "ระบุบัญชีคู่ก่อนบันทึก" if "ระบุบัญชี" in other or other == "บัญชีพักรอตรวจสอบ" else "")
    return out


# ── what a person must look at ───────────────────────────────────────────

def _exceptions(rv: Review, items: list[dict], st: dict, journal: list[dict]) -> list[dict]:
    out = []

    def add(code, severity, subject, source, detail, action):
        # the first row is where the queue points: the file and sheet, the row, the cell
        first = (source.get("rows") or [{}])[0]
        cells = first.get("cells") if isinstance(first.get("cells"), dict) else {}
        source = dict(source, sheet_name=" · ".join(str(x) for x in (first.get("file"), first.get("sheet")) if x),
                      row=first.get("row"), cell=cells.get("amount") or cells.get("in") or cells.get("out") or cells.get("debit")
                      or cells.get("credit") or cells.get("balance") or first.get("cell"))
        out.append({"reason_code": code, "reason": REASON_TH[code], "severity": severity, "subject": subject,
                    "source_ref": source, "detail": detail, "proposed_action": action,
                    "choices": [{"label": l, "value": v} for l, v in CHOICES.get(code, [])] if severity == "review" else []})

    def where(rows):
        return {"rows": [{k: r.get(k) for k in ("file", "sheet", "row", "cells", "date", "amount", "ref")} for r in rows]}

    g = rv.match["groups"]
    for s in g["suspect"]:
        rows = s["left"] + s["right"]
        if s["kind"] == "possible_duplicate":
            r = rows[0]
            add("possible_duplicate", "review", s["pair"], where(rows),
                f"{NAMES[r['side']]}: {E3C.line(r)} มีเลขอ้างอิงและยอดเดียวกับรายการที่จับคู่ไปแล้ว",
                "ตรวจว่าบันทึกซ้ำหรือไม่ ถ้าซ้ำให้กลับรายการ")
        else:
            add("match_unconfirmed", "review", s["pair"], where(rows),
                f"สมุดบัญชี {len(s['left'])} รายการ รวม {TH.fmt_money(abs(Decimal(s['left_total'])))} บาท กับรายการเดินบัญชี "
                f"{len(s['right'])} รายการ รวม {TH.fmt_money(abs(Decimal(s['right_total'])))} บาท ({s['pass_label']})",
                "ยืนยันว่าเป็นรายการเดียวกันหรือไม่ ทั้งสองฝั่งยังไม่ถูกนับเป็นรายการกระทบยอด")
    for i in items:
        src = {"rows": [dict(s, date=i["date"], amount=i["amount"], ref=i["ref"]) for s in i["sources"]]}
        line = f"{E3C.date_th(i['date'])} “{E3C.safe(i['description'])[:60]}” {TH.fmt_money(abs(Decimal(i['amount'])))} บาท"
        if i["type"] == "unclassified":
            add("unclassified_item", "review", i["row_id"], src, f"{line} · {i['note']}",
                "บอกว่ารายการนี้คืออะไร งบกระทบยอดจะยังไม่ลงตัวจนกว่าจะจัดประเภท")
        elif i["type"] == "outstanding_cheque" and i["age_days"] > C.STALE_DAYS:
            add("stale_cheque", "review", i["row_id"], src, f"{line} ค้างมา {i['age_days']} วัน เช็คมีอายุ 6 เดือน",
                "ยกเลิกเช็คและกลับรายการ หรือออกเช็คใหม่ให้ผู้รับ")
        elif i["type"] in OUTSTANDING and i["age_days"] > 60:
            add("aged_item", "info", i["row_id"], src, f"{line} ค้างมา {i['age_days']} วัน", "ติดตามกับผู้รับเงินหรือธนาคาร")
        elif i["type"] == "posting_error":
            add("posting_error", "info", i.get("pair") or i["row_id"], src, f"{line} · {i['note']}",
                "ตรวจร่างรายการปรับปรุงก่อนบันทึก")
        elif i["type"] == "bank_error":
            add("bank_error", "info", i["row_id"], src, line, "แจ้งธนาคารให้แก้รายการ")
    for p in rv.cutoff_pairs:
        rows = p["left"] + p["right"]
        outside = [r for r in rows if not (rv.start.isoformat() <= (r["date"] or "") <= rv.end.isoformat())]
        o = outside[0] if outside else rows[0]
        add("cut_off", "review", "~".join(r["id"] for r in rows), where(rows),
            f"{NAMES[o['side']]}: {E3C.line(o)} ลงวันที่นอกงวด แต่ตรงกับรายการของอีกฝั่งที่อยู่ในงวดนี้",
            "ตรวจว่าควรบันทึกในงวดใด รายการในงวดนี้ยังนับเป็นรายการกระทบยอด")
    for gp in rv.balances["bank"].gaps:
        add("statement_gap", "review", f"{gp['file']}:{gp['row']}", {"rows": [{"file": gp["file"], "sheet": gp["sheet"], "row": gp["row"]}]},
            f"แถว {gp['row']}: ยอดคงเหลือควรเป็น {TH.fmt_money(Decimal(gp['expected']))} บาท แต่ไฟล์แสดง "
            f"{TH.fmt_money(Decimal(gp['found']))} บาท", "ขอรายการเดินบัญชีฉบับเต็มจากธนาคาร อาจมีรายการขาดหาย")
    for u in rv.unread:
        add("row_without_date", "review", f"{u['file']}:{u['row']}", {"rows": [{"file": u["file"], "sheet": u["sheet"], "row": u["row"]}]},
            f"{SIDE_TH[u['side']]} แถว {u['row']}: มีจำนวนเงิน แต่ช่องวันที่เขียนว่า “{E3C.safe(u['text'])}”",
            "แก้วันที่ในไฟล์แล้วส่งใหม่")
    gap = opening_gap(rv)
    if gap is not None and gap != ZERO:
        add("opening_unexplained", "review", "opening", {"rows": [c for c in (rv.balances["bank"].opening_cell,
                                                                           rv.balances["book"].opening_cell) if c]},
            f"ยอดยกมาตามธนาคาร {TH.fmt_money(rv.balances['bank'].opening)} บาท ตามบัญชี {TH.fmt_money(rv.balances['book'].opening)} บาท "
            f"รายการค้างยกมาอธิบายไม่ได้ {TH.fmt_money(abs(gap))} บาท", "ส่งรายการค้างยกมาจากเดือนก่อน")
    for side in ("bank", "book"):
        if rv.balances[side].closing is None:
            add("balance_unknown", "review", side, {"rows": []}, f"ไม่ทราบยอดคงเหลือสิ้นงวดตาม{SIDE_TH[side]}",
                f"ส่ง{SIDE_TH[side]}ที่มียอดคงเหลือ")
    for j in journal:
        if "ไม่ใช่ 1% หรือ 15%" in j["note"]:
            add("unusual_wht_rate", "info", j["id"], {"rows": []}, j["note"], "ตรวจอัตราภาษีกับธนาคาร")
    return out


def _not_included(rv: Review) -> list[dict]:
    out = []
    for u, why in rv.others:
        if why == "set_aside":
            key = f"bankrec_side:{u.key}"
            out.append({"doc_key": u.key, "name": u.name, "code": "set_aside", "reason": "ยังไม่ได้บอกว่าไฟล์นี้เป็นของฝั่งใด",
                        "detail": "ไฟล์มีคอลัมน์เดบิตและเครดิต แต่ไม่มีอะไรบอกว่าเป็นของธนาคารหรือของบริษัท",
                        "how": "นำกลับมารวมได้: ต้องตอบก่อนว่าไฟล์นี้เป็นของฝั่งใด", "can_restore": True,
                        "restore": {"set": {}, "drop": [key]}})
        elif why == "same_file":
            out.append({"doc_key": u.key, "name": u.name, "code": "same_file", "reason": "เป็นไฟล์เดียวกับไฟล์ที่ส่งมาแล้ว",
                        "detail": "ไบต์ตรงกันทุกตัว นับครั้งเดียว", "how": "ไม่ต้องนำกลับ: ไฟล์เดียวกันนี้ใช้อยู่แล้ว",
                        "can_restore": False, "restore": {"set": {}, "drop": []}})
        else:
            out.append({"doc_key": u.key, "name": u.name, "code": why, "reason": u.data.get("say") or "ไม่ได้ใช้ไฟล์นี้",
                        "detail": "agent นี้อ่านรายการเดินบัญชีและสมุดบัญชีที่เป็น Excel หรือ CSV",
                        "how": "ส่งไฟล์ที่ส่งออกจากธนาคารหรือโปรแกรมบัญชีเป็น Excel หรือ CSV แทน", "can_restore": False,
                        "restore": {"set": {}, "drop": []}})
    return out


def compute(uploads, roles, period, *, answers=None, defaults=None, today=None) -> dict:
    rv = review(uploads, defaults=defaults, answers=answers, today=today, period=period)
    if not rv.ready:
        raise ValueError("the reconciliation has no statement or no ledger to work from")
    items = items_of(rv)
    st = statement_of(rv, items)
    gate = gate_of(rv, st, items)
    journal = journal_of(rv, items)
    run_ex = _exceptions(rv, items, st, journal)
    g = rv.match["groups"]
    matched_ids = {}
    for m in g["matched"]:
        for r in m["left"]:
            matched_ids[r["id"]] = m
    carried_in = []
    for ln in rv.carried:
        m = matched_ids.get(ln.id)
        carried_in.append({**ln.out(), "kind": ln.carried_kind, "kind_label": C.TYPE_TH[ln.carried_kind],
                           "cleared": m is not None,
                           "cleared_on": max((r["date"] for r in m["right"]), default=None) if m else None,
                           "pass": m["pass_label"] if m else None, "age_days": (rv.end - ln.date).days})
    outstanding = [i for i in items if i["type"] in OUTSTANDING]
    buckets = []
    for b in ("0-30", "31-60", "61+"):
        mine = [i for i in outstanding if i["bucket"] == b]
        buckets.append({"bucket": b, "label": C.BUCKET_TH[b], "count": len(mine),
                        "amount": f2(sum((abs(Decimal(i["amount"])) for i in mine), ZERO))})
    stale = [i["id"] for i in outstanding if i["type"] == "outstanding_cheque" and i["age_days"] > C.STALE_DAYS]
    label = TH.period_label_th(period)
    review_n = sum(1 for e in run_ex if e["severity"] == "review")
    c = rv.match["counts"]
    totals = {"bank_rows": len(rv.bank), "book_rows": len(rv.book), "carried": len(rv.carried), "matched": c["matched"],
              "by_rule": sum(t["by_rule"] for t in rv.match["tiers"]), "by_person": sum(t["by_person"] for t in rv.match["tiers"]),
              "missing": c["missing"], "extra": c["extra"], "suspect": c["suspect"], "items": len(items),
              "journal": len(journal), "outside_period": len(rv.outside), "run_exceptions": len(run_ex), "review": review_n,
              "difference": st["difference"], "tied": st["tied"],
              "adjusted_bank": st["bank"]["adjusted"], "adjusted_book": st["book"]["adjusted"]}
    summary = (f"กระทบยอดบัญชีธนาคารงวด {label}: " + (gate["what"] if not gate["passed"] else
               f"ลงตัว ยอดหลังปรับปรุง {TH.fmt_money(Decimal(st['bank']['adjusted']))} บาท"))
    fallback = {"summary": summary,
                "movements": [f"จับคู่ได้ {c['matched']} คู่ (ตามกฎ {totals['by_rule']} คู่ · คนยืนยัน {totals['by_person']} คู่)",
                              f"รายการกระทบยอด {len(items)} รายการ · ร่างรายการปรับปรุง {len(journal)} รายการ"],
                "watch": [r for r in gate["reasons"]][:4],
                "next_steps": ([gate["next"]] if gate.get("next") else []) +
                              (["ตรวจร่างรายการปรับปรุงก่อนบันทึกเข้าระบบบัญชี agent ไม่ได้บันทึกให้"] if journal else [])}
    quotable = {"adjusted_bank": st["bank"]["adjusted"] or "", "adjusted_book": st["book"]["adjusted"] or "",
                "difference": st["difference"] or "", "matched": str(c["matched"]), "items": str(len(items))}
    return {
        "schema": SCHEMA, "period": period, "period_label": label, "as_of": rv.end.isoformat(), "period_start": rv.start.isoformat(),
        "period_source": rv.period_source, "prepared_on": (today or rv.today).isoformat(),
        "company": str(rv.defaults.get("company") or ""), "account": {**rv.account, "gl_code": rv.gl_code or ""},
        "balances": {s: rv.balances[s].out() for s in ("bank", "book")},
        "opening_gap": None if opening_gap(rv) is None else f2(opening_gap(rv)),
        "match": as_written(rv, rv.match), "names": NAMES,
        "items": items, "statement": st, "gate": gate, "journal": journal,
        "ageing": {"buckets": buckets, "stale": stale, "stale_days": C.STALE_DAYS},
        "carried_in": carried_in, "carried_out": [i["id"] for i in outstanding],
        "cutoff": {"rows": [ln.out() for ln in rv.outside], "pairs": rv.cutoff_pairs},
        "unread": rv.unread, "run_exceptions": run_ex, "reason_codes": REASON_TH,
        "not_included": _not_included(rv), "assumptions": rv.assumptions, "totals": totals,
        "files": [{"name": u.name, "sha256": u.sha256, "side": s, "sheet": u.data.get("sheet"), "rows": len(u.data.get("rows") or []),
                   "header_row": u.data.get("header_row"), "columns": u.data.get("columns") or {}}
                  for s, fs in (("bank", rv.bank_files), ("book", rv.book_files), ("carried", rv.carried_files)) for u in fs],
        "settings": {"matching": rv.tolerance.to_dict(), "unusable": rv.unusable_settings},
        "index_entries": [], "models": [],
        "quotable": quotable, "quotable_ids": [], "fallback_narrative": fallback,
        # the runner's generic view of computed.json
        "exceptions": run_ex, "excluded": [], "lines": [], "ratios": [], "top_movements": [],
        "citations": _citations(rv, st, items),
        "source": {"bank": [{"name": u.name, "sha256": u.sha256} for u in rv.bank_files],
                   "book": [{"name": u.name, "sha256": u.sha256} for u in rv.book_files],
                   "carried": [{"name": u.name, "sha256": u.sha256} for u in rv.carried_files]},
        "script": SCRIPT,
    }


def _citations(rv: Review, st: dict, items: list[dict]) -> list[dict]:
    """How each figure of the statement was made: the cell a balance was read from, the rows an
    adjustment adds up."""
    def part(file, sheet, row, value, note=""):
        return {"file": file or "", "sheet": sheet or "", "row": row, "value": value, "note": note}

    def c(cid, label, value, formula, sources, aliases):
        return {"id": cid, "label": label, "value": value, "formula": formula, "sources": sources, "left_out": [],
                "aliases": aliases, "script": SCRIPT}

    out = []
    for n, side in enumerate(("bank", "book"), start=1):
        b, s = rv.balances[side], st[side]
        cell = b.closing_cell or {}
        how = {"row": "อ่านจากแถวยอดยกไปของไฟล์", "balance_column": "อ่านจากคอลัมน์ยอดคงเหลือของรายการสุดท้ายในงวด",
               "movement": "ยอดยกมาบวกรายการของงวด", "typed": "ค่าที่คุณพิมพ์", "zero": "ยอดยกมาเป็นศูนย์ บวกรายการของงวด"}.get(
            b.closing_from, "ไม่ทราบ")
        out.append(c(f"b{n}", f"ยอดคงเหลือตาม{SIDE_TH[side]} (บาท)", s["balance"], how,
                     [part(cell.get("file"), cell.get("sheet"), cell.get("row"), s["balance"], cell.get("cell") or "")] if cell else [],
                     [f"ยอดตาม{SIDE_TH[side]}"]))
        mine = [i for i in items if i["side"] == side]
        out.append(c(f"a{n}", f"ยอดหลังปรับปรุงของ{'ธนาคาร' if side == 'bank' else 'บัญชี'} (บาท)", s["adjusted"],
                     "ยอดคงเหลือ บวกหรือหักรายการกระทบยอดของฝั่งนี้ทุกรายการ",
                     [part(x["sources"][0].get("file"), x["sources"][0].get("sheet"), x["sources"][0].get("row"), x["effect"],
                           x["type_label"]) for x in mine],
                     ["ยอดหลังปรับปรุง", f"ยอดที่ถูกต้องของ{'ธนาคาร' if side == 'bank' else 'บัญชี'}"]))
    out.append(c("d1", "ผลต่าง (บาท)", st["difference"], "ยอดหลังปรับปรุงของธนาคาร ลบ ยอดหลังปรับปรุงของบัญชี", [], ["ผลต่าง", "ต่างกัน"]))
    return out


def summarize(computed: dict) -> dict:
    t, g, st = computed["totals"], computed["gate"], computed["statement"]
    return {"period": computed["period"], "period_label": computed["period_label"], "kind": "bank_reconciliation",
            "tied": st["tied"], "difference": st["difference"], "adjusted_bank": st["bank"]["adjusted"],
            "adjusted_book": st["book"]["adjusted"], "bank_balance": st["bank"]["balance"], "book_balance": st["book"]["balance"],
            "matched": t["matched"], "by_rule": t["by_rule"], "by_person": t["by_person"], "items": t["items"],
            "suspect": t["suspect"], "journal": t["journal"], "run_exceptions": t["run_exceptions"], "review": t["review"],
            "gate": {k: g.get(k) for k in ("passed", "code", "what", "next", "difference")},
            "tiers": [{k: x[k] for k in ("tier", "label", "groups", "by_rule", "by_person", "waiting")} for x in computed["match"]["tiers"]],
            "models": [], "excluded_list": [],
            "not_included": [{k: e[k] for k in ("doc_key", "name", "reason", "detail", "how", "can_restore")}
                             for e in computed.get("not_included") or []]}
