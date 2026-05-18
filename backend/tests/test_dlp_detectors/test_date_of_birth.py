"""Date of birth detector — keyword-gated."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestDateOfBirthHappyPath:
    def test_dob_label_dd_mm_yyyy(self):
        _, span_map, _ = mask("DOB: 15/03/1990")
        assert any("DATE_OF_BIRTH" in t for t in span_map)

    def test_date_of_birth_with_month_name(self):
        _, span_map, _ = mask("Date of birth: March 15, 1990")
        assert any("DATE_OF_BIRTH" in t for t in span_map)

    def test_thai_label_buddhist_era(self):
        # ``วันเกิด`` is the canonical Thai DOB keyword; the regex
        # accepts 4-digit Buddhist-era years up to 4 digits long.
        text = "วันเกิด: 15/03/2533"
        _, span_map, _ = mask(text)
        assert any("DATE_OF_BIRTH" in t for t in span_map)

    def test_thai_label_with_thai_month(self):
        text = "เกิดวันที่ 15 มีนาคม 2533"
        _, span_map, _ = mask(text)
        assert any("DATE_OF_BIRTH" in t for t in span_map)


class TestDateOfBirthBoundary:
    def test_iso_8601(self):
        _, span_map, _ = mask("born on 1990-03-15")
        assert any("DATE_OF_BIRTH" in t for t in span_map)

    def test_birthday_label_dashed(self):
        _, span_map, _ = mask("birthday: 15-03-1990")
        assert any("DATE_OF_BIRTH" in t for t in span_map)


class TestDateOfBirthNegative:
    def test_partial_date_no_year(self):
        assert "date_of_birth" not in detector_names_for("born 15/03")

    def test_year_only(self):
        # Year alone is not a DOB even with a keyword nearby.
        assert "date_of_birth" not in detector_names_for("born 2023")

    def test_no_keyword(self):
        # Bare date without DOB keyword — must stay quiet.
        assert "date_of_birth" not in detector_names_for("15/03/1990")
