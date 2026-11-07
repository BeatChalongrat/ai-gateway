"""Thai address detector — keyword-anchored.

Anchors on leading address keywords (``ที่อยู่``, ``บ้านเลขที่``,
``อยู่ที่``, …) then sweeps forward until a 5-digit postal code or
end-of-line. Bare ``กรุงเทพ`` city names without the keyword stay
unmatched.
"""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask


class TestThaiAddressHappyPath:
    def test_full_address_with_postal(self):
        text = (
            "ที่อยู่: 99/88 ถนนสุขุมวิท แขวงคลองเตย "
            "กรุงเทพฯ 10110"
        )
        _, _, matches = mask(text)
        assert has_match(matches, "thai_address", "address")

    def test_yu_thi_prefix(self):
        text = "อาศัยอยู่ที่ 99/88 ถนนสุขุมวิท กรุงเทพ 10110"
        _, _, matches = mask(text)
        assert has_match(matches, "thai_address", "address")


class TestThaiAddressBoundary:
    def test_house_no_with_subdistrict(self):
        text = (
            "บ้านเลขที่ 99/88 หมู่ 3 ต.คลองเตย "
            "อ.คลองเตย จ.กรุงเทพฯ 10110"
        )
        _, _, matches = mask(text)
        assert has_match(matches, "thai_address", "address")

    def test_silom_address(self):
        # ``ส่งที่`` is the matching prefix in the keyword list — the
        # compound ``ส่งของมาที่`` would also work because the regex is
        # not anchored, but using the exact keyword makes the
        # detector-vs-test contract explicit.
        text = "ส่งที่ 99 ถนนสีลม แขวงสีลม เขตบางรัก กทม 10500"
        _, _, matches = mask(text)
        assert has_match(matches, "thai_address", "address")


class TestThaiAddressNegative:
    def test_city_name_only(self):
        assert "thai_address" not in detector_names_for("กรุงเทพมหานคร")

    def test_street_name_only(self):
        # No leading address keyword — bare ``ถนนสุขุมวิท`` doesn't
        # trigger.
        assert "thai_address" not in detector_names_for("ถนนสุขุมวิท")


class TestThaiAddressProseNegative:
    """FIX23 — keyword words that are ordinary Thai prose must not fire.

    ``เลขที่`` is also "the number that…", ``อยู่ที่`` is "is at",
    ``ที่อยู่`` is "address" as a noun in a question. Without a house
    number / digit near the keyword there is no address to protect.
    """

    def test_recall_question_is_not_an_address(self):
        # The exact A2 turn from the owner's 33-test script. Before
        # FIX23 the whole string (span 0-26) became <<ADDRESS_1>>.
        text = "เลขที่ให้จำเมื่อกี้คืออะไร"
        assert "thai_address" not in detector_names_for(text)
        masked, span_map, _ = mask(text)
        assert masked == text
        assert span_map == {}

    def test_where_is_it_question(self):
        assert "thai_address" not in detector_names_for("ตอนนี้อยู่ที่ไหน")

    def test_company_address_question(self):
        assert "thai_address" not in detector_names_for(
            "ที่อยู่ของบริษัทคืออะไร"
        )

    def test_answer_quoting_the_remembered_number(self):
        # The model's natural A2 answer: keyword AND digits, but no
        # address component. Observed masked on OUTPUT (2026-08-26 20:34Z).
        for text in (
            "เลขที่ให้จำคือ **74-กระต่าย-2951**",
            "เลขที่ให้จำเมื่อกี้คือ 74-กระต่าย-2951 ใช่ไหม",
        ):
            assert "thai_address" not in detector_names_for(text), text

    def test_number_that_i_gave(self):
        assert "thai_address" not in detector_names_for(
            "เลขที่บอกไปเมื่อวานยังจำได้ไหม"
        )


class TestThaiAddressProseThenNumber:
    def test_registered_address_prose_before_house_number(self):
        # Prose between the keyword and the house number is still an
        # address as long as the digit lands within 30 chars.
        text = "ที่อยู่ตามทะเบียนบ้าน 99/88 หมู่ 3 ต.บางรัก กรุงเทพ 10500"
        _, _, matches = mask(text)
        assert has_match(matches, "thai_address", "address")

    def test_house_number_keyword_still_fires(self):
        _, _, matches = mask("เลขที่ 45/6 ซอยอารีย์ กรุงเทพ 10400")
        assert has_match(matches, "thai_address", "address")
