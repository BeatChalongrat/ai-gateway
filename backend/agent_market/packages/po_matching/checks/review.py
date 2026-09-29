"""The round judged: what each file is, the PO lines, the receipts, the invoices, and which PO
line every line belongs to.

preflight and compute both run this review — preflight to ask what a person must decide, compute
to test every line from the same judgement with the answers in it. Nothing here reads a clock (the
round's date is handed in) and no model is asked anything.

What a round needs: purchase order lines and at least one invoice. Goods receipts are taken when
they are there. Whether their absence means anything is the install's one setting
(checks/settings.py); until it is set, the round asks once, with the answer its own files suggest.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from ....engine.e2 import cards as E2C
from ....engine.e2 import decisions as E2D
from ....engine.e2.extract import rejudge
from ....engine.e3.rows import name_key, ref_key
from . import book as B
from . import links as L
from . import parties as P
from . import settings as S
from . import units as U
from .inputs import KINDS, Upload

ZERO = Decimal("0")
SCAN_TIERS = ("scan", "photo")
ACCEPTED_TYPES = B.INVOICE_TYPES + B.NOTE_TYPES
# what an invoice of an earlier round is worth to this one, by how that round left it
COUNTED = ("pass", "blocked", "person")     # presented: its quantities are part of what has been billed
_COPY_RANK = {"original": 0, "unmarked": 1, "copy": 2}
_TIER_RANK = {"etax": 0, "digital": 1, "scan": 2, "photo": 3}


@dataclass
class Doc:
    """A document upload judged with the person's answers."""
    upload: Upload
    out: dict
    dec: E2D.Decisions
    status: str                       # included | excluded | pending
    exclusion: dict | None = None
    findings: list = field(default_factory=list)


@dataclass
class Review:
    uploads: list
    defaults: dict
    answers: dict
    today: _dt.date
    settings: S.Settings
    history: list = field(default_factory=list)
    tables: list = field(default_factory=list)          # (upload, table, kind)
    waiting_kind: list = field(default_factory=list)    # (upload, table)
    others: list = field(default_factory=list)          # (upload, why, detail)
    docs: list = field(default_factory=list)
    po: list = field(default_factory=list)
    gr: list = field(default_factory=list)
    confirmed: list = field(default_factory=list)       # receipts confirmed in this system
    invoices: list = field(default_factory=list)
    known: list = field(default_factory=list)           # invoices presented in earlier rounds
    resubmitted: dict = field(default_factory=dict)     # invoice key → the earlier presentation it replaces
    gr_guess: str = S.GR_NO
    gr_files: int = 0
    links_gr: dict = field(default_factory=dict)        # group key → the E3 result
    links_inv: dict = field(default_factory=dict)       # invoice key → the E3 result
    dup: dict = field(default_factory=dict)             # invoice key → the E3 result
    gr_of: dict = field(default_factory=dict)           # PO line id → [(GrLine, link)]
    po_of: dict = field(default_factory=dict)           # invoice line id → (PoLine, link) | None
    waiting_of: dict = field(default_factory=dict)      # invoice line id → the proposal that waits
    supplier_of: dict = field(default_factory=dict)     # invoice key → {po key: (verdict, how, decided by)}
    period: str | None = None
    period_source: str = ""
    findings: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    @property
    def practice_known(self) -> bool:
        return self.settings.gr_practice in (S.GR_YES, S.GR_NO)

    @property
    def has_files(self) -> bool:
        return bool(self.po and (self.invoices or any(d.status == "pending" for d in self.docs)))

    @property
    def ready(self) -> bool:
        return bool(self.po and self.invoices and not self.waiting_kind and self.practice_known)

    def po_by_id(self, rid: str):
        return next((p for p in self.po if p.id == rid), None)


def table_key(u: Upload, t: dict) -> str:
    return f"{u.key}.{t['sheet_index']}"


