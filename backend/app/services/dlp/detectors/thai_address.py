"""Thai address detector (keyword-anchored).

Thai postal addresses are structured with recognisable leading
keywords (``บ้านเลขที่``, ``ที่อยู่``, …). Rather than try to parse
every address component, we anchor on the leading keyword and span
forward up to 200 characters or a newline — whichever comes first.

Severity is MEDIUM; addresses rarely warrant BLOCK action.

Precision/recall stance (FIX26 §1.2, the owner's keyword-precision split):
the leading keyword is graded. HIGH-precision keywords (``บ้านเลขที่``,
``อยู่บ้านเลขที่``, ``ที่อยู่`` as a phrase-initial noun) fire on a house
number within 30 characters alone, so building/condo/village-only
addresses mask again. AMBIGUOUS keywords (``เลขที่`` "the number that…",
``อยู่ที่`` "is at", ``ส่งที่`` "send to") keep FIX23's strict rule — house
number AND an address component (road, soi, moo, tambon/amphoe/province
words or abbreviations, khwaeng/khet, Bangkok, or a 5-digit postcode).
Two openers need no keyword of this kind (8 Oct 2026): a delivery or
pick-up lead-in said with an object or a direction (``ส่งเอกสารไปที่`` /
``จัดส่งไปที่`` / ``ส่งของมาที่`` / ``นัดรับที่`` — the AMBIGUOUS rule), and a
house number followed by a street-level part and a second part, whatever
comes before it. Both mask from the place on, not from the lead-in.
KNOWN GAPS that remain: addresses with no number in them at all, a prose
gap of more than 30 characters before the number, ambiguous-keyword
addresses written with only a building / estate / bare province name,
and a place named after a verb that is neither a keyword nor a delivery
lead-in (``เจอกันที่`` / ``ประชุมที่`` + a building and a floor: a meeting
place, left alone on purpose since FIX38).
Fixtures exist in BOTH directions (``test_thai_address_recall.py`` must
mask; ``test_thai_address.py`` prose must not; the FIX26 prose corpus).

FIX23 — the body must (a) carry a digit within 30 characters of the
keyword (a house number) AND (b) contain an address component on the
same line (ถนน/ซอย/หมู่/ตำบล/อำเภอ/จังหวัด/แขวง/เขต/กรุงเทพ/กทม or a
5-digit postcode). Without those anchors, ordinary Thai prose
containing ``เลขที่`` ("the number that…") / ``อยู่ที่`` ("is at") /
``ที่อยู่`` was swept to end-of-line as an "address": the question
``เลขที่ให้จำเมื่อกี้คืออะไร`` became ``<<ADDRESS_1>>`` on input (the
model echoed the token and restore turned it back into the user's own
question), and the answer ``เลขที่ให้จำคือ 74-…`` was masked on output. The Rust port (``dlp-sidecar/src/detectors/
thai_address.rs``) carries the identical regex — change both.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# FIX46 — the word lists live in data files shared byte-for-byte with the
# Rust sidecar (dlp-sidecar/data/, include_str!'d there); a parity test
# hashes both copies. Before this, the "what counts as an address part"
# question was answered by TWO hand-typed lists in this file (the entry
# gate's regex and the end-trim's tuple) that had drifted: the gate knew
# nothing of ตึก/อาคาร/ห้อง/ชั้น/คอนโด, so ``อยู่ที่ 23 ตึกบี ห้อง 402``
# passed the trim's idea of an address and failed the gate's.
_DATA = Path(__file__).resolve().parents[1] / "data"


def _words(fname: str) -> tuple[str, ...]:
    return tuple(
        ln.strip()
        for ln in (_DATA / fname).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    )


# Structural components — the ENTRY GATE and the first half of the trim list.
_GATE_COMPONENTS = _words("thai_address_components.txt")
# Locality words the trim also keeps but that do not qualify a body on their
# own (บ้าน / เลขที่ / แยก / ริม / ใกล้ / ตรงข้าม / หลัง / ข้าง …).
_TRIM_ONLY = _words("thai_address_trim_words.txt")
_CLAUSE_WORDS = _words("thai_address_clause_starts.txt")
# 77 provinces + the districts whose names begin with a clause word
# (ขอนแก่น → ขอ, หาดใหญ่ → หา, มีนบุรี → มี, มาบตาพุด → มา): an exact-token
# match is an address part, never a clause start.
_LOCALITIES = frozenset(_words("thai_address_localities.txt"))


# FIX26 §1.2 — keyword-precision split (owner decision after FIX23b §2).
#   HIGH-precision keywords (บ้านเลขที่ / อยู่บ้านเลขที่ / ที่อยู่): a digit
#   within 30 chars is enough — the permissive body returns for them, so
#   ``ที่อยู่ 199/22 คอนโดลุมพินี`` masks again.
#   AMBIGUOUS keywords (เลขที่ "the number that…", อยู่ที่ "is at", ส่งที่
#   "send to"): FIX23's strict rule stays — digit AND an address component
#   or postcode — which is what keeps ``เลขที่ให้จำเมื่อกี้คืออะไร`` unmasked.
# ``ที่อยู่`` is itself two-faced: the noun "address" starts a phrase, while
# glued after a Thai letter (สินค้าที่อยู่ในคลัง) or followed by a locative
# preposition (ที่อยู่ใน / ที่อยู่ตรง …) it is the relative clause "…that is
# at/in…" — those two shapes are excluded from the HIGH grade.
_HIGH_KW = (
    r"(?:อยู่บ้านเลขที่|บ้านเลขที่"
    r"|(?<![ก-ฺเ-๎])ที่อยู่(?![\s:：]*(?:ใน|ตรง|บน|ใต้|ข้าง|ระหว่าง|ใกล้|แถว|กับ|ไหน)))"
)
# A ``ที่อยู่`` that fails the HIGH shape (glued: จำที่อยู่ / ส่งไปที่อยู่) is
# not thrown away — it drops to the AMBIGUOUS grade and still masks a real
# address that carries a component or postcode.
_AMBIG_KW = r"(?:เลขที่|ที่อยู่|อยู่ที่|ส่งที่)"
_DIGIT_NEAR = r"(?=[^\n\d]{0,30}\d)"
# Sara am, one spelling. The scan's NFKC pass (and the sidecar's) splits ``ำ`` (U+0E33) into nikhahit +
# sara aa (U+0E4D U+0E32) before any detector sees the text; the word lists are written the way people
# type. So ``ตำบล`` / ``อำเภอ`` / ``ลำปาง`` in the lists never matched ``ตําบล`` / ``อําเภอ`` / ``ลําปาง`` in the
# text a chat is actually scanned as: an address that had only those for its parts did not pass the gate,
# and the end-trim stopped in front of them. The detector called directly, on text as typed, matched —
# which is why the fixtures did not show it. Both spellings are one word here: the regexes take either,
# and a token is put back into the typed spelling before it is compared or measured.
_AM = "\u0e33"
_AM_SPLIT = "\u0e4d\u0e32"


def _typed(tok: str) -> str:
    """``tok`` as a person types it: a split sara am put back together."""
    return tok.replace(_AM_SPLIT, _AM)


def _alt(words) -> str:
    return "|".join(re.escape(w).replace(_AM, f"(?:{_AM}|{_AM_SPLIT})") for w in words)


_COMPONENT_ALT = _alt(_GATE_COMPONENTS)
# A gate component must START a token: ``ต่อชั้น`` / ``ริมถนน`` glued after a
# Thai letter is prose, ``ชั้น 5`` / ``ถนนสุขุมวิท`` after a space or a
# number is an address part. (``ราคาอยู่ที่ 1200 ต่อชั้น 2 ชิ้น`` fired the
# moment ชั้น joined the gate; this is what keeps it out.)
_NOT_AFTER_THAI = r"(?<![ก-ฺเ-๎])"
_COMPONENT = rf"(?=[^\n]{{0,200}}?{_NOT_AFTER_THAI}(?:{_COMPONENT_ALT}|\d{{5}}(?=[\s,]|$)))"
# FIX46 #6 — lead-in words. ``ไปที่`` / ``มาที่`` / ``นัดที่`` / ``แวะที่`` and a
# bare ``อยู่`` are everyday words, so they are admitted on the narrowest
# shape only: a HOUSE NUMBER immediately after them (``ไปที่ 23 ตึกบี``,
# ``เขาอยู่ 88/4 ถนน…``) whose next token is an address part.
# ``ไปที่หน้า 3`` (a word between) and ``นัดที่ห้องประชุมชั้น 2`` (no number
# first) never enter; ``ไปที่ 3 โมง`` has no address part after the number.
# The AMBIGUOUS grade with an even tighter door.
_LEADIN_KW = r"(?:ไปที่|มาที่|นัดที่|แวะที่|อยู่)"
_HOUSE_NO_THEN_COMPONENT = (
    rf"(?=[\s:：]*\d+(?:/\d+)?(?:[\s,]+|$)\s*{_NOT_AFTER_THAI}(?:{_COMPONENT_ALT}))"
)
# FIX46 #5 (sf03) — ``…หน้างานที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ``: a bare
# ``ที่`` followed by ``<component><name> <number>`` and a SECOND address
# part after the number. ``ที่ถนนนี้รถติดมาก 3 ชั่วโมง`` has no second part.
# The ``ที่`` may be glued to the previous word (``หน้างานที่ ซอย…``) — that
# glued ``ที่`` IS the "at" this shape is about, so no left lookbehind here.
# The FIRST part must be street-level (ซอย/ถนน/หมู่/ตำบล/แขวง/เขต): rooms,
# floors and buildings after a bare ``ที่`` are meeting places —
# ``ประชุมที่ห้อง 402 ชั้น 5`` is in the FIX38 prose corpus as must-not-fire.
_STREET_ALT = _alt(("ซอย", "ซ.", "ถนน", "ถ.", "หมู่บ้าน", "หมู่", "ตำบล", "ต.", "แขวง", "เขต"))
_COMPONENT_NUMBER_COMPONENT = (
    rf"(?=\s*(?:{_STREET_ALT})\S{{0,25}}\s+\d[\d/\-]*(?:[\s,]+|$)"
    rf"[^\n]{{0,120}}?(?:{_COMPONENT_ALT}|\d{{5}}(?=[\s,]|$)))"
)
# 8 Oct 2026 — two openers more, found by asking the engine about lead-ins one by one
# (scripts/probes/AM13/dlp_lead_matrix.py). ``ส่งที่`` was the only way of saying "send it to" this detector
# heard, and a house number was only an address when one of a handful of words came before it:
#
#   ส่งเอกสารไปที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ          not found (ส่งที่ + the same place: found)
#   นัดรับที่ 99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500    not found
#   99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500             not found — a whole address, on its own
#
# (On a tenant with name recognition the street and the city are masked by it; the house number, the
# district and the postcode are not, and where it is off or down nothing was.)
#
# DELIVERY — sending and collecting, said with an object or a direction: ส่ง<what>ไปที่ / ส่ง<what>มาที่ /
# จัดส่ง<what>ที่ / ส่งถึง / นัดรับ<what>ที่ / มารับที่ / รับ<what>ที่. The AMBIGUOUS grade exactly: a digit within
# 30 characters AND an address part. ``<what>`` is at most 14 characters with no space, no digit and no
# ``ที่`` in it, so plain ``ส่งที่`` / ``จัดส่งที่`` stay with the keyword above and mask as they always did.
# What is masked begins AFTER the lead-in: a person who typed "ส่งเอกสารไปที่ …" still has "ส่งเอกสารไปที่"
# in front of the placeholder — the model is told that something is sent, and what, and not where.
_WHAT = r"(?:(?!ที่)[^\s\d])"
_DELIVERY_KW = (
    rf"(?:(?:จัด)?ส่ง{_WHAT}{{1,14}}?ที่"
    rf"|(?:จัด)?ส่งถึง(?:ที่)?"
    rf"|(?:นัด|มา|ไป)?รับ{_WHAT}{{0,14}}?ที่)"
)
# HOUSE NUMBER FIRST — no lead-in at all: a number that starts a token (``99/9``, ``12``), a street-level
# part right after it, and a SECOND address part or a postcode further on. The same strength as the bare
# ``ที่`` shape above, with the number in front instead of behind: ``3 ถนน`` alone is not an address,
# ``99/9 ถนนสีลม แขวงสีลม`` is, whatever was typed before it. The lead-ins above are tried first at every
# position, so everything that matched before matches as it did.
# (a Thai word glued in front — ``พักที่99/9`` — is no reason: Thai writes no space there; what the
# lookbehind keeps out is the tail of a longer number or code: ``A12``, ``1,250``, ``10:30``, ``02-123``)
_NUMBER_STARTS_TOKEN = r"(?<![\d/\-.,:A-Za-z])"
_HOUSE_NO_FIRST = (
    rf"{_NUMBER_STARTS_TOKEN}(?=\d+(?:/\d+)?[\s,]+(?:{_STREET_ALT})\S{{0,25}}[\s,]+"
    rf"[^\n]{{0,120}}?{_NOT_AFTER_THAI}(?:{_COMPONENT_ALT}|\d{{5}}(?=[\s,]|$)))"
)
_BODY = r"(?P<body>[^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))"
_ADDRESS_RE = re.compile(
    rf"(?:({_HIGH_KW}[\s:：]*{_DIGIT_NEAR}"
    rf"|{_AMBIG_KW}[\s:：]*{_DIGIT_NEAR}{_COMPONENT}"
    rf"|{_LEADIN_KW}{_HOUSE_NO_THEN_COMPONENT}[\s:：]*"
    rf"|ที่{_COMPONENT_NUMBER_COMPONENT}[\s:：]*)"
    # the two below have no group of their own: what is masked begins where the body does
    rf"|{_DELIVERY_KW}[\s:：]*{_DIGIT_NEAR}{_COMPONENT}"
    rf"|{_HOUSE_NO_FIRST})"
    rf"{_BODY}"
)


# FIX38 Part G — where does the address END? The body above is lazy but
# anchored on end-of-line, so with no postcode it always ran to the end of
# the line. The owner's own live-test sentence exposed it:
#
#   ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ…
#
# was masked as ONE <<ADDRESS_1>> from ``55/12`` to the last word — the
# order, the quantities and the request itself all vanished and the model
# had nothing left to answer. That is the swallow shape the FIX23 audit
# named, one level up: not into the next word, into the rest of the turn.
#
# The trim below walks the body token by token (whitespace-split, which is
# how people separate address components) and keeps:
#   * address-ish tokens — carry a digit (55/12, 5, 10260), start with a
#     component word or abbreviation (ซอย/ถนน/หมู่/ต./อ./จ./แขวง/เขต/อาคาร/
#     ตึก/ชั้น/ห้อง/คอนโด/หมู่บ้าน …), or are กรุงเทพ/กทม;
#   * up to two bare Thai tokens in a row after those — a district and a
#     province written without keywords (``77/1 บางใหญ่ นนทบุรี``);
# and stops at the first token that begins a new clause — a verb or a
# particle from the stop list (สั่ง/ส่ง/ช่วย/ขอ/แล้ว/ครับ/ค่ะ/นะ …) or the
# third bare token in a row. The postcode rule is unchanged: a 5-digit
# postcode still ends the body on the spot. Mirrored in the Rust sidecar.
_COMPONENT_STARTS = _GATE_COMPONENTS + _TRIM_ONLY
_CLAUSE_STARTS = _CLAUSE_WORDS
# FIX46 #5 (hr11) — a clause word is matched as a PREFIX because Thai glues
# verbs (ขอให้ / ทำให้ / มาส่ง). Two-character verbs are also the first two
# characters of place names — ขอนแก่น, หาดใหญ่, มีนบุรี, มาบตาพุด — and the
# prefix rule ended the address right before the province. So: a token in
# the locality list is an address part outright; a bare token followed by a
# 5-digit postcode is the "province + postcode" unit and is kept; and a
# two-character clause prefix only counts on a short token (≤ 5 chars:
# ขอให้ / ทำให้ / มาส่ง / ไปหา / ดูแล), never on a 7-character province.
_SHORT_PREFIX_MAX_TOKEN = 5
_POSTCODE_RE = re.compile(r"^\d{5}$")
# A bare token is kept only when it can be a district/province written
# without its keyword: at most 12 characters (บางใหญ่ / ลาดพร้าว / คลองเตย;
# the long provinces are in the locality list) and carrying no clause word
# inside it. Thai glues whole clauses into one whitespace token —
# ``เขาบอกว่าขอพรให้ปิดดีลได้ก่อนสิ้นเดือนด้วย`` is ONE token — and the old
# "two bare tokens" allowance swallowed exactly that (the sf03 decoys).
_BARE_MAX_CHARS = 12
_CLAUSE_INSIDE = tuple(w for w in _CLAUSE_WORDS if len(w) >= 3)
_MAX_BARE_RUN = 2
# The address is the match — never "the rest of the line".
#
# Until 2026-10-08 the walk below ended with ``return end if end > body_start
# else body_end``: when it kept nothing, the whole lazy body — everything to the
# end of the line — was the address. And it kept nothing whenever the FIRST
# token after the keyword was a place's own name longer than 12 characters:
#
#   ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด
#
# ``สำนักงานใหญ่`` is 12 characters as typed and 13 once the scan's NFKC pass
# has split its sara am in two, so through the registry (the path every chat
# takes) the request typed after the address was masked with it: 62 characters
# as one <<ADDRESS_1>>, and no model was shown what the person asked. A direct
# call to the detector, on the text as typed, did not show it.
#
# So: a token BEFORE the first address part is the place's name, and is kept
# whatever its length, as long as it begins inside the gate's own reach (the
# house number that qualified the match is within 30 characters of the
# keyword). The limits on bare tokens are for what follows the parts, where a
# glued clause is one long token. And there is no fallback to the end of the
# line: a walk that keeps nothing has found no address.
_LEAD_WINDOW = 30


def _is_component_token(tok: str, next_tok: str | None = None) -> bool:
    tok = _typed(tok)
    if any(ch.isdigit() for ch in tok):
        return True
    if tok.startswith(_COMPONENT_STARTS):
        return True
    if tok.strip(",.;:") in _LOCALITIES:
        return True
    # province + postcode written as two tokens: ``นนทบุรี 11130``
    return next_tok is not None and _POSTCODE_RE.match(next_tok) is not None


def _starts_clause(tok: str) -> bool:
    tok = _typed(tok)
    for w in _CLAUSE_STARTS:
        if tok.startswith(w):
            if len(w) <= 2 and len(tok) > _SHORT_PREFIX_MAX_TOKEN:
                continue
            return True
    return False


def trim_address_end(text: str, body_start: int, body_end: int) -> int:
    """Return the end offset of the address inside ``text[body_start:body_end]``."""
    import re as _re

    toks = list(_re.finditer(r"\S+", text[body_start:body_end]))
    end = body_start
    bare_run = 0
    seen_component = False
    for i, mt in enumerate(toks):
        tok = _typed(mt.group())
        nxt = toks[i + 1].group() if i + 1 < len(toks) else None
        tok_end = body_start + mt.end()
        if _is_component_token(tok, nxt):
            end = tok_end
            bare_run = 0
            seen_component = True
            continue
        if seen_component and _starts_clause(tok):
            break
        lead = not seen_component and mt.start() <= _LEAD_WINDOW
        if not lead:
            if len(tok) > _BARE_MAX_CHARS or any(w in tok for w in _CLAUSE_INSIDE):
                break
            bare_run += 1
            if bare_run > _MAX_BARE_RUN:
                break
        end = tok_end
    return end


class ThaiAddressDetector(BaseDetector):
    name = "thai_address"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ADDRESS_RE.finditer(text):
            # Match the full span from the keyword through the address body —
            # or, for the two openers that have no keyword of their own (a
            # delivery lead-in, a house number first), from the place on.
            body_start, body_end = m.span("body")
            full_start = m.start(1) if m.start(1) >= 0 else body_start
            full_end = trim_address_end(text, body_start, body_end)
            matched = text[full_start:full_end].strip()
            if len(matched) < 6:
                continue
            # The trim may have dropped the digit that qualified the match
            # (a far-away house number in prose): then it is not an address.
            if not any(ch.isdigit() for ch in text[body_start:full_end]):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_address",
                    category=self.category,
                    start=full_start,
                    end=full_end,
                    matched_text=matched,
                    severity=self.default_sensitivity,
                )
            )
        return matches
