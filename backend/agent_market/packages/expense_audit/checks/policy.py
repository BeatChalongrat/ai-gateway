"""The expense policy: the install's structured table is the only source of a limit.

Three inputs, three roles — kept apart on purpose:

  * the policy table (install default ``policy``, schema ``am2.expense_policy.v1``, the shape of
    fixtures/expense/policy.json): every limit, threshold, rate and prohibited word a rule uses.
    A run reads the stored table and nothing else for a number. No table (or an incomplete one)
    means no audit: preflight asks for it, compute refuses — a limit is never guessed;
  * the holiday calendar (install default ``holidays``). The holiday check fails CLOSED (AM3): it
    runs only on the company's own calendar, confirmed by a person —
    {"schema": "am3.holidays.v1", "kind": "company", "confirmed": true, "confirmed_by": …,
    "confirmed_at": …, "dates": {ISO date: name}}. Anything else (no setting, an AM2 plain map,
    another kind, not confirmed) turns the holiday check off and the output says so; weekends
    are still checked (they need no calendar). Bank holidays, government holidays and a
    company's own holidays are three different calendars and not interchangeable: for an
    expense audit only the company's is right. The package's data/holidays_th.json is the
    government calendar, a starting point a company may copy and confirm — never used by a rule;
  * a KB policy document (a .docx / .txt uploaded with the run): **context only**. Its paragraphs
    are quoted next to the category they talk about, a number in them that differs from the table
    is reported as a difference ("the table wins"), and nothing in it ever becomes a limit.

``policy_from_xlsx`` reads an owner's policy sheet (fixtures/expense/policy.xlsx) into the same
structure at install time and lists what the sheet does not say; runs never read a sheet.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ....engine import thai as TH
from ....engine.e2 import thai_text as T
from ..words import W

SCHEMA = "am2.expense_policy.v1"
Q2 = Decimal("0.01")
WEEKEND_RULES = ("flag", "exception", "allow")      # flag = a card for a person; exception = a run exception
VAT_DOCUMENTS = ("full_tax_invoice", "abbreviated_tax_invoice")
CODE_RX = re.compile(r"[A-Za-z0-9_\-]+")


@dataclass
class Category:
    code: str
    label: str
    per_claim: Decimal | None = None
    per_person_month: Decimal | None = None
    needs_purpose: bool = False
    wht_rate: Decimal | None = None       # percent
    wht_from: Decimal | None = None


@dataclass
class Policy:
    table: dict                           # exactly as stored on the install
    source: str                           # install | package_sample (the manifest's starting table, not yet edited)
    sha256: str
    currency: str
    receipt_required_above: Decimal
    approval_threshold: Decimal
    categories: dict
    prohibited: list
    weekend_holiday: str
    vat_claim_requires: str

    def category_of(self, raw) -> str | None:
        """A claim's category cell → the table's code: by code, by label, by the label without its
        leading ค่า, or by one part of a label written with "/" (ค่าแท็กซี่/เดินทางในเมือง)."""
        key = _norm(raw)
        if not key:
            return None
        for exact in (lambda c: _norm(c.code) == key, lambda c: _norm(c.label) == key,
                      lambda c: _strip_kha(_norm(c.label)) == _strip_kha(key)):
            hits = [c.code for c in self.categories.values() if exact(c)]
            if len(hits) == 1:
                return hits[0]
        hits = [c.code for c in self.categories.values()
                if any(_strip_kha(p) == _strip_kha(key) for p in _norm(c.label).split("/") if p)]
        return hits[0] if len(hits) == 1 else None


def _norm(s) -> str:
    return "".join(T.arabic(str(s or "")).split()).lower()


def _strip_kha(s: str) -> str:
    return s[3:] if s.startswith("ค่า") and len(s) > 3 else s


def _money(v, where: str, problems: list, *, required: bool = False) -> Decimal | None:
    if v is None or v == "":
        if required:
            problems.append(W("policy.not_set", where=where))
        return None
    if isinstance(v, bool):
        problems.append(W("policy.must_be_money", where=where))
        return None
    t = T.arabic(str(v)).replace(",", "").replace("บาท", "").replace("฿", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        problems.append(W("policy.not_money", where=where, v=v))
        return None
    return Decimal(t).quantize(Q2)


def _rate(v, where: str, problems: list) -> Decimal | None:
    if v is None or v == "":
        return None
    t = T.arabic(str(v)).replace("%", "").strip()
    try:
        r = Decimal(t)
    except InvalidOperation:
        problems.append(W("policy.not_rate", where=where, v=v))
        return None
    if not Decimal(0) < r <= Decimal(100):
        problems.append(W("policy.rate_range", where=where, v=v))
        return None
    return r


def fingerprint(table: dict) -> str:
    return hashlib.sha256(json.dumps(table, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def load_policy(defaults: dict | None) -> tuple[Policy | None, list[str]]:
    """The stored table → a checked Policy, or (None, what is wrong with it). Every money value must be a
    plain amount: "ประมาณ 2,000" is not one, and nothing here reads between the lines."""
    raw = (defaults or {}).get("policy")
    if not raw:
        return None, [W("policy.none")]
    if not isinstance(raw, dict):
        return None, [W("policy.not_table")]
    problems: list[str] = []
    cats_raw = raw.get("categories")
    cats: dict[str, Category] = {}
    if not isinstance(cats_raw, dict) or not cats_raw:
        problems.append(W("policy.no_categories"))
        cats_raw = {}
    for code, c in cats_raw.items():
        where = f"categories.{code}"
        if not CODE_RX.fullmatch(str(code)) or not isinstance(c, dict):
            problems.append(W("policy.bad_code", where=where))
            continue
        cat = Category(code=code, label=str(c.get("label") or code).strip(),
                       per_claim=_money(c.get("per_claim"), f"{where}.per_claim", problems),
                       per_person_month=_money(c.get("per_person_month"), f"{where}.per_person_month", problems),
                       needs_purpose=c.get("needs_purpose") is True,
                       wht_rate=_rate(c.get("wht_rate"), f"{where}.wht_rate", problems),
                       wht_from=_money(c.get("wht_from"), f"{where}.wht_from", problems))
        if cat.wht_rate is not None and cat.wht_from is None and "wht_from" not in c:
            problems.append(W("policy.wht_from", where=where))
        cats[code] = cat
    receipt_above = _money(raw.get("receipt_required_above"), "receipt_required_above", problems, required=True)
    approval = _money(raw.get("approval_threshold"), "approval_threshold", problems, required=True)
    weekend = raw.get("weekend_holiday", "flag")
    if weekend not in WEEKEND_RULES:
        problems.append(W("policy.weekend", weekend=weekend, weekend_rules=", ".join(WEEKEND_RULES)))
    vat_doc = raw.get("vat_claim_requires", "full_tax_invoice")
    if vat_doc not in VAT_DOCUMENTS:
        problems.append(W("policy.vat_doc", vat_doc=vat_doc, vat_documents=", ".join(VAT_DOCUMENTS)))
    currency = raw.get("currency", "THB")
    if currency != "THB":
        problems.append(W("policy.currency", currency=currency))
    words = raw.get("prohibited_merchants", [])
    if not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words):
        problems.append(W("policy.prohibited"))
        words = []
    if problems:
        return None, problems
    return Policy(table=raw, source="package_sample" if raw.get("source") == "package_sample" else "install",
                  sha256=fingerprint(raw), currency=currency, receipt_required_above=receipt_above,
                  approval_threshold=approval, categories=cats, prohibited=[w.strip() for w in words],
                  weekend_holiday=weekend, vat_claim_requires=vat_doc), []


# ── the holiday calendar ─────────────────────────────────────────────────────────

@dataclass
class Calendar:
    dates: dict                            # date → name (empty while the check is off)
    source: str                            # company | none
    years: set = field(default_factory=set)
    note: str = ""
    problems: list = field(default_factory=list)
    active: bool = False                   # the holiday check runs only on a confirmed company calendar
    reason: str = ""                       # why it is off: not_set | not_company | unconfirmed
    confirmed_by: str = ""
    confirmed_at: str = ""

    def holiday(self, d: _dt.date | None) -> str | None:
        return None if d is None or not self.active else self.dates.get(d)

    def covers(self, year: int) -> bool:
        return year in self.years


# why the holiday check is off (a table says its words when it is read, in the language of the run)
OFF_NOTE = W.table({k: f"cal.off.{k}" for k in ("not_set", "not_company", "unconfirmed")})


def _package_calendar() -> dict:
    """The government (ราชการ) calendar the package ships: a starting point to copy, never a rule's input."""
    try:
        from importlib.resources import files

        base = files(__name__.rsplit(".", 2)[0])
        return json.loads(base.joinpath("data").joinpath("holidays_th.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "holidays_th.json"),
                  encoding="utf-8") as f:
            return json.load(f)


def _parse_day(v) -> _dt.date | None:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v).strip())
    except ValueError:
        return TH.parse_date(str(v))