def history_entries(history: list | None) -> list:
    """The install's history as this agent wrote it: every entry with its detail as a dict."""
    out = []
    for n, h in enumerate(history or []):
        if not isinstance(h, dict):
            continue
        d = h.get("detail")
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                d = None
        e = dict(h, detail=d if isinstance(d, dict) else {})
        e["kind"] = e["detail"].get("kind") or "invoice"
        e["outcome"] = e["detail"].get("outcome") or "pass"
        e["seller_name"] = e["detail"].get("seller_name") or ""
        e["id"] = "h" + hashlib.sha256(f"{e.get('doc_key')}|{e.get('sha256')}|{n}".encode()).hexdigest()[:10]
        out.append(e)
    return out


# ── the review ───────────────────────────────────────────────────────────

def review(uploads: list, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
           today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None,
           period: str | None = None) -> Review:
    from . import cards as K

    rv = Review(uploads=list(uploads), defaults=defaults or {}, answers=answers or {}, today=today or _dt.date(2000, 1, 1),
                settings=S.settings_from(defaults, answers))
    rv.history = history_entries(history)
    _files(rv)
    _documents(rv, proposals)
    _book(rv)
    _known(rv)
    _conversions(rv)
    if rv.po and rv.invoices and not rv.waiting_kind:
        _link(rv)
        _suppliers(rv)
    _period(rv, period)
    rv.findings = K.findings(rv)
    rv.assumptions = _assumptions(rv)
    return rv


def _files(rv: Review) -> None:
    seen: dict = {}
    # by what the file holds, never by the order it was uploaded in or the name it was given
    for u in sorted(rv.uploads, key=lambda u: (u.sha256, u.name)):
        if u.sha256 in seen:
            rv.others.append((u, "same_file", f"ไฟล์เดียวกับ {seen[u.sha256]} (ไบต์ตรงกันทุกตัว)"))
            continue
        seen[u.sha256] = u.name
        if u.kind == "other":
            rv.others.append((u, u.data.get("reason") or "unsupported_type", u.data.get("say") or ""))
        elif u.kind == "tables":
            for t in u.data["tables"]:
                said = rv.answers.get(f"pomatch_kind:{table_key(u, t)}")
                kind = said if said in KINDS + ("unused",) else t["kind"]
                if kind is None:
                    rv.waiting_kind.append((u, t))
                elif kind == "unused":
                    rv.others.append((u, "set_aside", f"แผ่นงาน {t['sheet']}"))
                else:
                    # a table a person named is read as that kind (its columns were read for each kind it could be)
                    rv.tables.append((u, (t.get("read_as") or {}).get(kind, t), kind))
    rv.gr_files = sum(1 for _u, _t, k in rv.tables if k == "gr")
    rv.gr_guess = S.GR_YES if rv.gr_files else S.GR_NO


def _order(u: Upload) -> tuple:
    x = u.data["extraction"]
    d = ((x.get("fields") or {}).get("document_date") or {}).get("normalized")
    doc = x.get("document") or {}
    return (str(d) if d else "9999-12-31", _COPY_RANK.get(doc.get("copy_mark"), 1), _TIER_RANK.get(doc.get("tier"), 9), u.key)


