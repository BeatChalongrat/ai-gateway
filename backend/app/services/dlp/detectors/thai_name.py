"""Thai personal name detector (title-prefix gated).

Detects Thai names by matching a Thai honorific/title prefix followed by
a first name and surname in Thai script.  The title prefix requirement
keeps false positives low — bare Thai words without a title are not
flagged.

Supported prefixes:
    นาย, นาง, นางสาว, น.ส., คุณ, เด็กชาย, เด็กหญิง, ด.ช., ด.ญ.,
    ดร., ศ., ผศ., รศ., นพ., พญ., ทพ., ทพญ., ภก., ภญ.,
    Mr., Mrs., Ms., Miss, Dr.  (+ Thai name)
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_THAI_CHAR = r"[ก-ฺเ-๎]"

# Ordering matters: Python alternation is first-match, so every compound
# form (``ศ.ดร.``, ``พล.ต.อ.``) must appear BEFORE its shorter prefix
# (``ศ.``, ``พล.ต.``) or the short one wins and the rest of the title
# leaks into the name group.
_THAI_TITLE_PREFIXES = (
    r"นางสาว|น\.ส\.|"
    r"หม่อมหลวง|หม่อมราชวงศ์|หม่อมเจ้า|"
    r"ว่าที่ร้อยตรี|"
    r"นาง|นาย|"
    r"เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.|"
    r"คุณ|"
    r"ศ\.ดร\.|ผศ\.ดร\.|รศ\.ดร\.|"
    r"ศ\.|ผศ\.|รศ\.|ดร\.|"
    r"นพ\.|พญ\.|ทพ\.|ทพญ\.|ภก\.|ภญ\.|"
    r"พล\.ต\.อ\.|พล\.ต\.ท\.|พล\.ต\.ต\.|"
    r"พล\.อ\.|พล\.ท\.|พล\.ต\.|"
    r"พ\.ต\.ท\.|พ\.ต\.ต\.|ร\.ต\.ท\.|ร\.ต\.ต\.|"
    r"จ\.ส\.ต\.|ส\.ต\.ท\.|ส\.ต\.ต\.|"
    r"ร\.ต\.อ\.|พ\.ต\.อ\.|"
    r"ม\.ล\.|ม\.ร\.ว\.|ม\.จ\."
)

_ENGLISH_TITLE_PREFIXES = r"Mr\.|Mrs\.|Ms\.|Miss|Dr\."

# Given name required; surname OPTIONAL. A title prefix is a strong
# enough signal on its own — ``คุณสมชาย`` / ``นายสมชาย`` (no surname,
# followed by a digit/punctuation/EOL) is a real name and used to leak
# because the old regex forced a second Thai word.
_THAI_NAME_RE = re.compile(
    rf"(?:{_THAI_TITLE_PREFIXES})\s*"
    rf"{_THAI_CHAR}{{2,}}"
    rf"(?P<surname>\s+{_THAI_CHAR}{{2,}})?"
)

_MIXED_NAME_RE = re.compile(
    rf"(?:{_ENGLISH_TITLE_PREFIXES})\s*"
    rf"{_THAI_CHAR}{{2,}}"
    rf"(?P<surname>\s+{_THAI_CHAR}{{2,}})?"
)

# Whole-match rejects: ``prefix + word`` strings that are common nouns,
# NOT names. Checked with ``match`` (anchored at start): a match that
# BEGINS with one of these compounds is dropped, which keeps the
# existing precision contract (e.g. ``นายกรัฐมนตรี กล่าวต่อ`` → 0). The
# trade-off is that a spelled-out occupation + real name
# (``นายแพทย์ สมชาย``) is also dropped; the abbreviated title
# (``นพ.สมชาย``) still masks. The abstract-noun ``คุณ`` compounds allow a
# trailing Thai run (``คุณภาพดี``); kinship/role ``คุณ`` compounds are
# matched exactly.
_FALSE_POSITIVE_RE = re.compile(
    r"นายก(?:รัฐมนตรี|สมาคม|เทศมนตรี|สภา)|"
    r"นายพล|นายทหาร|นายตำรวจ|นายแพทย์|นายช่าง|นายหน้า|นายแบบ|"
    r"นายจ้าง|นายพราน|นายอำเภอ|นายด่าน|นายทุน|นายประกัน|นายท้าย|"
    r"นางแบบ|นางงาม|นางฟ้า|นางเอก|นางพยาบาล|นางรำ|นางสนม|"
    rf"คุณ(?:ภาพ|ค่า|สมบัติ|ธรรม|ประโยชน์|วุฒิ|ลักษณะ){_THAI_CHAR}*|"
    r"คุณ(?:พ่อ|แม่|หมอ|ครู|นาย|ยาย|ตา|ป้า|น้า|อา|ลุง|พี่|น้อง|หนู)|"
    # FIX26 §1.2 — ``คุณ`` + a particle / adverb / preposition is prose
    # ("ขอบคุณมากครับ", "ขอบคุณนะ", "คุณที่รัก"): ``คุณ`` sits inside
    # ``ขอบคุณ`` and the regex has no word boundary to stop it, so the
    # corpus sweep saw ``คุณมากครับ ช่วยได้เยอะเลย`` masked as a name. None
    # of these words begins a Thai given name.
    r"คุณ(?:มาก|นะ|ครับ|ค่ะ|คะ|จ้า|จ๊ะ|จ้ะ|ด้วย|เลย|ก็|ที่|กับ|ของ|และ|หรือ|ใน|"
    r"จาก|ถึง|เอง|ทั้ง|ทุก|ส(?:ำ|ํา)หรับ|จริง|หลาย|อีก|แล้ว|เท่า|ล่วงหน้า|ๆ)|"
    # ``คุณ`` fused directly to a verb / function word (no space) is a
    # sentence addressed at the listener ("คุณทำอะไรได้บ้าง"), not a
    # name. Only words that never begin a Thai given name are listed
    # bare; ``มี`` and ``จำ`` DO begin real names (มีชัย, มีนา,
    # จำลอง, จำเนียร) so they are gated on a following function word
    # to keep ``คุณมีชัย`` / ``คุณจำลอง`` masked.
    # SARA AM caveat: the scan runs on NFKC-normalized text where
    # ``ำ`` (U+0E33) decomposes to ``ํ``+``า`` (U+0E4D U+0E32), so every
    # word containing ำ must accept both spellings or the guard never
    # fires through the real pipeline.
    r"คุณ(?:ท(?:ำ|ํา)|เป็น|จะ|ช่วย|อยาก|คือ|ชอบ|คิด|พูด|รู้|เข้าใจ|ได้|"
    r"ต้องการ|ต้อง|ควร|ก(?:ำ|ํา)ลัง|ไม่|อยู่|ใช้|เคย|สามารถ|ให้|บอก|ถาม|"
    r"ลอง|กรุณา|โปรด|เห็น|ตอบ|ส่ง|หมายถึง|"
    r"ว่า(?:ยังไง|อย่างไร|ไง|อะไร|จะ|มัน|เรา|ผม|ฉัน|ดี)|"
    r"มี(?:ความ|อะไร|ใคร|กี่|ข้อ|ค(?:ำ|ํา)|วิธี|เวลา|ปัญหา|หน้าที่)|"
    r"จ(?:ำ|ํา)(?:ได้|ไม่|อะไร|ฉัน|ผม|เรา|ชื่อ))"
)

# Words that can trail a name but are NOT surnames — when the optional
# second word is one of these, trim the match back to the given name so
# we don't swallow trailing prose (``คุณสมหญิง จำนวน`` → ``คุณสมหญิง``).
_NOT_SURNAME = frozenset({
    "จำนวน", "ที่", "ได้", "มี", "เป็น", "คือ", "และ", "หรือ", "ว่า",
    "ไป", "มา", "ให้", "กับ", "ของ", "จาก", "ถึง", "โดย", "เมื่อ",
    "แล้ว", "อยู่", "จะ", "ต้อง", "ควร", "อาจ", "น่า", "ยัง",
    "ทั้ง", "ทุก", "บาง", "แต่ละ", "เงินเดือน", "โบนัส", "เบอร์",
    "อีเมล", "ที่อยู่", "บัญชี", "โทร", "ป่วย", "ถูก", "โดน",
    # SARA AM spellings on NFKC-normalised text (ำ → ํ + า), so the trim
    # also fires through the real pipeline. Same set in thai_name.rs.
    "จํานวน", "ตําแหน่ง", "ตำแหน่ง", "แผนก", "อายุ", "วันเกิด", "เลขบัตร",
})


class ThaiNameDetector(BaseDetector):
    name = "thai_name"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in (_THAI_NAME_RE, _MIXED_NAME_RE):
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()

                # Trim an over-captured non-surname trailing word so the
                # mask covers only ``prefix + given name`` in cases like
                # ``คุณสมหญิง จำนวน 10,000``.
                surname = m.group("surname")
                if surname is not None and surname.strip() in _NOT_SURNAME:
                    end = m.start("surname")

                matched = text[start:end]

                # Anchored ``match``: drop any span that begins with a
                # common-noun compound (``นายกรัฐมนตรี …``). The over-
                # capture trim above already handles the ``prefix + name
                # + trailing-word`` case before we get here.
                if _FALSE_POSITIVE_RE.match(matched):
                    continue

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
                    )
                )

        return matches
