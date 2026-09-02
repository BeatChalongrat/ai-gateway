"""FIX40 — over-masking: the masker must not eat the words around a name.

Two bugs, both pre-dating FIX37/38 and both found only when the corpus grew
a shape it never had (two Thai words separated by a space) and a question
kind it never had (one whose answer sits NEXT TO a masked span):

1. ``thai_name_no_prefix`` Pass 1 read any two space-separated Thai tokens
   as "given + surname" when a context keyword sat anywhere in a 25-char
   window. ``ลูกค้า`` is in nearly every support message, so
   ``12 ใบ แตก 3 ใบ`` became ``12 <<PERSON_B>> 3 ใบ`` and the model answered
   "ordered 3, no information about breakage".
2. ``thai_name`` matched a whole Thai RUN for ``given`` (Thai has no
   spaces) and FIX29's ``trim_run`` guard only ran when the optional
   surname group was absent — so one space later in the sentence skipped
   it and the match swallowed the rest of the line.

The Rust port carries the same two rules (``thai_name.rs`` /
``thai_name_no_prefix.rs``, tests beside them).
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.thai_name import ThaiNameDetector
from app.services.dlp.detectors.thai_name_no_prefix import ThaiNameNoPrefixDetector


def _p1(text: str) -> list[str]:
    return [m.matched_text for m in ThaiNameNoPrefixDetector().detect(text)]


def _titled(text: str) -> list[str]:
    return [m.matched_text for m in ThaiNameDetector().detect(text)]


# ── bug 1: Pass 1 needs an ADJACENT title / name label ────────────────────

@pytest.mark.parametrize(
    "text",
    [
        # the owner's sentence, and the rest of shape s1
        "ลูกค้าสั่งแก้วน้ำ 12 ใบ แตก 3 ใบ สรุปว่าสั่งกี่ใบ",
        "ลูกค้าแจ้งว่าส่งมา 3 กล่อง บุบ 1 กล่อง",
        "ลูกค้ามีเครื่อง 10 เครื่อง ค้าง 4 เครื่อง ที่ศูนย์ซ่อม",
        "ลูกค้าเลี้ยงปลาส่งมา 2 ตัว เสีย 1 ตัว",
        # shape s2 — two ordinary words
        "ทำรายงานให้ลูกค้าหน่อย ยอด ขาย เดือนนี้โตขึ้น",
        "ลูกค้าจองไว้ รอบ บ่าย วันพุธ",
        # shape s3 — words that can be names, used as words
        "ลูกค้าชมว่าพนักงานมี น้ำ ใจ มาก",
        # shape s6 — a business term a space split in two
        "ขอ ใบ กำกับภาษี ให้ลูกค้าด้วย",
        "ส่ง ใบ เสนอราคา ให้ลูกค้าแล้ว",
    ],
)
def test_pass1_does_not_invent_a_name_from_two_ordinary_words(text):
    assert _p1(text) == []


def test_pass1_still_masks_a_labelled_name():
    """The one shape Pass 1 measurably carries on its own: a form label.
    Turning the pass off costs exactly this across both eval sets."""
    assert "สุรชัย มั่นคง" in _p1("ผู้รับ: สุรชัย มั่นคง")
    # Pass 4 (the 10k lexicon) also reports the bare given name — both spans
    # come from this detector and the overlap resolver keeps one.
    assert "ณัฐพล วงศ์ใหญ่" in _p1("ชื่อ: ณัฐพล วงศ์ใหญ่")


def test_pass1_anchor_must_be_adjacent_not_merely_nearby():
    """``ลูกค้า`` 20 characters away used to be enough."""
    assert _p1("ลูกค้าบอกมาเมื่อเช้าว่า ยอด รวม ยังไม่ตรง") == []


# ── bug 2: the titled detector stops at the name ─────────────────────────

def test_a_name_glued_to_prose_stops_at_the_name():
    assert _titled("คุณแก้วโทรมาบอกว่าแก้วน้ำที่สั่งไปแตกสองใบ ขอให้ส่งชุดใหม่ไปแทน") == ["คุณแก้ว"]


def test_a_clause_after_the_space_is_not_a_surname():
    assert _titled("คุณพิมพ์ใจ ลาป่วยสามวัน ตั้งแต่วันจันทร์ถึงวันพุธ") == ["คุณพิมพ์ใจ"]
    assert _titled("คุณสมศักดิ์ ใจงาม จากคลังแจ้งว่าถุงมือเข้ามา 100 คู่") == ["คุณสมศักดิ์ ใจงาม"]


def test_a_real_surname_is_still_masked():
    assert _titled("ลูกค้าชื่อคุณสมชาย ใจดี โทรมาบ่นว่าของยังไม่ถึง") == ["คุณสมชาย ใจดี"]
    assert _titled("คุณประเสริฐ ทองดี แจ้งว่ากระเบื้อง 40 แผ่น บิ่น 6 แผ่น") == ["คุณประเสริฐ ทองดี"]


def test_a_bare_title_and_given_name_is_still_masked():
    assert _titled("คุณสมชาย โทรมา") == ["คุณสมชาย"]


def test_the_surname_cap_is_what_separates_a_word_from_a_clause():
    from app.services.dlp.detectors import thai_name as tn

    assert tn.MAX_SURNAME_THAI_CHARS == 10
    # every real surname in the eval sets is well under the cap
    for sur in ("ใจดี", "ทองดี", "ศรีสุข", "มั่นคง", "วงศ์ใหญ่", "ตั้งใจ", "ใจงาม", "ศรีทอง"):
        assert tn._thai_len(sur) <= tn.MAX_SURNAME_THAI_CHARS
    # the prose runs that used to ride along are over it
    assert tn._thai_len("ลาป่วยสามวัน") > tn.MAX_SURNAME_THAI_CHARS


# ── the end-to-end shape the owner reported ──────────────────────────────

def test_the_owners_sentence_keeps_its_quantities():
    from evals.mask_quality import harness as H

    text = "สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ สรุปว่าลูกค้าสั่งกี่ใบ แตกกี่ใบ"
    masked = H.Masker(ner_enabled=False).mask(text).text
    assert "12 ใบ" in masked
    assert "แตก 3 ใบ" in masked
    assert "PERSON" not in masked


def test_a_name_beside_the_quantities_is_still_masked():
    from evals.mask_quality import harness as H

    text = "คุณประเสริฐ ทองดี แจ้งว่ากระเบื้อง 40 แผ่น บิ่น 6 แผ่น"
    masked = H.Masker(ner_enabled=False).mask(text).text
    assert "ประเสริฐ" not in masked
    assert "40 แผ่น" in masked and "บิ่น 6 แผ่น" in masked