def _documents(rv: Review, proposals: dict | None) -> None:
    from . import cards as K

    org = None
    if rv.settings.company_tax_id:
        org = {"tax_id": rv.settings.company_tax_id, "branch": rv.settings.company_branch or None,
               "name": rv.settings.company or None}
    skip = {u.key for u, why, _ in rv.others if why == "same_file"}
    for u in sorted((u for u in rv.uploads if u.kind == "document" and u.key not in skip), key=_order):
        dec = E2D.for_document(u.key, rv.answers)
        p = (proposals or {}).get(u.key)
        props = []
        if p and u.data["extraction"]["document"]["tier"] in SCAN_TIERS and isinstance(p.get("fields"), dict):
            props = [{"method": f"vision:{p.get('model') or 'model'}", "fields": p["fields"]}]
        out = rejudge(u.data["extraction"], org=org, history=[], today=rv.today, proposals=props,
                      **dec.judge_kwargs(u.data["extraction"].get("fields") or {}))
        dtype = (out.get("document_type") or {}).get("normalized") or "unknown"
        if dec.excluded:
            asked = E2C.findings_for(u.key, u.data["extraction"]) + E2C.findings_for(u.key, out)
            f = next((x for x in asked if x.key == dec.excluded["key"]), None)
            detail = (f.question if f else "") or ""
            if dec.excluded.get("reupload"):
                detail = (detail + " · ขอไฟล์ที่ชัดกว่า").strip(" ·")
            rv.docs.append(Doc(u, out, dec, "excluded", {
                "code": dec.excluded["code"], "detail": detail, "decided_by": "card", "restore": E2D.restore_of(dec)}))
            continue
        if dtype not in ACCEPTED_TYPES and dtype != "unknown":
            rv.docs.append(Doc(u, out, dec, "excluded", {
                "code": "not_an_invoice", "decided_by": "rule", "restore": None,
                "detail": "เอกสารนี้ไม่ใช่ใบกำกับภาษี ใบแจ้งหนี้ ใบลดหนี้ หรือใบเพิ่มหนี้ จึงไม่นำมาตรวจกับใบสั่งซื้อ"}))
            continue
        fs = E2C.findings_for(u.key, out) + K.invalid_cards(u, out, dec)
        rv.docs.append(Doc(u, out, dec, "pending" if fs else "included", findings=fs))


def _book(rv: Review) -> None:
    for u, t, kind in rv.tables:
        if kind == "po":
            rv.po += B.po_lines(u, t)
        elif kind == "gr":
            rv.gr += [g for g in B.gr_lines(u, t) if not g.cancelled]
        else:
            rv.invoices += B.table_invoices(u, t)
    for d in rv.docs:
        if d.status == "included":
            rv.invoices.append(B.document_invoice(d.upload, d.out, fx_rate=d.dec.fx_rate))
    rv.po.sort(key=lambda p: (p.date.isoformat() if p.date else "", p.po_key, p.source.file, p.source.row))
    rv.gr.sort(key=lambda g: (g.date.isoformat() if g.date else "", g.po_key, g.source.file, g.source.row))
    for n, p in enumerate(rv.po):
        p.order = n
        if not p.currency:
            p.currency = rv.settings.currency
    for inv in rv.invoices:
        if not inv.currency:
            inv.currency = rv.settings.currency
    rv.invoices.sort(key=lambda i: i.order)
    # receipts a person confirmed in this system: in earlier rounds (the install's history) and in this one
    by_akey = {p.akey: p for p in rv.po}
    for e in rv.history:
        if e["kind"] != "receipt":
            continue
        d = e["detail"]
        pl = by_akey.get(str(d.get("akey") or ""))
        qty = B.D(d.get("quantity"))
        if pl is None or qty is None:
            continue
        rv.confirmed.append((pl, B.GrLine(id=e["id"], gr="ยืนยันในระบบ", date=B.date(str(e.get("document_date") or "")[:10] or None),
                                           po=pl.po, po_line=pl.line_no, item_code=pl.item_code, description=pl.description,
                                           qty=qty, unit=pl.unit, confirmed_by=str(d.get("confirmed_by") or ""))))
    for k in sorted(rv.answers):
        if not k.startswith("pomatch_receipt:"):
            continue
        pl = by_akey.get(k[len("pomatch_receipt:"):])
        v = rv.answers[k]
        qty = None
        if pl is not None and v == "all":
            qty = pl.qty
        elif pl is not None and isinstance(v, dict):
            from ....engine import cells as C

            n = C.number(v.get("typed"))
            qty = Decimal(n) if n is not None else None
        if pl is None or qty is None or qty < 0:
            continue
        rid = "c" + hashlib.sha256(k.encode()).hexdigest()[:10]
        rv.confirmed.append((pl, B.GrLine(id=rid, gr="ยืนยันในระบบ", date=rv.today, po=pl.po, po_line=pl.line_no,
                                           item_code=pl.item_code, description=pl.description, qty=qty, unit=pl.unit,
                                           confirmed_by="")))


