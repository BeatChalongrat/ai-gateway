"""FIX37 — confidence scoring and context enhancement for Thai NER.

Two of Presidio's four layers were missing from this gateway: an NER
model that speaks Thai, and a context enhancer that adjusts confidence
from the words around a candidate. This module is the second one, plus
the blending rule that turns several weak signals into one score.

The design point: **a Thai NER hit is a vote, not a verdict.** Measured
in the Phase-2 bake-off, the CRF ThaiNER fires on 22 of the 108
ordinary-Thai sentences that must never be masked. Firing on its word
alone would blow the ``corpus_fires == 0`` gate on the first request.
So every candidate gets a score built from independent signals, and only
candidates at or above the threshold become findings:

    score = base(source)
          + title / kin prefix immediately before the span
          + person-context keyword in the ±window
          + gazetteer membership (formal given name / nickname)
          + a regex detector agreeing on the same span
          - the span continuing into a longer Thai word
          - the span being a known ordinary word with no other support

Everything here is a pure function over (text, span) so the thresholds
can be swept in Phase 3 without touching the detector.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"

# ── weights (Phase-3 sweeps the THRESHOLD, not these) ─────────────────────

# Deliberately below the shipped threshold: a NER hit with NO supporting
# signal must not mask on its own. ``เดดไลน์โปรเจกต์`` came back as a
# PERSON from the CRF with nothing else pointing at it, and masked, until
# the base was set under the bar.
BASE_NER_PERSON = 0.45
BASE_NER_LOCATION = 0.45
BASE_NICKNAME = 0.45
# An UNAMBIGUOUS gazetteer name — one of the 10k given names that is not
# also a dictionary word — is strong evidence by itself. The old
# ``find_names`` scan additionally demanded a left word boundary, which
# rejected every name glued to the preceding Thai word
# (``ส่งเอกสารให้อรอนงค์``); the relaxed scan below reinstates those and
# this base lets them stand without extra context.
BASE_GAZETTEER_NAME = 0.70
# Names that are also ordinary words (``มานะ``, ``วีระ``, ``รัตนา``).
# The gazetteer keeps them in a separate ``ambiguous`` list and the old
# regex detector ignored them entirely, because without a scoring layer
# there was no safe way to use them. With one, they start lower and need
# a second signal — a title, a person verb, or a sensitive context.
BASE_GAZETTEER_AMBIGUOUS = 0.45

W_TITLE_PREFIX = 0.30
W_PERSON_CONTEXT = 0.20
W_ADDRESS_CONTEXT = 0.25
W_GAZETTEER = 0.20
W_REGEX_AGREES = 0.15
W_SENSITIVE_CONTEXT = 0.20
P_INSIDE_WORD = 0.60
P_ORDINARY_WORD = 0.35
P_TOO_SHORT = 0.20
# The span is an everyday Thai word (in the 60k dictionary) and is in
# neither name list. ``คุณคิดว่า…`` produced a PERSON span on ``คิด``;
# only this penalty keeps a verb from being masked as a name.
P_DICTIONARY_WORD = 0.35
# A catalogue / list context around the span.
P_NON_PERSON_CONTEXT = 0.35

CONTEXT_WINDOW = 30
ADDRESS_CONTEXT_WINDOW = 45

# ── lexicons ──────────────────────────────────────────────────────────────

# Titles and kinship words that precede a person's name in Thai chat.
# ``พี่`` / ``น้อง`` / ``ป้า`` are the ones the regex ``thai_name``
# detector does NOT accept (it wants a formal honorific), and they are
# exactly what colleagues use for nicknames.
TITLE_PREFIXES = (
    "นางสาว", "น.ส.", "นาง", "นาย", "คุณ", "ท่าน",
    "พี่", "น้อง", "ป้า", "ลุง", "น้า", "อา", "แม่", "พ่อ",
    "เด็กชาย", "เด็กหญิง", "ด.ช.", "ด.ญ.",
    "ดร.", "ศ.", "ผศ.", "รศ.", "นพ.", "พญ.", "ทพ.", "ภก.", "ภญ.",
)

# ``คุณ`` and ``ท่าน`` are titles AND second-person pronouns, so
# ``คุณคิดว่า…`` ("do you think…") looks exactly like a titled name. The
# must-not-fire corpus caught that: ``คิด`` was masked as a person. A weak
# title still adds confidence, but it cannot excuse a span that is an
# ordinary dictionary word.
WEAK_TITLES = frozenset({"คุณ", "ท่าน", "อา", "แม่", "พ่อ"})

# Classifier nouns that turn the word after them into a PLACE, a SEASON
# or an OBJECT — never a person. Thai compounds names freely
# (``วัดชัยมงคล``, ``หน้าฝน``, ``อากาศฟ้าใส``), and all three were caught
# masking an ordinary phrase because the tail happens to be a real given
# name. A person candidate sitting directly behind one of these is
# dropped outright, not merely penalised.
NON_NAME_PREFIXES = (
    "วัด", "โรงเรียน", "โรงพยาบาล", "โรงแรม", "ร้าน", "ตลาด", "หมู่บ้าน",
    "ถนน", "ซอย", "สะพาน", "คลอง", "ภูเขา", "ทะเล", "เกาะ", "อำเภอ",
    "ตำบล", "จังหวัด", "เขต", "แขวง", "นิคม", "สนามบิน", "สถานี",
    "หน้า", "ช่วง", "ฤดู", "อากาศ", "เดือน", "ปี", "วัน",
)


# Verbs and prepositions that introduce a person in Thai chat and are
# glued to the name (``ฝากตูนรับเคสนี้``, ``ให้อรอนงค์``). The gazetteer's
# ``left_boundary`` only accepts ``ชื่อ``-family labels, which left every
# handoff sentence unmasked. The right-hand dictionary test still guards
# the compound cases (``แก้วน้ำ``), and NON_NAME_PREFIXES still wins.
NAME_INTRO_WORDS = ("ฝาก", "ให้", "กับ", "และ", "ถึง", "จาก", "บอก", "พบ", "เจอ", "ตาม")


def name_intro_before(text: str, start: int) -> str | None:
    head = text[max(0, start - 8):start]
    for w in NAME_INTRO_WORDS:
        if head.endswith(w):
            return w
    return None


def non_name_prefix_before(text: str, start: int) -> str | None:
    """The place / season classifier glued to the left of the span, if any."""
    head = text[max(0, start - 12):start]
    for p in NON_NAME_PREFIXES:
        if head.endswith(p):
            return p
    return None

# Words that make "a person is being talked about" likely. Deliberately
# verbs and roles, not names.
PERSON_CONTEXT = (
    "ชื่อ", "ลูกค้า", "ผู้แจ้ง", "ผู้ใช้", "พนักงาน", "ผู้สมัคร", "ผู้ขอ",
    "เจ้าของ", "หัวหน้า", "ผู้จัดการ", "เจ้าหน้าที่", "ผู้รับเหมา",
    "โทรมา", "โทรเข้า", "โทรกลับ", "ติดต่อ", "แจ้งว่า", "แจ้งมา", "บอกว่า",
    "ฝากบอก", "ฝาก", "ส่งต่อ", "ส่งเคส", "รับเคส", "ต่อว่า", "ร้องเรียน",
    "ขอให้", "นัด", "ประสานงาน", "ดูแลบัญชี", "เซ็นสัญญา",
)

# Words that make "this is a catalogue / list of things, not people"
# likely. ``ส้ม`` is both a common nickname and an orange; without this
# the phrase ``รายชื่อสินค้า: กล้วย, ส้ม, …`` scored as a person because
# ``ชื่อ`` sits inside ``รายชื่อ``. Found by the must-not-fire corpus.
NON_PERSON_CONTEXT = (
    "สินค้า", "รายการ", "เมนู", "ผลไม้", "วัตถุดิบ", "สต็อก", "คลังสินค้า",
    "ราคาต่อ", "หน่วยละ", "ชิ้นละ", "สูตร", "ส่วนผสม",
)

# Money / health / legal / employment context. A Thai word that could be
# a name is far more likely to BE one when it sits next to these — this
# is the signal the old ``thai_name_no_prefix`` gazetteer pass used, kept
# here as one contribution among several instead of a hard gate.
SENSITIVE_CONTEXT = (
    "หนี้", "บัตรเครดิต", "เงินเดือน", "เงินกู้", "สินเชื่อ", "ค้างชำระ",
    "โอนเงิน", "บัญชี", "ภาษี", "ค่าจ้าง", "ค่าคอม",
    "ป่วย", "โรค", "เบาหวาน", "ความดัน", "แพ้ยา", "ใบรับรองแพทย์", "ผ่าตัด",
    "ฟ้อง", "คดี", "พยาน", "ทนาย", "ศาล", "ร้องเรียน",
    "สัญญา", "ลาออก", "ลาคลอด", "เตือน", "สอบสวน", "ประวัติ",
)

ADDRESS_CONTEXT = (
    "ที่อยู่", "บ้านเลขที่", "เลขที่", "ส่งของ", "จัดส่ง", "หน้างาน",
    "ซอย", "หมู่", "ถนน", "แขวง", "เขต", "ตำบล", "อำเภอ", "จังหวัด",
    "ต.", "อ.", "จ.", "รหัสไปรษณีย์",
)

# Ordinary Thai words the CRF routinely tags as PERSON / LOCATION on
# conversational text. A hit on one of these is penalised, not dropped:
# a real ``คุณแก้ว`` still clears the bar via the title prefix.
ORDINARY_WORDS = frozenset({
    "ครับ", "ค่ะ", "คะ", "นะครับ", "นะคะ", "ขอบคุณ", "ขอบใจ", "สวัสดี",
    "ช่วย", "หน่อย", "ได้เลย", "สรุป", "ข้อมูล", "ตอบ", "อธิบาย", "แปล",
    "ลูกค้า", "ผู้ใช้", "ท่าน", "คุณ", "พี่", "น้อง", "เรียน",
    "บริษัท", "ทีม", "แผนก", "ฝ่าย", "ระบบ", "ปัญหา", "เอกสาร",
})

_THAI = re.compile(r"[ก-ฺเ-๎]")


@lru_cache(maxsize=1)
def nicknames() -> frozenset[str]:
    try:
        raw = json.loads((_DATA / "thai_nicknames.json").read_text(encoding="utf-8"))
        return frozenset(raw.get("nicknames") or ())
    except Exception:  # noqa: BLE001 — a missing lexicon must not break DLP
        return frozenset()


def _is_thai_char(ch: str) -> bool:
    return bool(_THAI.match(ch))


# ── signal probes ─────────────────────────────────────────────────────────

def title_prefix_before(text: str, start: int) -> str | None:
    """The title / kinship word immediately preceding ``start``, if any.

    Thai writes ``คุณสมชาย`` and ``พี่ตูน`` with no space, so this looks
    at the raw characters before the span rather than at word tokens.
    One optional space is tolerated (``คุณ สมชาย``).
    """
    head = text[max(0, start - 12):start]
    stripped = head.rstrip()
    if len(head) - len(stripped) > 1:
        return None
    for p in TITLE_PREFIXES:
        if not stripped.endswith(p):
            continue
        # The "title" must be a word of its own. ``ขอบคุณที่ติดต่อมา``
        # ends in ``คุณ`` without containing a title at all, and that
        # false title was enough to score ``ที่`` as a person.
        title_start = start - (len(head) - len(stripped)) - len(p)
        if is_word_tail(text, title_start, title_start + len(p)):
            continue
        return p
    return None


def has_context(text: str, start: int, end: int, words: tuple[str, ...], window: int) -> bool:
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    around = text[lo:start] + " " + text[end:hi]
    return any(w in around for w in words)


def continues_into_word(
    text: str, start: int, end: int, *, strict_left: bool = True
) -> bool:
    """True when the span is a fragment of a longer Thai word.

    Thai writes without spaces, so "glued to more Thai script" is the
    normal case, not a signal — ``พี่ตูนที่เป็น`` has Thai on both sides
    of a perfectly good nickname. The first version of this function used
    a bare either-side rule and suppressed every nickname that followed a
    title, which is most of them.

    The real test is the 60k-word dictionary the gazetteer already ships:

    * right side — does ``span + next chars`` spell a longer word?
      ``แก้ว`` inside ``แก้วน้ำ`` and ``ทอง`` inside ``ทองคำ`` die here.
    * left side  — is there a word boundary, a name label (``ชื่อ``), or a
      title/kinship prefix (``คุณ``/``พี่``/``น้อง``)? If none of those,
      the span starts mid-word.
    """
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        if gaz.continues_into_word(text, start, end):
            return True
        if not strict_left:
            # The span is a known name that is NOT a dictionary word, so
            # it cannot be a word fragment; only a compound ENDING in it
            # would be a false positive, and ``is_word_tail`` covers that.
            return is_word_tail(text, start, end)
        left_ok = (
            gaz.left_boundary(text, start)
            or bool(title_prefix_before(text, start))
            or bool(name_intro_before(text, start))
        )
    except Exception:  # noqa: BLE001 — lexicon problems must not mask-block
        before_thai = start > 0 and _is_thai_char(text[start - 1])
        left_ok = (not before_thai) or bool(title_prefix_before(text, start))
    return not left_ok


def _thai_len(s: str) -> int:
    return sum(1 for c in s if _is_thai_char(c))


# ── the blend (N3) ────────────────────────────────────────────────────────

def score_person(
    text: str,
    start: int,
    end: int,
    *,
    source: str,
    in_gazetteer: bool = False,
    regex_agrees: bool = False,
) -> tuple[float, list[str]]:
    """Confidence that ``text[start:end]`` names a person, plus the reasons.

    ``source`` is ``"ner"``, ``"nickname"``, ``"gazetteer"`` or
    ``"gazetteer_ambiguous"``.
    """
    # The relaxed left rule is only safe for a name long enough that a
    # coincidental compound is implausible. Short gazetteer entries
    # (``ฝน``, ``ชัย``) sit inside ``หน้าฝน`` and ``วัดชัยมงคล``, which
    # the decoy set caught being masked.
    strict_left = source != "gazetteer" or _thai_len(text[start:end]) < 4
    span = text[start:end].strip()
    reasons: list[str] = []
    score = {
        "ner": BASE_NER_PERSON,
        "nickname": BASE_NICKNAME,
        "gazetteer": BASE_GAZETTEER_NAME,
        "gazetteer_ambiguous": BASE_GAZETTEER_AMBIGUOUS,
    }.get(source, BASE_NER_PERSON)
    reasons.append(f"base:{source}")

    if title_prefix_before(text, start):
        score += W_TITLE_PREFIX
        reasons.append("title")
    if has_context(text, start, end, PERSON_CONTEXT, CONTEXT_WINDOW):
        score += W_PERSON_CONTEXT
        reasons.append("context")
    if in_gazetteer and not source.startswith("gazetteer"):
        score += W_GAZETTEER
        reasons.append("gazetteer")
    if has_context(text, start, end, SENSITIVE_CONTEXT, CONTEXT_WINDOW):
        score += W_SENSITIVE_CONTEXT
        reasons.append("sensitive-context")
    if regex_agrees:
        score += W_REGEX_AGREES
        reasons.append("regex-agrees")

    if continues_into_word(text, start, end, strict_left=strict_left):
        score -= P_INSIDE_WORD
        reasons.append("-inside-word")
    if span.lower() in ORDINARY_WORDS:
        score -= P_ORDINARY_WORD
        reasons.append("-ordinary-word")
    if _thai_len(span) and _thai_len(span) <= 2:
        score -= P_TOO_SHORT
        reasons.append("-too-short")
    if has_context(text, start, end, NON_PERSON_CONTEXT, CONTEXT_WINDOW):
        score -= P_NON_PERSON_CONTEXT
        reasons.append("-list-context")
    # A span that is an everyday word needs a title to survive. Without
    # this, ``เพลง`` (a real nickname, and the word "song") was masked out
    # of ``เพลงไทยยุคเก้าศูนย์…`` in the must-not-fire corpus.
    _title = title_prefix_before(text, start)
    if (_title is None or _title in WEAK_TITLES) and _is_dictionary_word(span):
        score -= P_DICTIONARY_WORD
        reasons.append("-dictionary-word")

    return max(0.0, min(1.0, score)), reasons


def _is_dictionary_word(span: str) -> bool:
    """The span is an everyday Thai word.

    Being a name too (``แก้ว``, ``ฟ้า``, ``ส้ม``, ``เพลง``) does not
    exempt it: that ambiguity is exactly the risk. Only the unambiguous
    gazetteer — names that are not words at all — is out of scope here,
    and those never reach this test.
    """
    w = span.strip()
    if not w or " " in w:
        return False
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        if w in gaz.names():
            return False
        return w in gaz.words()
    except Exception:  # noqa: BLE001
        return False


def score_location(
    text: str,
    start: int,
    end: int,
    *,
    regex_agrees: bool = False,
) -> tuple[float, list[str]]:
    """Confidence that ``text[start:end]`` is part of a postal address."""
    span = text[start:end].strip()
    score = BASE_NER_LOCATION
    reasons = ["base:ner-location"]

    if has_context(text, start, end, ADDRESS_CONTEXT, ADDRESS_CONTEXT_WINDOW):
        score += W_ADDRESS_CONTEXT
        reasons.append("address-context")
    if regex_agrees:
        score += W_REGEX_AGREES
        reasons.append("regex-agrees")
    if continues_into_word(text, start, end):
        score -= P_INSIDE_WORD
        reasons.append("-inside-word")
    if span.lower() in ORDINARY_WORDS:
        score -= P_ORDINARY_WORD
        reasons.append("-ordinary-word")

    return max(0.0, min(1.0, score)), reasons


# A Thai given name plus surname tops out well under this. The CRF
# routinely over-extends a PERSON run into the following clause
# (measured: ``พี่ตูนก็โทรมาด้วย`` came back as one 17-char PERSON span),
# and masking a whole clause is the ``swallow`` failure the FIX23 audit
# named. Anything longer must be shrunk to a known name or dropped.
MAX_PERSON_THAI_CHARS = 20


def split_person_span(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Reduce an over-extended PERSON run to the names inside it.

    A short run is returned as-is. A long one is searched for gazetteer
    names and nicknames; each hit becomes its own span. When nothing
    name-like is found the run is dropped entirely — masking a whole
    clause is the ``swallow`` failure the FIX23 audit named, and the
    regex layer still covers the same text.
    """
    # ``trim_run`` cuts a run that STARTS with a known name and then
    # continues into a dictionary word — ``มีชัยจองห้องประชุม`` was
    # masked whole before this, the exact swallow shape FIX23 named.
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        trimmed = gaz.trim_run(text, start, end)
        if start < trimmed < end:
            end = trimmed
    except Exception:  # noqa: BLE001
        pass

    if _thai_len(text[start:end]) <= MAX_PERSON_THAI_CHARS:
        return [(start, end)]

    out: list[tuple[int, int]] = []
    try:
        for s0, e0 in gaz.find_names(text[start:end]):
            out.append((start + s0, start + e0))
    except Exception:  # noqa: BLE001
        pass

    known = nicknames()
    span = text[start:end]
    for nick in known:
        i = span.find(nick)
        while i != -1:
            s0, e0 = start + i, start + i + len(nick)
            if not continues_into_word(text, s0, e0):
                out.append((s0, e0))
            i = span.find(nick, i + 1)

    # Longest span wins at any position; nothing overlapping survives
    # beside it. Without this, ``มิ้นท์`` and the shorter ``มิ้น`` both
    # matched the same characters and minted two placeholders for one
    # person.
    out = sorted(set(out), key=lambda sp: (sp[0], -(sp[1] - sp[0])))
    kept: list[tuple[int, int]] = []
    for s0, e0 in out:
        if any(s0 < ke and ks < e0 for ks, ke in kept):
            continue
        kept.append(_extend_over_surname(text, s0, e0, limit=end))
    return kept


