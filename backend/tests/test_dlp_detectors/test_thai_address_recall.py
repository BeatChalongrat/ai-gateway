"""FIX23b §2 — recall probes for the Thai address detector.

Every input here is a real-shaped (synthetic) Thai address that a DLP
product would want masked. The FIX23 precision anchors (house-number
digit within 30 chars of the keyword AND an address component on the
line) deliberately stop the detector matching some of them.

Cases that currently ESCAPE are marked ``xfail(strict=True)`` with the
gap they represent: they document an accepted false negative awaiting
the owner's decision (FIX23b §2). If one starts passing, strict xfail
fails the suite so the marker gets removed consciously — the intent
never drifts silently in either direction.
"""

from __future__ import annotations

import pytest

from ._helpers import detector_names_for

GAP = "FIX23b §2 accepted gap (owner decision pending): "

CASES = [
    pytest.param("ที่อยู่ 99/88 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110", id="classic"),
    pytest.param("บ้านเลขที่ 12 ถนนพระราม 4", id="road_only"),
    pytest.param("บ้านเลขที่ 45/6 หมู่บ้านสัมมากร", id="village_name"),
    pytest.param("ที่อยู่ 5 ซ.ลาดพร้าว 101", id="soi_abbrev"),
    pytest.param("บ้านเลขที่ ๙๙/๘๘ ถนนสีลม", id="thai_digits"),
    pytest.param("ส่งที่ 88 มัณฑนา 10540", id="postcode_only"),
    pytest.param("ที่อยู่ 45 ม.7 ต.บางพลี", id="abbrev_m_t"),
    pytest.param("เลขที่ 7 ซอยสุขุมวิท 24", id="soi_word"),
    pytest.param("ที่อยู่ 199/22 คอนโดลุมพินี 10120", id="condo_with_postcode"),
    pytest.param("บ้านเลขที่ 12/3 แขวงบางนา", id="district_only"),
    pytest.param("บ้านเลขที่ 3 หมู่ 5 ตำบลบางรัก", id="moo_tambon"),
    # FIX26 §1.2 — recovered by the keyword-precision split (HIGH keyword ที่อยู่).
    pytest.param("ที่อยู่ 199/22 คอนโดลุมพินี", id="condo_only"),
    pytest.param("ส่งที่ 12 อาคารเอ ชั้น 5", id="building_floor"),  # FIX46: gate knows อาคาร/ชั้น
    pytest.param("อยู่ที่ 77/1 บางใหญ่ นนทบุรี", id="province_bare",
                 marks=pytest.mark.xfail(strict=True, reason=GAP + "bare amphoe/province names")),
    pytest.param("ที่อยู่ 199/22 Lumpini Condo", id="english_building"),  # FIX26: recovered (HIGH keyword)
    pytest.param("ที่อยู่สำหรับจัดส่งสินค้าของลูกค้ารายนี้คือ 99/88 ถนนสุขุมวิท 10110", id="long_prose_gap",
                 marks=pytest.mark.xfail(strict=True, reason=GAP + ">30-char prose gap before house number")),
    pytest.param("ส่งที่ 23 ตึกบี ห้อง 402", id="building_room"),  # FIX46: gate knows ตึก/ห้อง
    pytest.param("อยู่ที่ 55 นิคมอุตสาหกรรมอมตะ ชลบุรี", id="industrial_estate"),  # FIX46: gate knows นิคม
    pytest.param("ที่อยู่ ถนนสุขุมวิท กรุงเทพ", id="no_house_number",
                 marks=pytest.mark.xfail(strict=True, reason=GAP + "no house number")),
    pytest.param("ส่งที่ คอนโดไอดีโอ ตึก A ห้อง 1205", id="room_tower_no_house_no"),  # FIX46: gate knows คอนโด (room no. is the digit)
    pytest.param("อยู่ที่ 9/1 อพาร์ทเม้นท์สุขใจ", id="apartment"),  # FIX46: gate knows อพาร์ทเม้นท์
]


@pytest.mark.parametrize("text", CASES)
def test_real_address_is_still_masked(text):
    assert "thai_address" in detector_names_for(text), text