def _known(rv: Review) -> None:
    """The invoices of earlier rounds. One that this round presents again replaces its earlier
    presentation unless that one passed: then this one is the duplicate."""
    batch = {(i.supplier.key, i.number_key): i for i in rv.invoices if i.number_key}
    for e in rv.history:
        if e["kind"] == "receipt":
            continue
        who = P.Party(e.get("seller_name") or "", str(e.get("seller_tax_id") or ""))
        again = batch.get((who.key, ref_key(e.get("document_number"))))
        if again is not None and e["outcome"] != "pass":
            rv.resubmitted[again.key] = e
            continue
        rv.known.append(e)


def _conversions(rv: Review) -> None:
    """The unit conversions a person gave in this round, beside the install's own."""
    from ....engine import cells as C

    for k in sorted(rv.answers):
        if not k.startswith("pomatch_unit:"):
            continue
        frm, _, rest = k[len("pomatch_unit:"):].partition(">")
        to, _, item = rest.partition("@")
        v = rv.answers[k]
        if v == "same":
            rv.settings.conversions.insert(0, {"item": item, "from": to, "to": frm, "factor": "1", "answered": k})
        elif isinstance(v, dict):
            typed = str(v.get("typed") or "")
            n = C.number(typed)
            if n is not None and Decimal(n) > 0:
                # a number alone: how many of the invoice's unit one unit of the PO is
                rv.settings.conversions.insert(0, {"item": item, "from": to, "to": frm, "factor": str(Decimal(n).normalize()),
                                                   "answered": k})
            else:
                got, ok = S.conversions_from(typed)
                for c in got if ok else []:
                    if {c["from"], c["to"]} == {frm, to}:
                        rv.settings.conversions.insert(0, {**c, "item": item, "answered": k})


def _entry_of(inv: B.Invoice) -> dict:
    return {"id": "e" + inv.key.replace("~", "-").replace("+", "-"), "name": inv.name, "sha256": inv.sha256, "doc_key": inv.key,
            "seller_tax_id": inv.supplier.tax_id, "seller_name": inv.supplier.name, "document_number": inv.number,
            "document_date": inv.date.isoformat() if inv.date else None, "total": None if inv.total is None else f"{inv.total:.2f}",
            "kind": inv.doc_type if inv.is_note else "invoice", "outcome": "pass", "detail": {}, "batch": True}


def _link(rv: Review) -> None:
    decisions, keys = L.decisions_from(rv.answers)
    tol = rv.settings.tolerances
    # goods receipts: one run per receipt
    groups: dict = {}
    for g in rv.gr:
        k = (g.source.file if g.source else "", g.source.sheet if g.source else "", ref_key(g.gr) or g.id)
        groups.setdefault(k, []).append(g)
    by_id = {p.id: p for p in rv.po}
    gr_by_id = {g.id: g for g in rv.gr}
    for k in sorted(groups):
        res = L.link_receipt(groups[k], rv.po, tol, decisions, keys)
        rv.links_gr["|".join(k)] = res
        for m in res["groups"]["matched"]:
            pl = by_id[m["left"][0]["id"]]
            for r in m["right"]:
                rv.gr_of.setdefault(pl.id, []).append((gr_by_id[r["id"]], m))
    for pl, g in rv.confirmed:
        rv.gr_of.setdefault(pl.id, []).append((g, {"pass": "confirmed_in_system", "pass_label": "ยืนยันการรับของในระบบ",
                                                   "tier": "exact", "tier_label": "ยืนยันในระบบ", "confirmed": "person",
                                                   "confirmed_by": g.confirmed_by, "decision_key": None, "pair": f"{pl.id}~{g.id}"}))
    # duplicates, then the lines: in the order the invoices are dated
    before = list(rv.known)
    for inv in rv.invoices:
        res = L.find_duplicate(inv, before, tol, decisions, keys)
        rv.dup[inv.key] = res
        before.append(_entry_of(inv))
        lr = L.link_invoice(inv, rv.po, tol, decisions, keys)
        rv.links_inv[inv.key] = lr
        lines = {l.id: l for l in inv.lines}
        for m in lr["groups"]["matched"]:
            pl = by_id[m["left"][0]["id"]]
            for r in m["right"]:
                rv.po_of[r["id"]] = (pl, m)
        for s in lr["groups"]["suspect"]:
            if s["kind"] != "candidate":
                continue
            for r in s["right"]:
                if r["id"] in lines:
                    rv.waiting_of[r["id"]] = s


