"""Judgement → clarification cards (engine/findings.py), and what a person's answer means.

A rule can see that a claim is dated on a Saturday or that its description is only its category's
name; whether the business reason is good enough is judgement, so it is a card — never a rule's
verdict. One card per claim and trigger (``expense_weekend_holiday:C07``,
``expense_vague_purpose:C14``; both codes are in engine/findings.MULTI_INSTANCE so the subject is in
the key and an answer can never land on another claim).

Every card offers the same three answers, and its default — what a skipped card records — is the
one that hands the claim to a person:

    accept        "fine as it is": recorded as an info exception naming who decided (the person)
    ask_employee  a review exception: ask the employee for the reason
    review        (default) a review exception for the approver to decide
    {"typed": …}  the purpose card also takes the purpose the person got from the employee: recorded
                  word for word as the claim's purpose (info), never interpreted by a model

Anything else — an answer that is not one of these, an empty typed value — is treated as ``review``:
a judgement is never passed by default.
"""

from __future__ import annotations

import re

from ....engine.findings import FORBIDDEN_WORDS, Finding, finding
from .rules import ClaimAudit, date_th

CARD_CODES = {"weekend_or_holiday": "expense_weekend_holiday", "vague_purpose": "expense_vague_purpose"}
FLAG_OF = {v: k for k, v in CARD_CODES.items()}
HEADER = {"weekend_or_holiday": "วันหยุด", "vague_purpose": "วัตถุประสงค์"}      # ≤ 12 display characters
DEFAULT = "review"
RESOLUTIONS = ("accepted", "typed", "ask_employee", "review")
_FORBIDDEN = re.compile("|".join(re.escape(w) for w in FORBIDDEN_WORDS), re.IGNORECASE)


def safe(text: str) -> str:
    """Free text from a sheet (a merchant, a description) quoted on a card: a word a card may never
    say (engine/findings.FORBIDDEN_WORDS — a card never asks for a secret) is masked, so a merchant
    called "…Shopping…" cannot break the run."""
    return _FORBIDDEN.sub("…", str(text or ""))


def key_for(flag: str, claim_key: str) -> str:
    return f"{CARD_CODES[flag]}:{claim_key}"


def resolution(value) -> tuple[str, str | None]:
    """A card's answer → (accepted | typed | ask_employee | review, the typed purpose)."""
    if value == "accept":
        return "accepted", None
    if value == "ask_employee":
        return "ask_employee", None
    if isinstance(value, dict) and isinstance(value.get("typed"), str):
        t = " ".join(value["typed"].split())
        if t:
            return "typed", t[:300]
    return "review", None


def _who(a: ClaimAudit) -> str:
    c = a.claim
    return safe(" ".join(x for x in (c.label, "·", c.employee_name or c.employee_id or "") if x).strip())


def _what(a: ClaimAudit) -> str:
    c = a.claim
    label = a.category.label if a.category else c.category_raw
    amount = f"{c.amount:,.2f}" if c.amount is not None else c.amount_raw
    return safe(f"เบิก{label} {amount} บาท ที่ {c.merchant or 'ไม่ระบุร้าน'}")


