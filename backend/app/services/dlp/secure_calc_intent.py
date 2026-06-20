"""Deterministic secure-calculator intent detection — Phase 4A.2.6.1.

Keyword/phrase → :class:`CalcOperation`. NO LLM, no fuzzy matching, no eval.
Ambiguous input (more than one distinct operation matched) → ``None`` (deny;
chat proceeds normally). This NEVER inspects numbers — operand collection is a
separate, Vault-V2-eligibility-gated step. NO raw value is read here.
"""

from __future__ import annotations

import re

from app.services.dlp.secure_calc import CalcOperation

# Ordered (operation, pattern). All matched; if >1 distinct op → ambiguous.
# NOTE: Thai has no word boundaries, so ``\b`` is used ONLY around the English
# keywords; Thai keywords are matched as plain substrings. Stray Thai matches
# are harmless — the calc still requires >= 2 Vault-V2-eligible operands.
_PATTERNS: list[tuple[CalcOperation, re.Pattern]] = [
    (CalcOperation.PERCENTAGE_CHANGE, re.compile(
        r"เปอร์เซ็นต์(การเปลี่ยนแปลง|เพิ่มขึ้น|ลดลง)|percent(age)?\s*change|%\s*เปลี่ยน", re.IGNORECASE)),
    (CalcOperation.AVERAGE, re.compile(r"ค่าเฉลี่ย|เฉลี่ย|\baverage\b|\bmean\b", re.IGNORECASE)),
    (CalcOperation.DIFFERENCE, re.compile(
        r"ส่วนต่าง|ผลต่าง|ลบ|หัก|\bdifference\b|\bsubtract\b|\bminus\b", re.IGNORECASE)),
    (CalcOperation.MULTIPLY, re.compile(r"คูณ|\bmultiply\b|\btimes\b", re.IGNORECASE)),
    (CalcOperation.DIVIDE, re.compile(r"หาร|\bdivide\b", re.IGNORECASE)),
    (CalcOperation.SUM, re.compile(
        r"ผลรวม|รวม|บวก|\bsum\b|\badd\b|\btotal\b", re.IGNORECASE)),
]

# Operation → deterministic Thai response template (the canonical assistant text
# carries the result token; the owner display restores it to the raw result).
_TEMPLATES: dict[CalcOperation, str] = {
    CalcOperation.SUM: "ผลรวมคือ {tok}",
    CalcOperation.ADD: "ผลรวมคือ {tok}",
    CalcOperation.AVERAGE: "ค่าเฉลี่ยคือ {tok}",
    CalcOperation.DIFFERENCE: "ส่วนต่างคือ {tok}",
    CalcOperation.SUBTRACT: "ส่วนต่างคือ {tok}",
    CalcOperation.MULTIPLY: "ผลคูณคือ {tok}",
    CalcOperation.DIVIDE: "ผลหารคือ {tok}",
    CalcOperation.PERCENTAGE_CHANGE: "เปอร์เซ็นต์การเปลี่ยนแปลงคือ {tok}",
}

# Safe wording when a calc was requested but cannot be performed securely.
CALC_SAFE_ERROR = "ไม่สามารถคำนวณได้อย่างปลอดภัยจากข้อมูลที่มีอยู่"


def detect_calc_operation(text: str) -> "CalcOperation | None":
    """Return the single matched operation, or ``None`` (no/ambiguous intent)."""
    if not isinstance(text, str) or not text.strip():
        return None
    matched = []
    for op, pat in _PATTERNS:
        if pat.search(text):
            matched.append(op)
    distinct = list(dict.fromkeys(matched))
    if len(distinct) == 1:
        return distinct[0]
    return None  # zero matches OR ambiguous (>1 distinct) → deny


def calc_template(operation: CalcOperation, token: str) -> str:
    return _TEMPLATES.get(operation, "ผลลัพธ์คือ {tok}").format(tok=token)