def government_calendar() -> dict:
    """The government calendar the package ships ({"dates": {ISO: name}, "source", "unverified"}), for
    a person to copy into the company's own calendar and confirm. No rule reads it."""
    return _package_calendar()


def calendar_for(defaults: dict | None) -> Calendar:
    """The holiday check's calendar: the company's own, confirmed by a person — or none (check off)."""
    raw = (defaults or {}).get("holidays")
    if not raw:
        return Calendar({}, "none", set(), OFF_NOTE["not_set"], reason="not_set")
    if not isinstance(raw, dict) or raw.get("kind") != "company" or not isinstance(raw.get("dates"), dict):
        # an AM2 plain {date: name} map, or a calendar of another kind: nobody said it is the company's
        return Calendar({}, "none", set(), OFF_NOTE["not_company"], reason="not_company")
    if raw.get("confirmed") is not True:
        return Calendar({}, "none", set(), OFF_NOTE["unconfirmed"], reason="unconfirmed")
    dates, problems = {}, []
    for k, v in raw["dates"].items():
        d = _parse_day(k)
        if d is None:
            problems.append(W("policy.holiday_date", k=k))
            continue
        dates[d] = str(v or W("day.off"))
    years = {int(y) for y in raw.get("years") or [] if str(y).isdigit()} or {d.year for d in dates}
    by, at = str(raw.get("confirmed_by") or "").strip(), str(raw.get("confirmed_at") or "").strip()
    note = W("cal.company") + (" " + W("cal.by", by=by) if by else "") + (" " + W("cal.at", at=at[:10]) if at else "")
    return Calendar(dates, "company", years, note, problems, active=True, confirmed_by=by, confirmed_at=at)


