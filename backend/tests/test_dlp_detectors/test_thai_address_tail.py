"""The address is the match — never the rest of the line.

Found on a walk of the live product on 2026-10-07 and true of every chat on the platform: a person typed a
delivery address and went on typing, and everything after the address was masked with it —

    ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด

reached the model as ``… มีที่วางแขน <<ADDRESS_1>>`` (request_logs.dlp_findings_json: detector
thai_address, 62 characters). The request was the user's input and it disappeared silently.

Two things made it so. The end-trim kept nothing when the first token after the keyword was a place's
name longer than 12 characters — and ``สำนักงานใหญ่`` is 12 as typed, 13 after the scan's NFKC pass
splits its sara am — and a trim that kept nothing fell back to the whole line. So every case here goes
through ``registry.scan_text``, the path a chat takes; the detector called directly on the typed text
never showed the bug.

Both directions are held: what follows the address survives, and the address itself is still masked
whole (its parts are not to leak for the sake of the tail).
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_address import ThaiAddressDetector, trim_address_end

ADDRESS = "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"

# (the whole message, what must be inside the mask, what must be left for the model to read)
CASES = [
    pytest.param("ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน " + ADDRESS + " ขอให้เรียงจากถูกสุด",
                 ADDRESS, "ขอให้เรียงจากถูกสุด", id="the_request_typed_after_the_address"),
    pytest.param("ขอซื้อเก้าอี้สำนักงาน 20 ตัว " + ADDRESS + " งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด",
                 ADDRESS, "งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด", id="a_budget_and_two_requests_after_it"),
    pytest.param("ส่งที่อาคารสาทรธานี ชั้น 12 ถนนสาทรเหนือ กรุงเทพฯ ภายในวันศุกร์ ขอใบเสนอราคาด้วย",
                 "ส่งที่อาคารสาทรธานี ชั้น 12 ถนนสาทรเหนือ กรุงเทพฯ", "ภายในวันศุกร์ ขอใบเสนอราคาด้วย", id="a_deadline_after_it"),
    pytest.param("ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ ช่วยตรวจว่าส่งได้ไหม",
                 "ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ", "ช่วยตรวจว่าส่งได้ไหม", id="a_long_word_before_the_house_number"),
    pytest.param("ส่งที่ 99/1 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด",
                 "ส่งที่ 99/1 ถนนสีลม กรุงเทพฯ", "ขอให้เรียงจากถูกสุด", id="house_number_first"),
    pytest.param("ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ",
                 "ที่อยู่ 55/12 ซอย 5 บางนา", "สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ", id="the_owners_sentence_of_fix38"),
]


def _addresses(text: str):
    return [m for m in get_default_registry().scan_text(text, language="th") if m.detector_name == "thai_address"]


@pytest.mark.parametrize("text, masked, left", CASES)
def test_what_a_person_types_after_an_address_reaches_the_model(text: str, masked: str, left: str):
    found = _addresses(text)
    assert len(found) == 1, [text[m.start:m.end] for m in found]
    m = found[0]
    assert text[m.start:m.end] == masked, f"masked {text[m.start:m.end]!r}"
    assert text[m.end:].strip() == left, f"left for the model: {text[m.end:].strip()!r}"


@pytest.mark.parametrize("text, masked, left", CASES)
def test_the_same_through_the_wrapper_a_prompt_puts_round_the_words(text: str, masked: str, left: str):
    """A prompt puts the person's words on a line of their own between markers; the line ends where they end."""
    wrapped = "ข้อความของผู้ใช้:\n<<<\n" + text + "\n>>>\n\nรูปแบบคำตอบ: …"
    found = _addresses(wrapped)
    assert [wrapped[m.start:m.end] for m in found] == [masked]
    assert left in wrapped[found[0].end:]


def test_the_address_alone_is_still_masked_whole():
    for text in (ADDRESS, "กรุณา" + ADDRESS, ADDRESS + "\nขอบคุณครับ"):
        found = _addresses(text)
        assert [text[m.start:m.end] for m in found] == [ADDRESS], text


def test_a_walk_that_keeps_nothing_is_not_an_address_to_the_end_of_the_line():
    """The fallback that was removed: with nothing kept, the old trim returned the end of the body."""
    body = "เรื่องที่เขาพูดถึงกันเมื่อวานนี้ในที่ประชุมใหญ่ของบริษัท"  # one long token, no address part in it
    text = "ที่อยู่" + " " * 40 + body  # (past the gate's reach, so the long token is not a place's name)
    start = len("ที่อยู่")
    assert trim_address_end(text, start, len(text)) == start
    assert ThaiAddressDetector().detect(text) == []


def test_tambon_and_amphoe_are_address_parts_in_the_spelling_a_scan_sees():
    """Before: after the scan's NFKC pass ``ตำบล`` / ``อำเภอ`` / ``ลำปาง`` matched nothing in the word lists, so an
    address that had only those for its parts was cut short or not seen at all."""
    text = "ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง ขอบคุณครับ"
    found = _addresses(text)
    assert [text[m.start:m.end] for m in found] == ["ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง"]


def test_sara_am_is_not_what_decides_where_an_address_ends():
    """The same sentence as typed and as the scan sees it (NFKC splits ำ in two) ends at the same word."""
    import unicodedata

    text = "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด"
    for t in (text, unicodedata.normalize("NFKC", text)):
        found = ThaiAddressDetector().detect(t)
        assert len(found) == 1 and t[found[0].end:].strip() == "ขอให้เรียงจากถูกสุด", t[found[0].start:found[0].end]
