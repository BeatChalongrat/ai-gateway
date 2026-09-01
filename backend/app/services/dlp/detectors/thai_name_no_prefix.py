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

from app.services.dlp.detectors.thai_gazetteer import find_names
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

# --- FIX26 §1.2: Pass-1 stop rules --------------------------------------
#
# Thai has no spaces between words, so a whitespace-separated "word" in
# prose is usually a PHRASE (``ต้องส่ง``, ``ว่าสินค้า``), not a name. The
# FIX24 sweep saw ``ชนิด ตอบสั้นๆ`` ("1 kind, answer briefly") and
# ``เบอร์ เลขบัตร`` ("number, card number") masked as people because a
# context keyword (``ชื่อ`` / ``ลูกค้า``) stood nearby. Three cheap
# lexical gates reject a pair when either token is plainly not a name:
#   * EXACT — whole-token common vocabulary (nouns, pronouns, units);
#   * PREFIX — the token STARTS with a function word / common verb
#     (``ต้องส่ง``, ``ควรตั้ง``, ``ไม่ได้``): no Thai given name begins
#     with these syllables (``มา``/``มี``/``ดี`` DO begin names — มานะ,
#     มีชัย, ดีพร้อม — so they are EXACT, not PREFIX);
#   * SUFFIX — the token ENDS with a sentence particle or question word
#     (``…นะ``, ``…ครับ``, ``…ไหม``, ``…ยังไง``), or contains ``ๆ``.
# And a SPAN rule: when the second token is a field LABEL that follows a
# name in forms (``เบอร์`` / ``ที่อยู่`` / ``อีเมล`` / ``อายุ`` …), the
# label is not a surname — the span is trimmed to the first token, and
# that lone token is kept only when a name label (``ชื่อ`` / ``name``)
# immediately precedes it, so ``ชื่อ สมชาย เบอร์ 08…`` masks exactly
# ``สมชาย`` and restoration is no longer off by a token.
# The Rust port carries the SAME three lists (``STOP_EXACT`` /
# ``STOP_PREFIX`` / ``STOP_SUFFIX`` / ``TRAIL_LABELS``) — a test compares
# them to this file. Change both.
_STOP_EXACT = frozenset({
    # pronouns / demonstratives / question words
    "ผม", "ฉัน", "เรา", "คุณ", "เขา", "เธอ", "มัน", "ใคร", "อะไร", "ไหน", "นี้", "นั้น",
    "โน้น", "นี่", "นั่น", "ไหม", "มั้ย", "ยังไง", "อย่างไร", "ทำไม", "เมื่อไหร่", "เท่าไหร่",
    # common nouns that sit beside name keywords in chat
    "ชนิด", "ประเภท", "แบบ", "เบอร์", "เลขบัตร", "บัตร", "อีเมล", "ที่อยู่", "บัญชี",
    "รายงาน", "ไฟล์", "ข้อมูล", "ระบบ", "สินค้า", "ราคา", "ยอด", "จำนวน", "จํานวน",
    "เวลา", "วันนี้", "พรุ่งนี้", "เมื่อวาน", "ตอนนี้", "เดือน", "ปี", "ครั้ง", "อัน", "ชิ้น",
    "คน", "ทุกคน", "บางคน", "หลายคน", "เรื่อง", "งาน", "ประชุม", "บริษัท", "ทีม", "แผนก",
    "ตำแหน่ง", "ตําแหน่ง", "อายุ", "วันเกิด", "เงินเดือน", "โบนัส", "โทร", "หน้า", "ข้อ",
    # single common words that also begin names (kept EXACT on purpose)
    "มา", "มี", "ดี", "ไป", "ใหม่", "เก่า", "มาก", "น้อย", "ใหญ่", "เล็ก",
    # adverbs / conjunctions / aspect
    "แล้ว", "ยัง", "เลย", "ก็", "จึง", "เพราะ", "แต่", "หรือ", "และ", "กับ", "ของ",
    "ก่อน", "หลัง", "ตอน", "ช่วง", "ระหว่าง", "ด้วย", "เอง", "ทั้งหมด", "บ้าง", "อีก",
    "สั้นๆ", "ยาวๆ", "เร็วๆ", "ช้าๆ",
})
_STOP_PREFIX = (
    # verbs / auxiliaries / negation that open a phrase
    "ช่วย", "ต้อง", "ควร", "ถาม", "ตอบ", "บอก", "ว่า", "ไม่", "ให้", "ได้", "ทำ", "ทํา",
    "ขอ", "อยาก", "จะ", "กำลัง", "กําลัง", "เคย", "ลอง", "ใช้", "เอา", "เขียน", "สร้าง",
    "แก้", "ส่ง", "รับ", "ดู", "ดึง", "หา", "เช็ก", "เช็ค", "ตรวจ", "สรุป", "แปล", "อธิบาย",
    "แนะนำ", "แนะนํา", "จัด", "เปิด", "ปิด", "เพิ่ม", "ลบ", "ลด", "คิด", "รู้", "เข้าใจ",
    "เห็น", "พูด", "คุย", "เล่า", "ฟัง", "อ่าน", "ซื้อ", "ขาย", "จ่าย", "โอน", "จอง",
    "เริ่ม", "หยุด", "เสร็จ", "ผ่าน", "พัง", "ขึ้น", "ลง", "ออก", "เข้า", "กลับ", "เปลี่ยน",
    # conjunctions / prepositions / determiners that open a phrase
    "ที่", "ซึ่ง", "เมื่อ", "ถ้า", "หาก", "เพราะ", "แต่", "และ", "หรือ", "กับ", "ใน",
    "จาก", "ถึง", "ตาม", "โดย", "เพื่อ", "สำหรับ", "สําหรับ", "เกี่ยว", "ทุก", "บาง",
    "หลาย", "แต่ละ", "ก่อน", "หลัง", "ยัง", "แล้ว", "ก็", "ตอน", "ช่วง", "เรื่อง",
    "อย่าง", "แบบ", "เท่า", "เกิน", "ประมาณ", "ราว",
)
_STOP_SUFFIX = (
    "นะ", "ครับ", "ค่ะ", "คะ", "จ้า", "จ๊ะ", "จ้ะ", "สิ", "เถอะ", "ล่ะ", "หรอ", "เหรอ",
    "ไหม", "มั้ย", "ยังไง", "อย่างไร", "อะไร", "ไหน", "เท่าไหร่", "เมื่อไหร่", "ทำไม", "ทําไม",
    "ด้วย", "เลย", "บ้าง", "หน่อย", "ที", "ๆ",
)
_TRAIL_LABELS = frozenset({
    "เบอร์", "โทร", "ที่อยู่", "อีเมล", "เลขบัตร", "บัตร", "บัญชี", "เงินเดือน", "โบนัส",
    "ตำแหน่ง", "ตําแหน่ง", "แผนก", "อายุ", "วันเกิด", "จำนวน", "จํานวน",
})
_NAME_LABEL_BEFORE_RE = re.compile(r"(?:ชื่อ|name)\s*[:：]?\s*$", re.IGNORECASE)

