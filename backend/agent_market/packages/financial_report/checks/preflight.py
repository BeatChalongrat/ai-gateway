"""Preflight checks — run the moment files arrive, before any compute.

Each check reads the parsed files and returns findings. Nothing here
computes a reported number; it only decides what is unclear. A check that
has nothing to say returns nothing — on clean input the agent asks zero
questions, and the eval set fails the gate if it does not.

Checks: missing_file · wrong_document_type · unmapped_column ·
ambiguous_debit_credit · period_undetermined · period_mixed ·
currency_mixed · account_name_missing (cosmetic) · year_convention_mixed
(cosmetic).
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from decimal import Decimal

from ....engine.findings import Finding, finding
from ....engine.headers import FIELD_LABEL_TH
from ....engine.ledger import ParsedFile, apply_convention, indicator_guess
from ....engine.thai import (
    find_period_in_text, fmt_int, fmt_money, period_bounds, period_label_th, prev_period, year_style,
)

LEDGER_TYPES = ("gl", "tb", "pl")
DOC_TYPE_TH = {"gl": "บัญชีแยกประเภท", "tb": "งบทดลอง", "pl": "งบกำไรขาดทุน", "budget": "งบประมาณ",
               "unknown": "ไม่ใช่ไฟล์บัญชี", "unreadable": "เปิดไม่ได้"}


# ── roles ──────────────────────────────────────────────────────────────────

def dominant_period(pf: ParsedFile) -> tuple[str | None, float, Counter]:
    months = Counter(r.period for r in pf.rows if r.period)
    if not months:
        return pf.period_hint, 0.0, months
    top, n = months.most_common(1)[0]
    return top, n / max(1, sum(months.values())), months


def assign_roles(files: list[ParsedFile], answers: dict) -> dict[str, str]:
    """name → primary | prior | budget | ignored | unknown."""
    roles: dict[str, str] = {}
    ignored = {k.split(":", 1)[1] for k, v in answers.items()
               if k.startswith("wrong_document_type:") and v == "ignore"}
    ledgers = []
    for pf in files:
        if pf.name in ignored:
            roles[pf.name] = "ignored"
        elif pf.doc_type == "budget":
            roles[pf.name] = "budget"
        elif pf.doc_type in LEDGER_TYPES and pf.rows:
            ledgers.append(pf)
        else:
            roles[pf.name] = "unknown"
    # Latest data wins primary; the rest are prior-period candidates.
    def latest(pf):
        ps = [r.period for r in pf.rows if r.period] or [pf.period_hint or ""]
        return max(ps)
    ledgers.sort(key=latest, reverse=True)
    for i, pf in enumerate(ledgers):
        roles[pf.name] = "primary" if i == 0 else "prior"
    return roles


# ── the period ────────────────────────────────────────────────────────────

def resolve_period(primary: ParsedFile, message: str, answers: dict, today: _dt.date) -> tuple[str | None, str]:
    """(period, source). source says where the period came from."""
    if answers.get("period_undetermined"):
        return answers["period_undetermined"], "answer"
    p = find_period_in_text(message or "", today)
    if p:
        return p, "message"
    if primary.period_hint:
        return primary.period_hint, "file_title"
    top, share, months = dominant_period(primary)
    if top and (share >= 0.8 or len(months) == 1):
        return top, "data"
    if primary.doc_type in ("tb", "pl"):
        return None, "none"
    return None, "none"


# ── individual checks ─────────────────────────────────────────────────────

def check_files(files, roles) -> list[Finding]:
    out = []
    for pf in files:
        if roles.get(pf.name) != "unknown":
            continue
        what = DOC_TYPE_TH.get(pf.doc_type, "ไม่ใช่ไฟล์บัญชี")
        cols = [h for h in (pf.header.headers if pf.header else []) if h][:6]
        out.append(finding(
            "wrong_document_type", "blocking", "preflight", "ไฟล์ผิดชนิด",
            f"ไฟล์ “{pf.name}” ดูไม่ใช่บัญชีแยกประเภท งบทดลอง หรืองบกำไรขาดทุน"
            + (" (เปิดไฟล์ไม่ได้)" if pf.doc_type == "unreadable" else "")
            + " จะให้ทำอย่างไรกับไฟล์นี้ครับ",
            [("ไม่ใช้ไฟล์นี้ในรอบนี้", "ignore", "ข้ามไฟล์นี้ แล้วทำต่อด้วยไฟล์อื่น"),
             ("ส่งไฟล์ที่ถูกต้องแทน", "replace", "ผมจะรอไฟล์ใหม่ก่อนเริ่มคำนวณ")],
            "ignore",
            evidence={"file": pf.name, "columns": cols, "rows": pf.data_rows, "detected": what,
                      "error": pf.error},
            subject=pf.name, facts={"file": pf.name, "detected": what},
        ))
    return out


def check_missing(files, roles, message: str, answers: dict | None = None) -> list[Finding]:
    answers = answers or {}
    if any(r == "primary" for r in roles.values()):
        out = []
        low = (message or "").lower()
        wants_budget = any(w in low for w in ("งบประมาณ", "budget")) and answers.get("missing_file") != "proceed_without"
        if wants_budget and not any(r == "budget" for r in roles.values()):
            out.append(finding(
                "missing_file", "blocking", "preflight", "ขาดไฟล์",
                "คุณขอให้เทียบกับงบประมาณ แต่ยังไม่มีไฟล์งบประมาณครับ",
                [("ไม่มีไฟล์นี้ ทำเท่าที่มี", "proceed_without", "รายงานจะระบุว่าไม่ได้เทียบกับงบประมาณ"),
                 ("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้")],
                "wait", kind="request_files", subject="budget",
                request=[{"role": "budget", "label": "ไฟล์งบประมาณของงวดนี้",
                          "why": "ใช้เทียบค่าใช้จ่ายจริงกับงบ", "required": False, "accept": [".xlsx", ".csv"]}],
                facts={"missing": "budget"},
            ))
        return out
    return [finding(
        "missing_file", "blocking", "preflight", "ขาดไฟล์",
        "ยังไม่มีบัญชีแยกประเภท (GL) งบทดลอง หรืองบกำไรขาดทุนของเดือนที่จะปิดครับ "
        "ผมต้องใช้ไฟล์นี้ก่อนจึงจะเริ่มได้",
        [("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้"),
         ("ยกเลิกรอบนี้", "cancel", "ปิดรอบนี้ไว้ก่อน")],
        "wait", kind="request_files", subject="primary",
        request=[{"role": "primary", "label": "บัญชีแยกประเภท (GL) ของเดือนที่จะปิด",
                  "why": "ตัวเลขทั้งรายงานคำนวณจากไฟล์นี้", "required": True, "accept": [".xlsx", ".csv"]}],
        facts={"missing": "primary"},
    )]


def check_mapping(primary: ParsedFile, answers: dict) -> list[Finding]:
    hm = primary.header
    if hm is None:
        return []
    key = f"unmapped_column:{primary.name}"
    if key in answers:
        return []
    required = ["account_code"]
    if primary.doc_type == "gl":
        required.append("date")
    if "debit" in hm.fields or "credit" in hm.fields:
        required += ["debit", "credit"]
    else:
        required.append("amount")
    inferred = [f for f in required if hm.source.get(f) == "inferred"]
    missing = [f for f in required if f not in hm.fields]
    if not inferred and not missing:
        return []
    proposal = {f: hm.headers[hm.fields[f]] for f in hm.fields if f in required or hm.source.get(f) == "inferred"}
    guessed = ", ".join(f"{FIELD_LABEL_TH.get(f, f)} = “{hm.headers[hm.fields[f]]}”"
                        for f in required if hm.source.get(f) == "inferred")
    q = (f"ผมไม่รู้จักชื่อคอลัมน์บางตัวในไฟล์ “{primary.name}” จึงเดาจากค่าที่อยู่ในคอลัมน์: {guessed} "
         "ถูกต้องไหมครับ")
    if missing:
        q = (f"ในไฟล์ “{primary.name}” ผมหาคอลัมน์ "
             + ", ".join(f"“{FIELD_LABEL_TH.get(f, f)}”" for f in missing)
             + " ไม่เจอ ช่วยบอกผมว่าอยู่คอลัมน์ไหนครับ")
    options = [("ถูกต้อง ใช้ตามนี้", "accept", "จำการจับคู่นี้ไว้ใช้รอบหน้า")] if not missing else []
    options += [("ขอเลือกคอลัมน์เอง", "manual", "เปิดหน้าจับคู่คอลัมน์ เลือกด้วยการคลิก"),
                ("ส่งไฟล์ใหม่แทน", "replace", "ใช้ไฟล์ที่ export ใหม่แล้ว")]
    return [finding(
        "unmapped_column", "blocking", "preflight", "จับคู่คอลัมน์",
        q, options, options[0][1],
        evidence={"file": primary.name, "sheet": primary.sheet, "header_row": hm.header_row + 1,
                  "proposal": proposal, "headers": [h for h in hm.headers if h],
                  "sample_rows": [[r.row, r.date.isoformat() if r.date else None, r.account_code,
                                   str(r.debit), str(r.credit)] for r in primary.rows[:5]]},
        rememberable=True, subject=primary.name, facts={"file": primary.name, "fields": list(proposal)},
    )]


def check_unreadable_dates(files, roles, answers: dict) -> tuple[list[Finding], list[dict]]:
    """Rows with an account and money whose date the parser cannot read are
    left out of the ledger. That must never happen silently: the person
    either sends an export with day/month/year dates or agrees to go on
    without them, and the report says how much was left out."""
    out: list[Finding] = []
    assumptions: list[dict] = []
    for pf in files:
        if not pf.unreadable_date_rows or roles.get(pf.name) not in ("primary", "prior"):
            continue
        key = f"unreadable_dates:{pf.name}"
        n, amount = pf.unreadable_date_rows, pf.unreadable_date_amount
        if key in answers:
            if answers[key] == "exclude":
                assumptions.append({"code": "unreadable_dates", "source": "answer", "value": "exclude",
                                    "text": f"ไม่นับ {fmt_int(n)} แถวใน “{pf.name}” ที่อ่านวันที่ไม่ได้ "
                                            f"(รวม {fmt_money(amount)} บาท)"})
            continue
        examples = []
        for s in pf.unreadable_date_samples:
            if s[1] not in examples:
                examples.append(s[1])
        out.append(finding(
            "unreadable_dates", "blocking", "preflight", "อ่านวันที่ไม่ได้",
            f"ในไฟล์ “{pf.name}” มี {fmt_int(n)} แถวที่มีรหัสบัญชีและจำนวนเงิน แต่ผมอ่านวันที่ไม่ออก "
            f"เช่น {', '.join('“' + e + '”' for e in examples[:3])} ถ้าคำนวณต่อ แถวเหล่านี้จะไม่ถูกนับ "
            f"รวม {fmt_money(amount)} บาท จะให้ทำอย่างไรครับ",
            [("ส่งไฟล์ใหม่ที่วันที่เป็น วัน/เดือน/ปี", "replace", "export ใหม่ แล้วผมตรวจใหม่ตั้งแต่ต้น"),
             ("ไม่นับแถวเหล่านี้ คำนวณต่อ", "exclude", f"ตัวเลขจะขาดไป {fmt_money(amount)} บาท และรายงานจะบอกไว้")],
            "replace",
            evidence={"file": pf.name, "rows": pf.unreadable_date_samples[:20],
                      "columns": ["แถว", "วันที่ในไฟล์", "บัญชี", "จำนวนเงิน"]},
            subject=pf.name, facts={"file": pf.name, "rows": n, "amount": str(amount)},
        ))
    return out, assumptions


def _dc_header(pf: ParsedFile) -> str:
    hm = pf.header
    f = "dc_indicator" if pf.dc_mode == "indicator" else "amount"
    return hm.header_of(f) if hm else f


def remembered_convention(pf: ParsedFile, defaults: dict):
    conv = (defaults.get("dc_convention") or {})
    return conv.get(_dc_header(pf))


def check_dc(pf: ParsedFile, defaults: dict, answers: dict) -> list[Finding]:
    if pf.dc_mode not in ("indicator", "signed") or pf.doc_type == "pl":
        return []
    if answers.get("ambiguous_debit_credit") is not None or remembered_convention(pf, defaults) is not None:
        return []
    header = _dc_header(pf)
    import copy

    def revenue_under(conv) -> Decimal:
        trial = copy.deepcopy(pf)
        apply_convention(trial, conv)
        return sum((r.credit - r.debit for r in trial.rows if r.account_code.replace("-", "").startswith("41")), Decimal(0))

    if pf.dc_mode == "indicator":
        guess = indicator_guess(pf.indicator_values) or {v: ("debit" if i == 0 else "credit")
                                                         for i, v in enumerate(pf.indicator_values[:2])}
        flipped = {k: ("credit" if v == "debit" else "debit") for k, v in guess.items()}
        if revenue_under(guess) < 0:
            guess, flipped = flipped, guess
        deb = next((k for k, v in guess.items() if v == "debit"), "?")
        cre = next((k for k, v in guess.items() if v == "credit"), "?")
        q = (f"คอลัมน์ “{header}” มีค่าเป็น {deb} กับ {cre} ผมเดาว่า {deb} = เดบิต และ {cre} = เครดิต "
             "แต่ขอให้ยืนยันก่อน เพราะถ้าเดาผิด ตัวเลขทั้งรายงานจะกลับข้าง")
        options = [(f"ใช่ {deb} คือเดบิต", guess, f"รายได้รวมจะเป็น {fmt_money(revenue_under(guess))}"),
                   (f"ไม่ใช่ {deb} คือเครดิต", flipped, f"รายได้รวมจะเป็น {fmt_money(revenue_under(flipped))}")]
        default = guess
    else:
        pos_d = revenue_under("positive_debit")
        q = (f"คอลัมน์ “{header}” มีทั้งเลขบวกและเลขลบ ผมเดาว่าเลขบวก = เดบิต เลขลบ = เครดิต "
             "ขอให้ยืนยันก่อนเริ่มคำนวณครับ")
        a, b = ("positive_debit", "positive_credit") if pos_d >= 0 else ("positive_credit", "positive_debit")
        label = {"positive_debit": "บวก = เดบิต", "positive_credit": "บวก = เครดิต"}
        options = [(f"ใช่ {label[a]}", a, f"รายได้รวมจะเป็น {fmt_money(revenue_under(a))}"),
                   (label[b], b, f"รายได้รวมจะเป็น {fmt_money(revenue_under(b))}")]
        default = a
    ev_rows = [[r.row, r.account_code, r.description[:40], str(r.debit + r.credit), r.raw_indicator]
               for r in pf.rows[:5]]
    return [finding(
        "ambiguous_debit_credit", "blocking", "preflight", "เดบิต/เครดิต",
        q, options, default,
        evidence={"file": pf.name, "column": header, "values": pf.indicator_values[:4], "sample_rows": ev_rows,
                  "sample_columns": ["แถว", "บัญชี", "คำอธิบาย", "จำนวนเงิน", header]},
        rememberable=True, subject=header, facts={"column": header},
    )]


def check_period(primary: ParsedFile, period: str | None, source: str, answers: dict,
                 defaults: dict) -> tuple[list[Finding], list[dict]]:
    """Returns (findings, assumptions)."""
    out: list[Finding] = []
    assumptions: list[dict] = []
    top, share, months = dominant_period(primary)
    if period is None:
        if primary.doc_type in ("tb", "pl"):
            opts_periods = [primary.period_hint] if primary.period_hint else []
        else:
            opts_periods = sorted(months, reverse=True)[:3]
        if not opts_periods:
            opts_periods = []
        options = [(period_label_th(p), p, f"{fmt_int(months.get(p, 0))} รายการ") for p in opts_periods]
        if len(options) < 2:
            # Offer the neighbouring month so the card is always a real choice.
            base = opts_periods[0] if opts_periods else None
            if base:
                options.append((period_label_th(prev_period(base)), prev_period(base), "เดือนก่อนหน้า"))
        if len(options) >= 2:
            out.append(finding(
                "period_undetermined", "blocking", "preflight", "งวดบัญชี",
                f"ไฟล์ “{primary.name}” มีรายการหลายเดือน และผมไม่แน่ใจว่าจะให้ปิดงบเดือนไหนครับ",
                options[:4], options[0][1],
                evidence={"file": primary.name, "months": {p: n for p, n in sorted(months.items())}},
                subject=primary.name, facts={"months": list(months)},
            ))
        return out, assumptions
    if primary.doc_type not in ("gl",):
        return out, assumptions
    start, end = period_bounds(period)
    in_p = months.get(period, 0)
    stray = []
    for r in primary.rows:
        if r.period == period or r.date is None:
            continue
        # Earlier months with real volume are history for the trend, not strays.
        if r.date < start and months.get(r.period, 0) >= max(20, in_p * 0.3):
            continue
        stray.append(r)
    if not stray:
        return out, assumptions
    remembered = (defaults.get("answers") or {}).get("period_mixed")
    total = sum((r.debit for r in stray), Decimal(0))
    stray_months = sorted({r.period for r in stray})
    date_span = f"{min(r.date for r in stray).strftime('%d/%m')}–{max(r.date for r in stray).strftime('%d/%m')}"
    if answers.get("period_mixed") is not None:
        return out, assumptions
    if remembered is not None:
        assumptions.append({"code": "period_mixed", "source": "remembered", "value": remembered,
                            "text": f"ใช้ค่าที่จำไว้: {'ตัดออก' if remembered == 'exclude' else 'นับรวม'} "
                                    f"{fmt_int(len(stray))} รายการที่ลงวันที่นอกงวด ({', '.join(stray_months)})"})
        return out, assumptions
    label = period_label_th(period).split(" ")[0]
    others = ", ".join(period_label_th(p).split(" ")[0] for p in stray_months)
    out.append(finding(
        "period_mixed", "blocking", "preflight", "รายการนอกงวด",
        f"มี {fmt_int(len(stray))} รายการลงวันที่ {date_span} ({others}) ปนมาในไฟล์ "
        f"รวมเป็นเงิน {fmt_money(total)} บาท (ฝั่งเดบิต) จะให้นับเข้างวด{label}หรือตัดออกครับ",
        [(f"ตัดออก ใช้เฉพาะ {period_label_th(period).split(' ')[0]}", "exclude", "แนะนำ: ตัวเลขตรงกับงวดที่ปิด"),
         ("นับรวมด้วย", "include", "รายการเหล่านี้จะถูกนับเป็นของงวดนี้")],
        "exclude",
        evidence={"file": primary.name, "count": len(stray), "debit_total": str(total), "months": stray_months,
                  "rows": [[r.row, r.date.isoformat(), r.doc_no, r.account_code, r.description[:40],
                            str(r.debit), str(r.credit)] for r in stray[:50]],
                  "columns": ["แถว", "วันที่", "เลขที่", "บัญชี", "คำอธิบาย", "เดบิต", "เครดิต"]},
        rememberable=True, subject="", facts={"count": len(stray), "total": str(total), "period": period},
    ))
    return out, assumptions


def check_currency(primary: ParsedFile, base: str, answers: dict, defaults: dict) -> tuple[list[Finding], list[dict]]:
    foreign = {c: n for c, n in primary.currencies.items() if c and c != base}
    if not foreign or answers.get("currency_mixed") is not None:
        return [], []
    remembered = (defaults.get("answers") or {}).get("currency_mixed")
    if remembered is not None:
        return [], [{"code": "currency_mixed", "source": "remembered", "value": remembered,
                     "text": "ใช้ค่าที่จำไว้เรื่องรายการสกุลเงินอื่น"}]
    rows = [r for r in primary.rows if r.currency != base]
    names = ", ".join(f"{c} {fmt_int(n)} รายการ" for c, n in foreign.items())
    return [finding(
        "currency_mixed", "blocking", "preflight", "สกุลเงิน",
        f"ในไฟล์มีรายการที่ระบุสกุลเงินอื่นปนอยู่ ({names}) "
        f"ยอดเงินในคอลัมน์ของรายการเหล่านี้เป็นเงินบาทแล้วหรือยังครับ",
        [("เป็นบาทแล้ว แค่ระบุสกุลของเอกสาร", "amounts_in_base", "ใช้ยอดตามไฟล์"),
         ("ยังไม่ใช่บาท ตัดรายการเหล่านี้ออก", "exclude_foreign", f"ตัดออก {fmt_int(len(rows))} รายการ")],
        "amounts_in_base",
        evidence={"file": primary.name, "currencies": primary.currencies,
                  "rows": [[r.row, r.date.isoformat() if r.date else None, r.account_code, r.description[:40],
                            str(r.debit), str(r.credit), r.currency] for r in rows[:50]],
                  "columns": ["แถว", "วันที่", "บัญชี", "คำอธิบาย", "เดบิต", "เครดิต", "สกุล"]},
        rememberable=True, facts={"foreign": foreign},
    )], []


def check_cosmetic(primary: ParsedFile, files, answers) -> list[Finding]:
    out = []
    names_known = bool(primary.account_names) or any(f.account_names for f in files if f is not primary)
    if not names_known and "account_name_missing" not in answers:
        out.append(finding(
            "account_name_missing", "cosmetic", "preflight", "ชื่อบัญชี",
            "ไฟล์นี้มีแต่รหัสบัญชี ไม่มีชื่อบัญชี ในรายงานจะให้แสดงอย่างไรครับ",
            [("แสดงรหัสบัญชีอย่างเดียว", "code_only", "เช่น 5300-01"),
             ("ใช้ชื่อหมวดตามผังบัญชีมาตรฐาน", "standard_names", "เช่น 5300-01 ค่าใช้จ่ายในการบริหาร")],
            "code_only", evidence={"file": primary.name, "accounts": sorted({r.account_code for r in primary.rows})[:20]},
            rememberable=True,
        ))
    style = year_style(primary.raw_date_texts)
    if style == "mixed" and "year_convention_mixed" not in answers:
        be_n = sum(1 for t in primary.raw_date_texts if isinstance(t, str) and t.strip()[-4:].startswith("25"))
        out.append(finding(
            "year_convention_mixed", "cosmetic", "preflight", "ปี พ.ศ./ค.ศ.",
            "วันที่ในไฟล์ใช้ทั้งปี พ.ศ. และ ค.ศ. ปนกัน (ผมอ่านถูกทั้งสองแบบ) ในรายงานจะให้เขียนปีแบบไหนครับ",
            [("พ.ศ. เช่น กันยายน 2569", "be", ""), ("ค.ศ. เช่น September 2026", "ce", "")],
            "be", evidence={"file": primary.name, "be_texts": be_n, "sample": primary.raw_date_texts[:6]},
            rememberable=True,
        ))
    return out


# ── entry point ───────────────────────────────────────────────────────────

def run(files: list[ParsedFile], *, message: str = "", defaults: dict | None = None,
        answers: dict | None = None, today: _dt.date | None = None) -> dict:
    defaults = defaults or {}
    answers = answers or {}
    today = today or _dt.date.today()
    roles = assign_roles(files, answers)
    findings: list[Finding] = []
    assumptions: list[dict] = []
    findings += check_files(files, roles)
    findings += check_missing(files, roles, message, answers)
    primary = next((f for f in files if roles.get(f.name) == "primary"), None)
    period = source = None
    if primary is not None:
        findings += check_mapping(primary, answers)
        blocking_mapping = any(f.code == "unmapped_column" for f in findings)
        if not blocking_mapping:
            f0, a0 = check_unreadable_dates(files, roles, answers)
            findings += f0
            assumptions += a0
            findings += check_dc(primary, defaults, answers)
            period, source = resolve_period(primary, message, answers, today)
            f2, a2 = check_period(primary, period, source, answers, defaults)
            findings += f2
            assumptions += a2
            f3, a3 = check_currency(primary, defaults.get("currency", "THB"), answers, defaults)
            findings += f3
            assumptions += a3
            findings += check_cosmetic(primary, files, answers)
    stats = {}
    if primary is not None:
        stats = {"rows": primary.data_rows, "skipped_rows": primary.skipped_rows, "doc_type": primary.doc_type,
                 "debit_total": str(sum((r.debit for r in primary.rows), Decimal(0))) if primary.dc_mode == "columns" else None,
                 "credit_total": str(sum((r.credit for r in primary.rows), Decimal(0))) if primary.dc_mode == "columns" else None}
    return {
        "roles": roles,
        "period": period,
        "period_source": source,
        "prior_period": prev_period(period) if period else None,
        "findings": [f.to_dict() for f in findings],
        "assumptions": assumptions,
        "stats": stats,
        "files": [f.summary() for f in files],
    }
