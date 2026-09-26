"""A run's uploads judged in the run's context — the one place preflight and compute share.

Context, applied the same way on every pass:
  * the install's policy table and holiday calendar (checks/policy.py) — the only source of a limit;
  * the uploads: claims sheets, receipts (read by E2), a KB policy document (context only);
  * the duplicate index — receipts claimed in earlier runs of this install (``history``);
  * a vision model's reading of a scanned or photographed receipt — a candidate only;
  * the person's answers: to the E2 cards about a receipt's total, and to the judgement cards.

The same file uploaded twice counts once (a receipt keeps every name it was uploaded under, so a
claim that names the copy still finds it — and the duplicate rule sees the same bytes twice).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from ....engine.thai import fmt_money
from . import cards as CARDS
from . import policy as P
from . import receipts as RC
from . import rules as R
from .inputs import Upload


@dataclass
class Review:
    policy: P.Policy | None
    problems: list
    calendar: P.Calendar
    sheets: list                      # claims uploads
    receipts: dict                    # key → receipts.ReceiptReview
    views: dict                       # key → rules.ReceiptView
    kb: list                          # policy_document uploads
    others: list                      # uploads that are none of these
    same_file: list                   # (the copy, the upload it repeats)
    claims: list
    audits: list
    findings: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    context: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    missing_refs: list = field(default_factory=list)   # (claim, the file name it names) not uploaded
    unlinked: list = field(default_factory=list)        # receipts no claim names


def _claims(sheets: list[Upload]) -> list[R.Claim]:
    rows = [(s, r) for s in sheets for r in s.data["rows"]]
    ids: dict[str, int] = {}
    for _, r in rows:
        if r.get("id"):
            ids[r["id"]] = ids.get(r["id"], 0) + 1
    out = []
    for s, r in rows:
        key = r["id"] if r.get("id") and ids[r["id"]] == 1 else f"{r.get('id') or 'row'}-{s.key}-r{r['row']}"
        out.append(R.Claim.from_row(r, sheet=s.name, sheet_name=s.data["sheet"], key=key))
    return out


def review(uploads: list[Upload], *, defaults: dict | None, answers: dict | None, today: _dt.date | None,
           history: list | None = None, proposals: dict | None = None, final: bool = False) -> Review:
    defaults, answers = defaults or {}, answers or {}
    policy, problems = P.load_policy(defaults)
    calendar = P.calendar_for(defaults)
    seen: dict[str, Upload] = {}
    same_file, kept = [], []
    aliases: dict[str, list[str]] = {}
    for u in uploads:
        if u.sha256 in seen:
            same_file.append((u, seen[u.sha256]))
            aliases.setdefault(seen[u.sha256].key, []).append(u.name)
            continue
        seen[u.sha256] = u
        kept.append(u)
    sheets = [u for u in kept if u.kind == "claims"]
    kb = [u for u in kept if u.kind == "policy_document"]
    others = [u for u in kept if u.kind == "other"]
    receipts = [u for u in kept if u.kind == "receipt"]
    claims = _claims(sheets)

    # which claim names which receipt (by file name, the same way the rules link them)
    names = {}
    for u in receipts:
        for n in [u.name] + aliases.get(u.key, []):
            names.setdefault(n.lower(), u)
            names.setdefault(n.lower().rsplit(".", 1)[0], u)
    vat, linked_keys, missing = {}, set(), []
    for c in claims:
        if not c.receipt_ref:
            continue
        low = c.receipt_ref.lower()
        u = names.get(low) or names.get(low.rsplit(".", 1)[0])
        if u is None:
            missing.append((c, c.receipt_ref))
            continue
        linked_keys.add(u.key)
        vat[u.key] = vat.get(u.key, False) or c.vat_claim
    rr = RC.review(receipts, vat_claimed=vat, aliases=aliases, answers=answers, defaults=defaults, today=today,
                   proposals=proposals)
    views = {k: RC.view(r, final=final) for k, r in rr.items()}
    audits = R.audit(claims, policy=policy, calendar=calendar, receipts=list(views.values()), history=history) if policy else []
    rv = Review(policy, problems, calendar, sheets, rr, views, kb, others, same_file, claims, audits,
                missing_refs=missing, unlinked=[rr[u.key] for u in receipts if u.key not in linked_keys])
    if policy is not None:
        rv.context, rv.conflicts = P.kb_context(policy, kb, defaults.get("policy_kb"))

    # what is asked: the setup a run cannot go without, the receipts' totals, then judgement
    if policy is None:
        rv.findings.append(CARDS.no_policy(problems))
    if not sheets:
        rv.findings.append(CARDS.missing_claims([u.name for u in others if u.data.get("reason") == "unrecognised_sheet"]))
    for k in sorted(rr, key=lambda k: rr[k].up.name):
        if k in linked_keys:
            rv.findings += rr[k].findings
    rv.findings += CARDS.cards_for(audits, answers)
    rv.assumptions = _assumptions(rv)
    return rv


def _assumptions(rv: Review) -> list[dict]:
    out = []
    p = rv.policy
    if p is not None and p.source == "package_sample":
        out.append({"code": "package_sample_policy", "source": "rule",
                    "text": "ตารางนโยบายที่ใช้ตรวจเป็นตารางตัวอย่างที่มากับ agent ยังไม่ได้แก้ให้เป็นของบริษัท "
                            "ตรวจวงเงินในหน้าตั้งค่า agent ก่อนใช้ผลจริง", "changes_numbers": True})
    if rv.kb:
        out.append({"code": "kb_context_only", "source": "rule",
                    "text": f"เอกสารระเบียบ ({', '.join(u.name for u in rv.kb)}) ใช้เป็นบริบทเท่านั้น วงเงินทุกตัวมาจากตารางนโยบาย"})
    for c in rv.conflicts:
        table = f"กำหนด {fmt_money(c['table'])} บาท" if c["table"] else "ไม่ได้กำหนดวงเงินนี้"
        out.append({"code": "kb_differs_from_table", "source": "rule",
                    "text": f"{c['file']} ย่อหน้า {c['paragraph']} กล่าวถึง{c['label']} {fmt_money(c['document'])} บาท แต่ตารางนโยบาย{table} "
                            f"— ใช้ตาราง เอกสารเป็นบริบทเท่านั้น"})
    cal = rv.calendar
    years = sorted({c.date.year for c in rv.claims if c.date})
    if cal.source == "package":
        out.append({"code": "package_holiday_calendar", "source": "rule",
                    "text": "ยังไม่ได้ตั้งปฏิทินวันหยุดของบริษัท ใช้ปฏิทินวันหยุดราชการที่มากับ agent"})
    for y in years:
        if not cal.covers(y):
            out.append({"code": "holiday_calendar_missing_year", "source": "rule",
                        "text": f"ปฏิทินวันหยุดไม่มีปี {y + 543} ตรวจได้เฉพาะเสาร์–อาทิตย์ของปีนั้น"})
    for copy, orig in rv.same_file:
        out.append({"code": "same_file_twice", "source": "rule",
                    "text": f"{copy.name} เป็นไฟล์เดียวกับ {orig.name} (ไบต์ตรงกันทุกตัว) นับครั้งเดียว"})
    for u in rv.others:
        why = {"policy_sheet": "เป็นตารางนโยบาย: ตารางนโยบายตั้งในหน้าตั้งค่า agent รอบนี้ใช้ตารางที่ตั้งไว้",
               "unrecognised_sheet": "ไม่มีหัวคอลัมน์ของรายการเบิก", "unsupported_type": "ไม่ใช่ชนิดไฟล์ที่ agent นี้ใช้",
               "unreadable": "เปิดไฟล์ไม่ได้"}.get(u.data.get("reason"), "ไม่ได้ใช้")
        out.append({"code": "file_not_used", "source": "rule", "text": f"ไม่ได้ใช้ไฟล์ {u.name}: {why}"})
    if rv.missing_refs:
        out.append({"code": "receipt_files_missing", "source": "rule",
                    "text": "รายการเบิกอ้างถึงใบเสร็จที่ไม่ได้ส่งมา: " + ", ".join(f"{c.label} → {ref}" for c, ref in rv.missing_refs[:10])})
    for r in rv.unlinked:
        out.append({"code": "receipt_not_linked", "source": "rule",
                    "text": f"ใบเสร็จ {r.up.name} ไม่มีรายการเบิกใดอ้างถึง จึงไม่ได้ใช้ตรวจ"})
    return out


def period_of(claims: list[R.Claim], today: _dt.date | None) -> tuple[str, str]:
    """The month most claims are dated in (ties: the latest), else today's."""
    months: dict[str, int] = {}
    for c in claims:
        if c.date:
            m = f"{c.date:%Y-%m}"
            months[m] = months.get(m, 0) + 1
    if months:
        return max(months, key=lambda m: (months[m], m)), "claims"
    t = today or _dt.date.today()
    return f"{t.year:04d}-{t.month:02d}", "today"