# ── the KB document: context only ───────────────────────────────────────────────

def _mentions(cat: Category, text: str) -> bool:
    t = _norm(text)
    label = _norm(cat.label)
    keys = {label, _strip_kha(label)} | {_strip_kha(p) for p in label.split("/") if p}
    return any(len(k) >= 4 and k in t for k in keys)


def kb_context(policy: Policy, docs: list, install_kb: dict | None = None) -> tuple[list[dict], list[dict]]:
    """The KB paragraphs as context, and every number in them that the table does not say.

    Each paragraph is kept verbatim with the categories it talks about and the amounts it names —
    ``used_as_limit`` is always False: a limit comes from the table alone. A category paragraph that
    names an amount other than the table's limit (per claim, or per month when it says ต่อเดือน) is a
    *difference*, reported so a person sees that the table won."""
    paragraphs = []
    for d in docs:
        for p in (d.data.get("paragraphs") or []):
            paragraphs.append((d.name, p["n"], p["text"]))
    if isinstance(install_kb, dict):
        for p in install_kb.get("paragraphs") or []:
            if isinstance(p, dict) and p.get("text"):
                paragraphs.append((install_kb.get("name") or "policy_kb", p.get("n"), p["text"]))
    context, conflicts = [], []
    for name, n, text in paragraphs:
        cats = [c.code for c in policy.categories.values() if _mentions(c, text)]
        numbers = [f"{v:.2f}" for v, _, _ in T.money_tokens(T.arabic(text))]
        context.append({"file": name, "paragraph": n, "text": text, "categories": cats, "numbers": numbers,
                        "used_as_limit": False})
        for code in cats:
            cat = policy.categories[code]
            kind = "per_person_month" if "ต่อเดือน" in text else "per_claim"
            table = getattr(cat, kind)
            for num in numbers:
                if table is None or Decimal(num) != table:
                    conflicts.append({"category": code, "label": cat.label, "policy_ref": f"categories.{code}.{kind}",
                                      "table": None if table is None else f"{table:.2f}", "document": num,
                                      "file": name, "paragraph": n, "text": text})
    return context, conflicts


# ── an owner's policy sheet → the same structure (install time) ──────────────────

_SHEET_HEADERS = {
    "label": ("หมวด", "หมวดค่าใช้จ่าย", "category", "label"), "code": ("รหัส", "รหัสหมวด", "code"),
    "per_claim": ("วงเงินต่อครั้ง", "per_claim", "perclaim"),
    "per_person_month": ("วงเงินต่อคนต่อเดือน", "per_person_month", "perpersonmonth"),
    "needs_purpose": ("ต้องระบุวัตถุประสงค์", "needs_purpose"), "wht_rate": ("อัตราหักณที่จ่าย", "wht_rate"),
    "wht_from": ("หักณที่จ่ายตั้งแต่", "wht_from"), "note": ("หมายเหตุ", "note", "notes"),
}
_SETTINGS = {
    "receipt_required_above": ("receipt_required_above", "ต้องมีใบเสร็จเมื่อเกิน", "ต้องแนบใบเสร็จเมื่อเกิน"),
    "approval_threshold": ("approval_threshold", "วงเงินอนุมัติ", "วงเงินที่ต้องขออนุมัติ"),
    "weekend_holiday": ("weekend_holiday", "เบิกวันหยุด"), "vat_claim_requires": ("vat_claim_requires",),
}