# FIX40 — Pass 1's gate: a context keyword ANYWHERE in a 25-character window
# was enough to read two space-separated Thai tokens as "given + surname".
# Thai chat is full of that shape without a name in sight, and the word
# ``ลูกค้า`` is in almost every support message, so the rule fired on
# ``ใบ แตก`` (12 ใบ แตก 3 ใบ → ``12 <<PERSON_B>> 3 ใบ``), ``กล่อง บุบ``,
# ``เครื่อง ค้าง``, ``ตัว เสีย``, ``รอบ บ่าย``, ``น้ำ ใจ``,
# ``ใบ กำกับภาษี`` — measured 30 of 150 protected strings eaten on the
# FIX40 corpus. The keyword must now sit IMMEDIATELY before the pair: a
# name label (``ชื่อ:`` / ``ผู้รับ:`` / ``name:``) or a title
# (``คุณ`` / ``นาย`` / ``พี่`` …). Measured against T1 (require NER
# agreement), T2 (hand stoplist), T3 (no number before) and T5 (dictionary
# gate): this is the only one that removed the over-masking WITHOUT
# costing recall anywhere — see docs/FIX40_REPORT.md §3.
#
# What Pass 1 still carries, measured: the ``ผู้รับ: สุรชัย มั่นคง`` shape.
# Turning the pass off entirely costs exactly that one item across both
# eval sets, which is why it is narrowed rather than deleted.
# The Rust port carries the same regex — change both.
_PASS1_ANCHOR_RE = re.compile(
    r"(?:ชื่อ|นามว่า|ชื่อว่า|ผู้รับ|ผู้ส่ง|ผู้แจ้ง|ลงชื่อ|name)\s*[:：]?\s*$"
    r"|(?:นางสาว|น\.ส\.|นาง|นาย|คุณ|ท่าน|ดร\.|นพ\.|พญ\.|พี่|น้อง)\s*$",
    re.IGNORECASE,
)


def _is_stop_token(tok: str) -> bool:
    """True when a whitespace-delimited Thai token is plainly not a name."""
    if "ๆ" in tok or tok in _STOP_EXACT:
        return True
    if tok.startswith(_STOP_PREFIX):
        return True
    return tok.endswith(_STOP_SUFFIX)


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
        spans.extend(self._detect_lexicon(text))

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
            # FIX40 — the anchor must be immediately to the LEFT of the
            # pair, not merely somewhere in the window.
            if _PASS1_ANCHOR_RE.search(before):
                w1, w2 = m.group(1).split(None, 1)
                if w2 in _TRAIL_LABELS:
                    # FIX26: the second token is a field label, never a
                    # surname. Keep the lone first token only when a name
                    # label immediately precedes it.
                    w1_end = start + len(w1)
                    if not _is_stop_token(w1) and _NAME_LABEL_BEFORE_RE.search(before):
                        out.append((start, w1_end, w1))
                        pos = w1_end
                    else:
                        pos = start + 1
                elif _is_stop_token(w1) or _is_stop_token(w2):
                    pos = start + 1
                else:
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

    # -- Pass 4 (FIX29) ------------------------------------------------
    def _detect_lexicon(self, text: str) -> list[tuple[int, int, str]]:
        """A bare given name from the 10k-name lexicon at a left word
        boundary, ending where a dictionary word begins (``ณัฐพล|บอกว่า``).
        No context gate: the FIX27 measurement showed the gate cost more
        than half the recall while the dictionary-word filter already keeps
        the corpus at zero fires. See thai_gazetteer.py."""
        return [(s, e, text[s:e]) for s, e in find_names(text)]

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
