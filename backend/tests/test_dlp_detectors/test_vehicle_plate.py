"""Thai vehicle plate detector — keyword OR province context."""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask


class TestVehiclePlateHappyPath:
    def test_thai_label_keyword(self):
        text = "ทะเบียนรถ กข 1234 กรุงเทพมหานคร"
        _, _, matches = mask(text)
        assert has_match(matches, "vehicle_plate")

    def test_thai_dashed_plate(self):
        text = "รถยนต์ทะเบียน กข-1234 กทม."
        _, _, matches = mask(text)
        assert has_match(matches, "vehicle_plate")

    def test_english_license_plate_with_thai_chars(self):
        # license-plate keyword + Thai-char plate body.
        text = "License plate กข 1234 Bangkok"
        _, _, matches = mask(text)
        assert has_match(matches, "vehicle_plate")


class TestVehiclePlateBoundary:
    def test_keyword_with_plate(self):
        text = "ป้ายทะเบียน กข1234"
        _, _, matches = mask(text)
        assert has_match(matches, "vehicle_plate")


class TestVehiclePlateNegative:
    def test_city_alone(self):
        # No plate body in front of the province name.
        assert "vehicle_plate" not in detector_names_for("กรุงเทพมหานคร")

    def test_non_thai_format_no_keyword(self):
        # Western plate without Thai keyword / province.
        assert "vehicle_plate" not in detector_names_for("ABC-1234")