def _num_text(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{Decimal(repr(v)).quantize(Q2):.2f}"
    t = T.arabic(str(v)).replace(",", "").strip()
    return f"{Decimal(t).quantize(Q2):.2f}" if re.fullmatch(r"\d+(?:\.\d+)?", t) else str(v)


def policy_from_xlsx(path: str) -> dict:
    """An owner's policy sheet → {"policy", "holidays", "missing", "defaulted", "source"}.

    Sheets: the category table (หมวด · รหัส · วงเงินต่อครั้ง · วงเงินต่อคนต่อเดือน · หมายเหตุ, where a
    note may say ต้องระบุวัตถุประสงค์ or หัก ณ ที่จ่าย 3% [ตั้งแต่ 1,000]); ร้านต้องห้าม (one word per
    row); วันหยุด (date · name); optionally ตั้งค่า (key · value). What the sheet does not say is listed
    in ``missing`` for the owner to fill — never invented; the table is incomplete (load_policy refuses
    it) until they do."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    policy: dict = {"schema": SCHEMA, "currency": "THB", "categories": {}, "prohibited_merchants": []}
    holidays: dict[str, str] = {}
    defaulted: list[str] = []
    try:
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            title = _norm(ws.title)
            if "ต้องห้าม" in title or "prohibited" in title:
                policy["prohibited_merchants"] += [str(r[0]).strip() for r in rows if r and r[0] not in (None, "")]
                continue
            if "วันหยุด" in title or "holiday" in title:
                for r in rows:
                    d = _parse_day(r[0]) if r and r[0] not in (None, "") else None
                    if d is not None:
                        holidays[d.isoformat()] = str(r[1] if len(r) > 1 and r[1] else "วันหยุด")
                continue
            if "ตั้งค่า" in title or "setting" in title:
                for r in rows:
                    if not r or r[0] in (None, ""):
                        continue
                    k = next((key for key, names in _SETTINGS.items() if _norm(r[0]) in names), None)
                    if k and len(r) > 1 and r[1] not in (None, ""):
                        policy[k] = _num_text(r[1]) if k in ("receipt_required_above", "approval_threshold") else str(r[1])
                continue
            header_at, cols = None, {}
            for i, r in enumerate(rows[:15]):
                found = {f: j for j, cell in enumerate(r) for f, names in _SHEET_HEADERS.items() if _norm(cell) in names}
                if "label" in found and ("per_claim" in found or "per_person_month" in found):
                    header_at, cols = i, found
                    break
            if header_at is None:
                continue
            for r in rows[header_at + 1:]:
                def cell(f):
                    j = cols.get(f)
                    return r[j] if j is not None and j < len(r) else None
                label = cell("label")
                if label in (None, ""):
                    continue
                code = str(cell("code") or "").strip() or str(label).strip()
                c: dict = {"label": str(label).strip()}
                for k in ("per_claim", "per_person_month", "wht_from"):
                    v = _num_text(cell(k))
                    if v is not None:
                        c[k] = v
                note = str(cell("note") or "")
                if cell("needs_purpose") in (True, 1, "ใช่", "yes", "true") or "วัตถุประสงค์" in note:
                    c["needs_purpose"] = True
                m = re.search(r"หัก\s*ณ\s*ที่\s*จ่าย\s*(\d+(?:\.\d+)?)\s*%", T.arabic(note))
                rate = cell("wht_rate") or (m.group(1) if m else None)
                if rate not in (None, ""):
                    c["wht_rate"] = str(rate).replace("%", "").strip()
                m = re.search(r"ตั้งแต่\s*([\d,]+(?:\.\d+)?)", T.arabic(note))
                if m and "wht_from" not in c:
                    c["wht_from"] = _num_text(m.group(1))
                policy["categories"][code] = c
    finally:
        wb.close()
    for k, v in (("weekend_holiday", "flag"), ("vat_claim_requires", "full_tax_invoice")):
        if k not in policy:
            policy[k] = v
            defaulted.append(k)
    missing = [k for k in ("receipt_required_above", "approval_threshold") if k not in policy]
    missing += [f"categories.{code}.wht_from" for code, c in policy["categories"].items() if c.get("wht_rate") and not c.get("wht_from")]
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return {"policy": policy, "holidays": holidays, "missing": missing, "defaulted": defaulted,
            "source": {"file": os.path.basename(path), "sha256": sha}}
