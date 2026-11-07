"""An address is an address whatever was typed before it.

Found on 8 Oct 2026 by asking the engine about lead-ins one at a time: ``ส่งที่`` was the only way of saying
"send it to" the address detector heard, and a house number was an address only after one of a handful of
words. The same office address after ``ส่งเอกสารไปที่`` / ``จัดส่งไปที่`` / ``ส่งของมาที่`` / ``นัดรับที่`` was not
found; a complete postal address after ``นัดรับที่`` / ``มารับที่`` — or on a line of its own — was not found
either. (Where name recognition runs it masks the street and the city; the house number, the district and
the postcode still went out, and where it does not run, everything did.)

Held here:

  * a delivery or pick-up lead-in said with an object or a direction opens an address under the same rule
    ``ส่งที่`` always had (a number near, and an address part);
  * a house number followed by a street-level part and a second part is an address with nothing before it;
  * for both, what is masked begins at the PLACE — the words before it ("ส่งเอกสารไปที่") are what the
    person asked, and stay;
  * what a person types after the address is still not part of it;
  * and the sentences that must never be an address still are not one.

Asked of the detector directly AND through the registry's scan (the path a chat takes: it normalises the
text first — see test_thai_address_tail.py for why both).
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_address import ThaiAddressDetector

OFFICE = "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
FULL = "99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500"
TAIL = " ภายในวันศุกร์นะครับ"


def _direct(text: str) -> list[str]:
    return [text[m.start:m.end] for m in ThaiAddressDetector().detect(text)]


def _scanned(text: str) -> list[int]:
    """Lengths of what the registry's scan masks as an address (its text is normalised, so lengths, not strings)."""
    return [m.end - m.start for m in get_default_registry().scan_text(text) if m.detector_name == "thai_address"]


DELIVERY = ["ส่งเอกสารไปที่", "จัดส่งไปที่", "ส่งของมาที่", "นัดรับที่", "ส่งไปที่", "ส่งมาที่", "จัดส่งสินค้ามาที่", "ส่งถึง",
            "ส่งให้ที่", "นัดรับของที่", "มารับที่", "รับของที่", "รับสินค้าที่"]


@pytest.mark.parametrize("lead", DELIVERY)
def test_a_delivery_lead_in_opens_an_office_address_and_stays_in_front_of_it(lead):
    text = f"ขอบคุณครับ {lead}{OFFICE}{TAIL}"
    assert _direct(text) == [OFFICE], "the place is masked; the lead-in and what follows are not"
    assert len(_scanned(text)) == 1


@pytest.mark.parametrize("lead", DELIVERY + ["เจอกันที่", "พักที่", "ทำงานที่", "บ้าน", ""])
def test_a_whole_address_is_found_whatever_comes_before_it(lead):
    text = f"ขอบคุณครับ {lead}{' ' if lead in ('บ้าน',) else ''}{FULL}{TAIL}"
    assert _direct(text) == [FULL]
    assert _scanned(text) == [len(FULL)]


def test_a_whole_address_on_a_line_of_its_own():
    assert _direct(FULL) == [FULL]
    assert _direct(f"ที่ทำงานใหม่\n{FULL}\nเริ่มวันจันทร์") == [FULL]
    assert _direct("12 ตำบลบางรัก อำเภอเมือง ลำปาง ขอบคุณครับ") == ["12 ตำบลบางรัก อำเภอเมือง ลำปาง"]
    assert _direct("88 หมู่ 3 ตำบลบางพลีใหญ่ อำเภอบางพลี สมุทรปราการ 10540 ส่งได้เลย") == ["88 หมู่ 3 ตำบลบางพลีใหญ่ อำเภอบางพลี สมุทรปราการ 10540"]


def test_what_matched_before_matches_as_it_did():
    """The keywords are tried first at every position: their spans — keyword included — do not move."""
    for text, want in (
        (f"ส่งที่{OFFICE}{TAIL}", f"ส่งที่{OFFICE}"),
        (f"จัดส่งที่{OFFICE}{TAIL}", f"ส่งที่{OFFICE}"),
        (f"ที่อยู่ {FULL}{TAIL}", f"ที่อยู่ {FULL}"),
        ("ไปที่ 23 ตึกบี ห้อง 402", "ไปที่ 23 ตึกบี ห้อง 402"),
        ("เขาอยู่ 88/4 ถนนสุขุมวิท กรุงเทพ", "อยู่ 88/4 ถนนสุขุมวิท กรุงเทพ"),
    ):
        assert _direct(text) == [want], text


PROSE = [
    # the lead-ins' everyday selves
    "ส่งเอกสารไปที่ฝ่ายบัญชีภายใน 5 วันทำการนะครับ",
    "ส่งงานไปที่หัวหน้าแล้ว 2 รอบ ยังไม่ตอบ",
    "นัดรับที่ร้านกาแฟ 3 โมงเย็น",
    "มารับที่หน้าบริษัทได้เลย รอ 10 นาที",
    "ได้รับที่ปรึกษาใหม่ 2 คนในไตรมาสนี้",
    "ส่งเสริมการขายที่สาขา 5 แห่งทั่วประเทศ",
    "ส่งถึงมือลูกค้าภายใน 3 วัน",
    # a number, then a street word, and nothing more of an address
    "เดินไปอีก 3 ซอย ก็ถึงแล้ว",
    "ร้านนี้มี 2 ถนนให้เลือกเข้า",
    "ครั้งที่ 3 ถนนคนเดินเปิดถึงเที่ยงคืน",
    "ราคา 1200 บาท ต่อ 3 ชั้น",
    # the old decoys
    "ไปที่หน้า 3 แล้วกดปุ่มสีเขียว",
    "นัดที่ห้องประชุมชั้น 2 ตอนบ่าย",
    "ประชุมที่ห้อง 402 ชั้น 5",
    "ไปที่ 3 โมงเย็นพอดีนะ อย่าสาย",
    "เลขที่ให้จำเมื่อกี้คืออะไร",
    "โทร 02 123 4567 ต่อ 12",
    "ออเดอร์ 12 ชิ้น ส่งวันที่ 5 ตุลาคม",
]


@pytest.mark.parametrize("text", PROSE)
def test_prose_is_not_an_address(text):
    assert _direct(text) == []
    assert _scanned(text) == []


def test_a_number_that_does_not_start_a_token_is_not_a_house_number():
    for text in ("รหัส A12 ถนนสีลม แขวงสีลม", "ยอด 1,250 ถนนสีลม แขวงสีลม", "เวลา 10:30 ถนนสีลม แขวงสีลม", "โทร 02-99 ถนนสีลม แขวงสีลม"):
        got = _direct(text)
        assert all(not g.startswith(("12 ", "250 ", "30 ", "99 ")) for g in got), (text, got)
    # a Thai word glued in front is not that: Thai writes no space there
    assert _direct("พักที่99/9 ถนนสีลม แขวงสีลม ครับ") == ["99/9 ถนนสีลม แขวงสีลม"]