def card(a: ClaimAudit, flag: str) -> Finding:
    c = a.claim
    fact = a.facts["weekend_holiday" if flag == "weekend_or_holiday" else "purpose"]
    ev = {"claim": c.label, "sheet": c.sheet, "sheet_name": c.sheet_name, "row": c.row, "fact": fact.to_dict()}
    if flag == "weekend_or_holiday":
        day = fact.evidence.get("weekday") if (c.date and c.date.weekday() >= 5) else ""
        hol = fact.evidence.get("holiday")
        what = " และ".join(x for x in (day, f"วันหยุด{hol}" if hol else "") if x)
        q = (f"{_who(a)} {_what(a)} วันที่ {date_th(c.date)} ซึ่งเป็น{what} เป็นงานของบริษัทหรือไม่ "
             f"(กฎบอกได้แค่ว่าเป็นวันหยุด เหตุผลต้องให้คนพิจารณา)")
        opts = [("เป็นงานบริษัท รับได้", "accept", "บันทึกว่าคุณยืนยันแล้ว ไม่ต้องมีคนตัดสินอีก"),
                ("ให้ถามพนักงาน", "ask_employee", "ส่งเป็นข้อยกเว้น: ขอเหตุผลจากพนักงาน"),
                ("ส่งให้ผู้อนุมัติตัดสิน", DEFAULT, "ส่งเป็นข้อยกเว้นรอคนตัดสิน (ข้ามการ์ดนี้ก็เป็นแบบนี้)")]
        return finding(CARD_CODES[flag], "blocking", "preflight", HEADER[flag], q, opts, DEFAULT, subject=c.key,
                       evidence=ev, facts={"claim": c.label, "check": "weekend_or_holiday", "date": c.date.isoformat(),
                                           "holiday": hol})
    desc = safe(c.description)
    said = f"รายละเอียดเขียนว่า “{desc}”" if desc.strip() else "ไม่ได้เขียนรายละเอียด"
    q = (f"{_who(a)} {_what(a)} ({date_th(c.date)}) {said} ซึ่งยังไม่บอกวัตถุประสงค์ทางธุรกิจ "
         f"รับได้หรือไม่ หรือพิมพ์วัตถุประสงค์ที่ได้จากพนักงาน")
    opts = [("วัตถุประสงค์ชัดพอ รับได้", "accept", "บันทึกว่าคุณยืนยันแล้ว ไม่ต้องมีคนตัดสินอีก"),
            ("ให้ถามพนักงาน", "ask_employee", "ส่งเป็นข้อยกเว้น: ขอวัตถุประสงค์จากพนักงาน"),
            ("ส่งให้ผู้อนุมัติตัดสิน", DEFAULT, "ส่งเป็นข้อยกเว้นรอคนตัดสิน (ข้ามการ์ดนี้ก็เป็นแบบนี้)")]
    return finding(CARD_CODES[flag], "blocking", "preflight", HEADER[flag], q, opts, DEFAULT, subject=c.key, evidence=ev,
                   input={"kind": "text", "hint": "หรือพิมพ์วัตถุประสงค์ เช่น เลี้ยงรับรองลูกค้าบริษัท ก. หลังประชุมเสนอราคา"},
                   facts={"claim": c.label, "check": "vague_purpose", "description": desc})


def cards_for(audits: list[ClaimAudit], answers: dict | None) -> list[Finding]:
    """One card per judgement trigger of a claim whose outcome is ``question`` and that has no answer yet."""
    answers = answers or {}
    return [card(a, flag) for a in audits for flag in a.flags if key_for(flag, a.claim.key) not in answers]


def no_policy(problems: list[str]) -> Finding:
    listed = safe("; ".join(problems[:4]) + (f" และอีก {len(problems) - 4} ข้อ" if len(problems) > 4 else ""))
    return finding("expense_no_policy", "blocking", "preflight", "นโยบายการเบิก",
                   f"ยังตรวจไม่ได้: ตารางนโยบายการเบิกของ agent นี้ยังใช้ไม่ได้ ({listed}) ตั้งหรือแก้ตารางในหน้าตั้งค่า agent "
                   f"แล้วสั่งทำรอบนี้ต่อ ผมจะไม่เดาวงเงินเอง และจะไม่เอาวงเงินจากเอกสารระเบียบมาใช้แทน",
                   [("ตั้งแล้ว ทำต่อ", "wait", "ตรวจต่อเมื่อมีตารางนโยบายที่ใช้ได้"),
                    ("ยกเลิกรอบนี้", "cancel", "ปิดรอบนี้ไว้ก่อน")], "wait", facts={"problems": problems[:10]})


def missing_claims(others: list[str]) -> Finding:
    extra = f" (ไฟล์ {safe(', '.join(others[:3]))} ไม่มีหัวคอลัมน์ของรายการเบิก)" if others else ""
    return finding("missing_file", "blocking", "preflight", "ขาดไฟล์",
                   f"ยังไม่มีไฟล์รายการเบิกค่าใช้จ่าย (Excel หรือ CSV){extra} ผมต้องใช้ไฟล์นี้ก่อนจึงจะเริ่มตรวจได้",
                   [("รอก่อน จะอัปโหลดให้", "wait", "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้"),
                    ("ยกเลิกรอบนี้", "cancel", "ปิดรอบนี้ไว้ก่อน")], "wait", kind="request_files", subject="claims",
                   request=[{"role": "claims", "label": "ไฟล์รายการเบิกค่าใช้จ่าย",
                             "why": "ทุกรายการที่ตรวจมาจากไฟล์นี้: เลขที่เบิก พนักงาน วันที่ หมวด ร้านค้า จำนวนเงิน ใบเสร็จ",
                             "required": True, "accept": [".xlsx", ".xlsm", ".csv"]}],
                   facts={"missing": "claims"})