def _extend_over_surname(
    text: str, start: int, end: int, *, limit: int
) -> tuple[int, int]:
    """Pull a following surname into a recovered given-name span.

    When the CRF hands back ``แก้ว สุทธิผล ขอลาคลอด…`` as one PERSON run
    we anchor on the given name, but stopping there leaves the surname in
    the clear. One space plus one Thai word, still inside the run the
    model claimed, is a surname often enough to be worth taking — and the
    run boundary keeps the extension from wandering into the next clause.
    """
    if end >= limit or end >= len(text) or text[end] != " ":
        return start, end
    i = end + 1
    j = i
    while j < min(limit, len(text)) and _is_thai_char(text[j]):
        j += 1
    if j - i < 2:
        return start, end
    return start, j


def trim_leading_title(text: str, start: int, end: int) -> int:
    """Move ``start`` past a title the NER swallowed into the span.

    The CRF tags ``คุณสมชาย ใจดี`` as one PERSON run including ``คุณ``.
    Masking the honorific reads badly (``<<PERSON_A>> โทรมา`` vs
    ``คุณ<<PERSON_A>> โทรมา``) and, more importantly, makes the span
    disagree with the regex detector's span for the same person, which
    is what mints a second placeholder for one human.
    """
    span = text[start:end]
    for p in sorted(TITLE_PREFIXES, key=len, reverse=True):
        if span.startswith(p) and len(span) > len(p):
            return start + len(p)
    return start