def is_duplicate(rv: Review, inv: B.Invoice) -> dict | None:
    """The earlier presentation this invoice repeats, by rule or by a person's answer."""
    res = rv.dup.get(inv.key)
    if not res or not res["groups"]["matched"]:
        return None
    return res["groups"]["matched"][0]


def _suppliers(rv: Review) -> None:
    """The supplier on the invoice against the supplier on every PO its lines belong to."""
    by_id = {p.id: p for p in rv.po}
    for inv in rv.invoices:
        pos: dict = {}
        for l in inv.lines:
            hit = rv.po_of.get(l.id)
            if hit:
                pos.setdefault(hit[0].po_key, hit[0])
        lr = rv.links_inv.get(inv.key) or {}
        for k in lr.get("known") or []:
            pl = next((p for p in rv.po if p.po_key == k), None)
            if pl is not None:
                pos.setdefault(k, pl)
        out = {}
        for k, pl in sorted(pos.items()):
            verdict, how = P.compare(pl.supplier, inv.supplier)
            by = "rule"
            if verdict == P.UNSURE:
                said = rv.answers.get(f"pomatch_supplier:{inv.key}:{k}")
                if said in (P.SAME, P.DIFFERENT):
                    verdict, by = said, "person"
                elif said == P.UNSURE:
                    by = "skipped"
            out[k] = {"verdict": verdict, "how": how, "how_label": P.HOW_TH[how], "decided_by": by, "po": pl.po,
                      "po_supplier": pl.supplier.out(), "decision_key": f"pomatch_supplier:{inv.key}:{k}" if by == "person" else None}
        rv.supplier_of[inv.key] = out
    _ = by_id


def _period(rv: Review, period: str | None) -> None:
    if period:
        rv.period, rv.period_source = period, "preflight"
        return
    months: dict = {}
    for inv in rv.invoices:
        if inv.date:
            m = inv.date.isoformat()[:7]
            months[m] = months.get(m, 0) + 1
    if months:
        rv.period, rv.period_source = max(months, key=lambda m: (months[m], m)), "documents"
    else:
        rv.period, rv.period_source = f"{rv.today.year:04d}-{rv.today.month:02d}", "today"


# ── units of a linked line ───────────────────────────────────────────────

def unit_factor(rv: Review, frm: str, pl: B.PoLine) -> tuple[Decimal | None, str]:
    """How many of the PO line's unit one ``frm`` is, and where that comes from."""
    f, how = U.factor(frm, pl.unit, pl.item_code, rv.settings.conversions)
    if f is None and rv.answers.get(unit_key(frm, pl)) == "unknown":
        return None, "person_unknown"
    return f, how


def unit_key(frm: str, pl: B.PoLine) -> str:
    item = B.code_key(pl.item_code)
    return f"pomatch_unit:{U.canonical(frm)}>{U.canonical(pl.unit)}" + (f"@{item}" if item else "")


# ── what the round assumed ───────────────────────────────────────────────

