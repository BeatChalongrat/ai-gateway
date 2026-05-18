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