def find_ambiguous_names(text: str) -> list[tuple[int, int]]:
    """Gazetteer ``ambiguous`` names sitting at a word boundary.

    Mirrors ``thai_gazetteer.find_names`` but over the list of names that
    are also dictionary words. These never mask on their own — the caller
    scores them and the threshold decides.
    """
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        amb = gaz.names_ambiguous()
    except Exception:  # noqa: BLE001
        return []
    if not amb:
        return []
    out: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if _is_thai_char(text[i]) and (
            gaz.left_boundary(text, i) or bool(title_prefix_before(text, i))
        ):
            hit = None
            for length in range(min(gaz.NAME_MAX, n - i), gaz.NAME_MIN - 1, -1):
                cand = text[i:i + length]
                if cand in amb and gaz.right_edge_ok(text, i + length) and not gaz.continues_into_word(text, i, i + length):
                    hit = length
                    break
            if hit:
                out.append((i, i + hit))
                i += hit
                continue
        i += 1
    return out


def is_word_tail(text: str, start: int, end: int) -> bool:
    """True when the span is the END of a longer dictionary word.

    The right-hand guard (``continues_into_word``) covers ``แก้ว`` inside
    ``แก้วน้ำ``; this is the mirror image, so a name-shaped tail of a
    compound cannot be masked.
    """
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        w = gaz.words()
    except Exception:  # noqa: BLE001
        return False
    for k in range(1, 6):
        if start - k >= 0 and text[start - k:end] in w:
            return True
    return False


def find_gazetteer_names(text: str) -> list[tuple[int, int]]:
    """Unambiguous gazetteer names, without the left-boundary demand.

    ``thai_gazetteer.find_names`` requires a word boundary (or a name
    label) on the left, which in Thai prose is usually absent — the eval
    set's ``ส่งเอกสารให้อรอนงค์ ศรีสุข`` and ``เบอร์ของกมลวรรณคืออะไร``
    both failed on that rule alone. These names are not dictionary words,
    so the right-edge test plus the word-tail test above are enough.
    """
    from app.services.dlp.detectors import thai_gazetteer as gaz

    try:
        known = gaz.names()
    except Exception:  # noqa: BLE001
        return []
    if not known:
        return []
    out: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if _is_thai_char(text[i]):
            hit = None
            for length in range(min(gaz.NAME_MAX, n - i), gaz.NAME_MIN - 1, -1):
                end = i + length
                if (
                    text[i:end] in known
                    and gaz.right_edge_ok(text, end)
                    and not gaz.continues_into_word(text, i, end)
                    and not is_word_tail(text, i, end)
                ):
                    hit = length
                    break
            if hit:
                out.append((i, i + hit))
                i += hit
                continue
        i += 1
    return out
