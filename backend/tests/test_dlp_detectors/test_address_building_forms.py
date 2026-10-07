"""FIX46 — the address that never got masked (#6) and the tail that leaked (#5).

Every sentence here is synthetic. The Rust sidecar carries the same cases
in ``thai_address.rs::end_trim_tests``; ``make dlp-eval`` and
``evals.dlp.probe_address`` are the per-category / per-sentence parity
checks between the two engines.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.dlp.detectors import thai_address as TA

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

detector = TA.ThaiAddressDetector()


def spans(text: str) -> list[str]:
    return [m.matched_text for m in detector.detect(text)]


# ── Part B — one list, two consumers ────────────────────────────────────────


def test_the_gate_and_the_trim_read_the_same_component_list():
    # The gate regex is BUILT from the component list; the trim list is the
    # component list plus the locality-only words. Neither is typed by hand
    # any more, so they cannot drift apart again.
    # (2026-10-08: the gate takes a word in either spelling of its sara am — as typed, and as the scan's
    # NFKC pass leaves it — so "built from the list" is asked of the regex itself, not of its source text.)
    import re

    gate = re.compile(TA._COMPONENT_ALT)
    for w in TA._GATE_COMPONENTS:
        assert gate.fullmatch(w), w
        assert gate.fullmatch(w.replace("ำ", "ํา")), f"{w}: the spelling a scan sees"
        assert w in TA._COMPONENT_STARTS
    for w in ("ตึก", "อาคาร", "ห้อง", "ชั้น", "คอนโด", "หมู่บ้าน", "อพาร์ทเม้นท์", "หอพัก", "นิคม"):
        assert w in TA._GATE_COMPONENTS, f"{w} must be in the gate (it was only in the trim before FIX46)"


@pytest.mark.parametrize("fname", [
    "thai_address_components.txt", "thai_address_trim_words.txt",
    "thai_address_clause_starts.txt", "thai_address_localities.txt",
])
def test_word_lists_are_identical_in_both_engines(fname):
    py = BACKEND / "app" / "services" / "dlp" / "data" / fname
    rs = REPO / "dlp-sidecar" / "data" / fname
    if not rs.exists():
        pytest.skip("dlp-sidecar not checked out beside backend/")
    assert hashlib.sha256(py.read_bytes()).hexdigest() == hashlib.sha256(rs.read_bytes()).hexdigest(), f"{fname} differs between the engines"


# ── #6 — building / room addresses, lead-in words ───────────────────────────


@pytest.mark.parametrize("text, expect", [
    ("ไปที่ 23 ตึกบี ห้อง 402", "ไปที่ 23 ตึกบี ห้อง 402"),
    ("อยู่ที่ 23 ตึกบี ห้อง 402", "อยู่ที่ 23 ตึกบี ห้อง 402"),
    ("ส่งไปที่ 199/22 คอนโดลุมพินี ชั้น 8 ห้อง 812", "ไปที่ 199/22 คอนโดลุมพินี ชั้น 8 ห้อง 812"),
    ("มาที่ 101 อาคารซีพี ทาวเวอร์ ชั้น 20 ตอนบ่ายนะ", "มาที่ 101 อาคารซีพี ทาวเวอร์ ชั้น 20"),
    ("นัดที่ 99/1 หมู่ 5 ต.บางพลี ตอนบ่าย", "นัดที่ 99/1 หมู่ 5 ต.บางพลี"),
    ("เขาอยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130 เขาบอกว่าจะร้องเรียน", "อยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130"),
])
def test_building_addresses_and_lead_ins_are_masked(text, expect):
    assert spans(text) == [expect]


@pytest.mark.parametrize("text", [
    "ไปที่หน้า 3 แล้วกดปุ่มสีเขียว",          # a word between the lead-in and the number
    "ให้ไปที่ขั้นตอนที่ 2 ก่อน",
    "ไปที่ 3 โมงเย็นพอดีนะ อย่าสาย",          # number, but no address part after it
    "นัดที่ห้องประชุมชั้น 2 ตอนบ่าย 2",         # the decided borderline: a meeting room, not an address
    "ประชุมที่ห้อง 402 ชั้น 5 ของอาคารสำนักงานใหญ่ตอนบ่ายสองนะ",
    "ราคาอยู่ที่ 1200 ต่อชั้น 2 ชิ้นได้ส่วนลด",   # ชั้น glued after a Thai letter is prose
    "อยู่ 3 ปีแล้วยังไม่เคยไปทะเล",
    "รถติดที่ถนนนี้ 3 ชั่วโมงทุกเย็น",           # one street word, no second part
    "เลขที่ให้จำเมื่อกี้คืออะไร",
])
def test_lead_ins_without_the_address_shape_stay_prose(text):
    assert spans(text) == []


# ── #5 — the tail that leaked ───────────────────────────────────────────────


def test_khon_kaen_is_not_a_request():
    # ``ขอ`` (request) is a clause starter; ``ขอนแก่น`` starts with it.
    assert spans("ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000 ช่วยร่างข้อความทวงเอกสาร") == [
        "ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000"
    ]


def test_hat_yai_is_not_a_search():
    assert spans("ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110 ขอให้ส่งด่วน") == [
        "ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110"
    ]


def test_province_then_postcode_is_one_unit():
    # a province NOT in the locality list still rides on the postcode
    assert spans("อยู่ที่ 77/1 หมู่ 3 บางใหญ่ เมืองสมมติ 11140 พรุ่งนี้ส่งของ") == [
        "อยู่ที่ 77/1 หมู่ 3 บางใหญ่ เมืองสมมติ 11140"
    ]


def test_a_glued_clause_after_the_address_is_not_two_bare_tokens():
    # FIX38's "two bare tokens" allowance let one 40-character glued clause
    # ride along as a single token — the sf03 decoys (ขอพร / ปิดดีล) were
    # eaten. Length and clause-inside rules end the address at the address.
    text = "ไปวัดหน้างานที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ อาทิตย์หน้า เขาบอกว่าขอพรให้ปิดดีลได้ก่อนสิ้นเดือนด้วย"
    assert spans(text) == ["ที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ"]


def test_time_words_end_an_address():
    assert spans("นัดที่ 99/1 หมู่ 5 ต.บางพลี ตอนบ่าย 2 โมง") == ["นัดที่ 99/1 หมู่ 5 ต.บางพลี"]


def test_short_verbs_still_end_an_address_on_short_tokens():
    # ``ขอให้`` is 5 chars — the two-character prefix still applies there.
    assert spans("ที่อยู่ 9 หมู่ 3 ต.บางพลี ขอให้ส่งด่วน") == ["ที่อยู่ 9 หมู่ 3 ต.บางพลี"]
