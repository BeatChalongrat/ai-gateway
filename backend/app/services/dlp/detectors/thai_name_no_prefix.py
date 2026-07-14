"""Fallback Thai-name detector — no title prefix required.

The default ``thai_name`` detector is title-prefix gated: it only fires
when a Thai honorific (``นาย``, ``นาง``, ``ดร.`` …) precedes the name.
When PresidioDetector is enabled the Presidio PERSON recognizer covers
the remaining bare-name cases, but when a tenant turns NER off (and on
deployments where ``presidio-analyzer`` is not installed at all) any
Thai personal name without an honorific slips through unmasked — a
direct PDPA exposure.

This detector closes that gap with three deliberately conservative
passes, all emitting ``severity=MEDIUM`` / ``priority=-5`` so the
overlap resolver always prefers the higher-precision ``thai_name``
detector on shared spans:

1. **Two-word + context-keyword** (original): two consecutive Thai-script
   words separated by whitespace, each 2-15 chars, with a phone-book
   context keyword (``ชื่อ``/``ลูกค้า``/``name``/…) within ±25 chars.
2. **Gazetteer + sensitive-context** (Fix #4): a known common Thai given
   name (``thai_common_names.json``) that appears as a standalone token
   AND has a financial/health/legal/PII keyword within ±100 chars. A
   bare gazetteer hit with no sensitive context is NOT masked — this is
   what keeps false positives low on ambiguous nickname-like words.
3. **Person-list** (Fix #3): inside a person-list context
   (``รายชื่อพนักงาน``/``CC:``/…), items separated by ``,`` / ``|`` /
   ``;`` / newline that are EITHER title-prefixed (``คุณสมชาย``) OR a
   gazetteer name. Bare non-name Thai words in the list are rejected.

Registry-side activation: registered unconditionally (regardless of
``ner_enabled``) because the shipped image has no Thai spaCy model, so
this is the only bare-name net. ``priority=-5`` keeps precision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# Same Thai-script character class used by ``thai_name`` and the
# ``thai_address`` detectors — covers base letters, combining marks,
# and tone marks. Digits and punctuation are intentionally excluded so
# the pattern cannot accidentally swallow numeric IDs or address bodies.
_THAI_CHAR = r"[ก-ฺเ-๎]"

# --- Pass 1: two-word + context keyword -------------------------------

# Surrounding-context keywords that signal a nearby Thai noun phrase is
# a person's name. Fix #6 adds English field-label keywords so JSON/API
# payloads (``{"name": "สมชาย ใจดี"}``) validate too.
_CONTEXT_KEYWORDS = (
    "ชื่อ",
    "ผู้ใช้",
    "ลูกค้า",
    "พนักงาน",
    "ผู้ส่ง",
    "เจ้าของ",
    "ลงชื่อ",
    "ผู้รับ",
    # English field labels (Fix #6)
    "name",
    "Name",
    "NAME",
    "customer",
    "employee",
    "staff",
    "member",
    "owner",
    "patient",
    "client",
    "user",
    "recipient",
    "defendant",
    "plaintiff",
    "suspect",
)
_CONTEXT_RE = re.compile("|".join(re.escape(k) for k in _CONTEXT_KEYWORDS))

# Exactly two whitespace-separated Thai-script words. A negative
# lookahead refuses to start the match on a context-keyword token so the
# greedy regex doesn't grab ``ชื่อ สมชาย`` and then fail the gate. Only
# the Thai keywords are relevant here (English can't sit at a Thai-char
# position), so the lookahead uses the Thai subset.
_THAI_CONTEXT_KEYWORDS = tuple(
    k for k in _CONTEXT_KEYWORDS if re.search(_THAI_CHAR, k)
)
_CONTEXT_KEYWORD_ALT = "|".join(re.escape(k) for k in _THAI_CONTEXT_KEYWORDS)
_NAME_RE = re.compile(
    rf"(?<!{_THAI_CHAR})"
    rf"(?!(?:{_CONTEXT_KEYWORD_ALT})(?:\s|$|[^ก-ฺเ-๎]))"
    rf"({_THAI_CHAR}{{2,15}}\s+{_THAI_CHAR}{{2,15}})"
    rf"(?!{_THAI_CHAR})"
)

# Window of characters before/after the match span to scan for a
# context keyword. Searched *outside* the match span so a noun phrase
# ending in (e.g.) ``ลูกค้า`` cannot self-validate.
_CONTEXT_WINDOW = 25


# --- Pass 2: gazetteer + sensitive context (Fix #4) -------------------

_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "thai_common_names.json"
)


def _load_gazetteer() -> frozenset[str]:
    """Load common Thai given names. Names shorter than 3 codepoints are
    dropped from the auto-matcher — ultra-short nicknames (``เอ``/``บี``)
    are too ambiguous to match even with a sensitive-context gate."""
    try:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return frozenset(
            n
            for n in data.get("thai_first_names", [])
            if isinstance(n, str) and len(n) >= 3
        )
    except (OSError, ValueError):
        return frozenset()


_GAZETTEER = _load_gazetteer()

if _GAZETTEER:
    # Longest-first so a longer name wins over a shorter prefix of it.
    # Whole-token boundaries via Thai-char negative lookarounds so
    # ``แก้ว`` never matches inside ``แก้วน้ำ``.
    _sorted_names = sorted(_GAZETTEER, key=len, reverse=True)
    _GAZETTEER_RE: re.Pattern | None = re.compile(
        rf"(?<!{_THAI_CHAR})(?:"
        + "|".join(re.escape(n) for n in _sorted_names)
        + rf")(?!{_THAI_CHAR})"
    )
else:
    _GAZETTEER_RE = None

# A gazetteer hit is masked ONLY when one of these appears within
# ±_SENSITIVE_WINDOW chars — a name next to money/health/legal/PII data
# is what makes it worth protecting; a bare name in neutral prose is
# left alone to keep false positives low.
_SENSITIVE_CONTEXT = (
    # financial
    "เงินเดือน", "เงิน", "โบนัส", "ค่าจ้าง", "รายได้", "รายรับ",
    "salary", "income", "โอน", "บัญชี", "เครดิต", "หนี้", "กู้",
    "ผ่อน", "ยอด",
    # PII
    "เบอร์", "โทร", "อีเมล", "email", "ที่อยู่", "address", "บัตร",
    "เลขบัตร", "บัตรประชาชน", "พาสปอร์ต", "passport",
    # health
    "ป่วย", "โรค", "ผลตรวจ", "วินิจฉัย", "อาการ", "HbA1c", "HIV",
    "เบาหวาน", "ความดัน", "มะเร็ง", "แพ้", "รักษา",
    # legal
    "จำเลย", "โจทก์", "ผู้ต้องหา", "ผู้เสียหาย", "ผู้ร้อง", "ข้อหา",
    "คดี", "ศาล",
    # HR
    "พนักงาน", "ตำแหน่ง", "แผนก", "ฝ่าย", "ลาออก", "สมัคร",
)
_SENSITIVE_RE = re.compile(
    "|".join(re.escape(k) for k in _SENSITIVE_CONTEXT), re.IGNORECASE
)
_SENSITIVE_WINDOW = 100


# --- Pass 3: person-list (Fix #3) -------------------------------------

# Presence of one of these enables list-item scanning. Precision does
# NOT rely on this list (a product list like ``รายชื่อสินค้า: กล้วย, ...``
# is safe because each item must still be prefixed or a gazetteer name).
_LIST_CONTEXT_KEYWORDS = (
    "รายชื่อ", "พนักงาน", "ลูกค้า", "สมาชิก", "ผู้เข้าร่วม", "ผู้ร่วม",
    "ผู้รับ", "ผู้ส่ง", "เรียน", "CC", "cc",
    "employee", "customer", "staff", "member",
)
_LIST_CONTEXT_RE = re.compile(
    "|".join(re.escape(k) for k in _LIST_CONTEXT_KEYWORDS)
)
_LIST_PREFIX = r"(?:คุณ|นางสาว|น\.ส\.|นาย|นาง|ดร\.)"
# An item is a Thai word delimited by a list separator / colon / line
# boundary on both sides, with an optional title prefix.
_LIST_ITEM_RE = re.compile(
    rf"(?:(?<=[,，|;:\n])|^)\s*"
    rf"(?P<pre>{_LIST_PREFIX}\s*)?"
    rf"(?P<name>{_THAI_CHAR}{{2,15}})"
    rf"\s*(?=[,，|;\n]|$)",
    re.MULTILINE,
)


class ThaiNameNoPrefixDetector(BaseDetector):
    name = "thai_name_no_prefix"
    category = "pii"
    # MEDIUM (vs ``thai_name``'s HIGH) — bare-name matches are a softer
    # signal. The classifier may still escalate via the multi-category
    # rule.
    default_sensitivity = SensitivityLevel.MEDIUM
    # Negative priority so the overlap resolver always picks
    # ``thai_name`` (priority 0) or any specific identity detector on
    # shared spans. This detector is the floor of last resort.
    priority = -5

    def detect(self, text: str) -> list[DetectionMatch]:
        seen_spans: set[tuple[int, int]] = set()
        spans: list[tuple[int, int, str]] = []

        spans.extend(self._detect_two_word(text))
        spans.extend(self._detect_gazetteer(text))
        spans.extend(self._detect_list(text))

        matches: list[DetectionMatch] = []
        for start, end, matched in spans:
            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_name",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=matched,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches

    # -- Pass 1 --------------------------------------------------------
    def _detect_two_word(self, text: str) -> list[tuple[int, int, str]]:
        # Walk with an explicit cursor (not ``finditer``): on a
        # context-filter reject, advance by one codepoint so a rejected
        # leading word can't swallow a real downstream name.
        out: list[tuple[int, int, str]] = []
        pos = 0
        n = len(text)
        while pos < n:
            m = _NAME_RE.search(text, pos)
            if m is None:
                break
            start, end = m.start(1), m.end(1)
            low = max(0, start - _CONTEXT_WINDOW)
            high = min(len(text), end + _CONTEXT_WINDOW)
            before = text[low:start]
            after = text[end:high]
            if _CONTEXT_RE.search(before + after):
                out.append((start, end, m.group(1)))
                pos = end
            else:
                pos = start + 1
        return out

    # -- Pass 2 --------------------------------------------------------
    def _detect_gazetteer(self, text: str) -> list[tuple[int, int, str]]:
        if _GAZETTEER_RE is None:
            return []
        out: list[tuple[int, int, str]] = []
        for m in _GAZETTEER_RE.finditer(text):
            start, end = m.start(), m.end()
            low = max(0, start - _SENSITIVE_WINDOW)
            high = min(len(text), end + _SENSITIVE_WINDOW)
            if _SENSITIVE_RE.search(text[low:high]):
                out.append((start, end, m.group()))
        return out

    # -- Pass 3 --------------------------------------------------------
    def _detect_list(self, text: str) -> list[tuple[int, int, str]]:
        if not _LIST_CONTEXT_RE.search(text):
            return []
        out: list[tuple[int, int, str]] = []
        for m in _LIST_ITEM_RE.finditer(text):
            pre = m.group("pre")
            name = m.group("name")
            # Accept only prefixed items or known gazetteer names —
            # this is the precision gate that keeps arbitrary Thai words
            # in a comma list from being masked.
            if pre is None and name not in _GAZETTEER:
                continue
            start = m.start("pre") if pre is not None else m.start("name")
            end = m.end("name")
            out.append((start, end, text[start:end]))
        return out