def _assumptions(rv: Review) -> list:
    out = []
    for u, why, detail in rv.others:
        say = {"same_file": "เป็นไฟล์เดียวกับไฟล์ที่ส่งมาแล้ว นับครั้งเดียว", "set_aside": "คุณตอบว่าไม่ใช้แผ่นงานนี้ในรอบนี้",
               "unrecognised_sheet": "ไม่พบหัวคอลัมน์ของใบสั่งซื้อ ใบรับของ หรือใบแจ้งหนี้",
               "unsupported_type": "อ่านชนิดไฟล์นี้ไม่ได้", "unreadable": "เปิดไฟล์ไม่ได้"}.get(why, "ไม่ได้ใช้")
        out.append({"code": "file_not_used", "source": "rule", "text": f"ไม่ได้ใช้ไฟล์ {u.name}: {say}"})
    for k in rv.settings.unusable:
        label = S.SETTING_TH.get(k, S.UNIT_SETTING_TH)
        out.append({"code": "setting_not_usable", "source": "rule", "changes_numbers": True,
                    "text": f"ค่าที่ตั้งไว้สำหรับ “{label}” ใช้ไม่ได้ จึงใช้ค่าตั้งต้นของ agent"})
    if rv.practice_known:
        how = {"settings": "ตามที่ตั้งไว้ใน agent", "answer": "ตามที่คุณตอบ"}.get(rv.settings.gr_from, "")
        if rv.settings.gr_practice == S.GR_YES:
            text = f"บริษัทออกใบรับของ ({how}): รายการสินค้าที่เรียกเก็บโดยยังไม่มีใบรับของเป็นข้อยกเว้นที่ต้องติดตาม"
        else:
            text = f"บริษัทไม่ออกใบรับของ ({how}): รายการที่ไม่มีใบรับของตรวจ 2 ทาง และระบุไว้ทุกบรรทัดว่าตรวจ 2 ทาง"
        out.append({"code": "gr_practice", "source": "answer" if rv.settings.gr_from == "answer" else "rule",
                    "changes_numbers": True, "text": text})
    if not rv.ready:
        return out
    t = rv.settings.tolerances
    from ....engine.thai import fmt_money

    out.append({"code": "tolerance", "source": "rule",
                "text": f"ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกินร้อยละ {fmt_money(t.price_pct)} และไม่เกิน {fmt_money(t.price_abs)} บาทต่อบรรทัด · "
                        f"ปริมาณเกินที่รับจริงได้ไม่เกินร้อยละ {fmt_money(t.qty_pct)} · รับของเกินที่สั่งได้ไม่เกินร้อยละ {fmt_money(t.over_pct)} · "
                        f"ผลต่างจากการปัดเศษไม่เกิน {fmt_money(t.small)} บาท · อัตราภาษีมูลค่าเพิ่มร้อยละ {fmt_money(t.vat_rate)}"})
    carried = sum(1 for _u, tb, _k in rv.tables for r in tb["rows"] if r.get("carried"))
    if carried:
        out.append({"code": "carried_values", "source": "rule",
                    "text": f"มี {carried} แถวที่ไม่ได้พิมพ์เลขที่เอกสาร ผู้ขาย หรือวันที่ซ้ำ จึงใช้ค่าจากบรรทัดแรกของเอกสารเดียวกัน"})
    if rv.resubmitted:
        out.append({"code": "presented_again", "source": "rule", "changes_numbers": True,
                    "text": f"มี {len(rv.resubmitted)} ใบแจ้งหนี้ที่เคยส่งมาแล้วและรอบนั้นยังไม่ผ่าน รอบนี้ตรวจใหม่ และนับยอดสะสมจากรอบนี้แทนรอบเดิม"})
    if rv.confirmed:
        out.append({"code": "receipts_confirmed", "source": "answer", "changes_numbers": True,
                    "text": f"มีการยืนยันรับของในระบบ {len(rv.confirmed)} รายการ ใช้เป็นหลักฐานการรับของของบรรทัดนั้น"})
    return out


def name_of(v) -> str:
    return name_key(v)
