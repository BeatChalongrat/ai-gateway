"""date_of_birth detector — Thai-month coverage + boundary contract.

Heavy multi-detector live test surfaced that the existing detector
missed Thai-script DOB shapes ("15 มีนาคม 2532", "15 มี.ค. 2532").
The numeric Thai-Buddhist-year form (15/03/2532) already worked
because the regex's date branch is digit-count-based.

This file is the complete regression contract for the post-fix
behavior. Every probe from the diagnose-phase REPL run is here as a
test case, plus two production-leaning negatives the diagnosis
didn't initially cover.

Asymmetric design: English keywords (born/dob/etc.) are unchanged.
Thai keywords gain a negative lookbehind ``(?<![฀-๿])`` so the broad
"เกิด" keyword does NOT match as a substring of Thai compound words
("เหตุเกิด", "ประชุมเกิดวันที่"). Thai has no word separators; the
lookbehind plays the role of \\b in English.
"""

from __future__ import annotations

from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector


# ---------------------------------------------------------------------------
# Positive cases — every shape that MUST fire
# ---------------------------------------------------------------------------


class TestDobPositiveCases:
    def setup_method(self):
        self.det = DateOfBirthDetector()

    # --- Thai full-month forms ----------------------------------------

    def test_thai_full_month_with_kerd_wanthi(self):
        # The exact heavy-scenario shape.
        text = "เกิดวันที่ 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "15 มีนาคม 2532"

    def test_thai_full_month_with_wankerd(self):
        # Alternate Thai keyword for "birthday/date of birth".
        text = "วันเกิด 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_thai_full_month_with_kerd_alone(self):
        # Bare "เกิด" as keyword, separated from prose context — the
        # negative lookbehind allows this case (keyword is at start).
        text = "เกิด 15 ธันวาคม 2510"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_thai_full_month_inside_prose(self):
        # Heavy-scenario realistic context: name + DOB phrase.
        text = "ผมชื่อ นายสมชาย เกิดวันที่ 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "15 มีนาคม 2532"

    # --- Thai abbreviated-month forms ---------------------------------

    def test_thai_abbrev_month_with_dots(self):
        text = "เกิดวันที่ 15 มี.ค. 2532"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "15 มี.ค. 2532"

    def test_thai_abbrev_month_no_dots_two_digit_year(self):
        # Compact form: "15 มีค 32" — common in handwritten DOBs.
        text = "เกิดวันที่ 15 มีค 32"
        matches = self.det.detect(text)
        assert len(matches) == 1

    # --- Numeric forms (Thai-Buddhist year + Gregorian) ---------------

    def test_thai_buddhist_numeric(self):
        text = "เกิดวันที่ 15/03/2532"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "15/03/2532"

    # --- English forms (must remain unchanged after the regex edits) --

    def test_english_born_full_month(self):
        text = "born March 15, 1989"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "March 15, 1989"

    def test_english_dob_iso(self):
        text = "DOB: 1989-05-15"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "1989-05-15"

    def test_english_date_of_birth_numeric(self):
        text = "date of birth 15/05/1990"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_english_birthday_iso(self):
        text = "birthday: 1990-05-15"
        matches = self.det.detect(text)
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# Negative cases — false positives that MUST NOT fire
# ---------------------------------------------------------------------------


class TestDobNegativeCases:
    def setup_method(self):
        self.det = DateOfBirthDetector()

    # --- "เกิด" as substring of compound Thai words -------------------
    # These are the cases the negative lookbehind is specifically
    # designed to reject. Without it, all four would have fired as
    # false positives once Thai months were added to _DATE_ALT.

    def test_kerdhet_incident_occurred(self):
        # "เกิดเหตุ" — "incident occurred". 'เกิด' starts the compound,
        # but "เหตุ" follows; without lookbehind the date afterwards
        # would have matched.
        text = "เกิดเหตุ 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []

    def test_hetkerd_incident_at(self):
        # "เหตุเกิด" — "the incident occurred (at/on)". Here "เกิด" is
        # preceded by another Thai char; the lookbehind kicks in.
        text = "เหตุเกิด 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []

    def test_meeting_took_place_compound(self):
        # "การประชุมเกิดวันที่" — "the meeting took place on (date)".
        # "เกิดวันที่" is the substring but it's part of a longer
        # compound starting with "การประชุม".
        text = "การประชุมเกิดวันที่ 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []

    def test_accident_compound(self):
        # "อุบัติเหตุเกิดขึ้นเมื่อ" — "the accident happened on".
        text = "อุบัติเหตุเกิดขึ้นเมื่อ 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []

    # --- Adjacent shapes that are not dates --------------------------

    def test_version_string(self):
        # "1.2.3" looks like \d.\d.\d but lacks a 4-digit year and no
        # DOB keyword nearby anyway.
        text = "version 1.2.3"
        matches = self.det.detect(text)
        assert matches == []

    def test_bare_page_number(self):
        text = "หน้า 15"
        matches = self.det.detect(text)
        assert matches == []

    def test_chapter_with_thai_month_no_keyword(self):
        # "บทที่ 15 มีนาคม" — chapter heading, no DOB keyword present.
        text = "บทที่ 15 มีนาคม"
        matches = self.det.detect(text)
        assert matches == []

    def test_meeting_date_no_keyword(self):
        # "ประชุม 15 มีนาคม 2532" — meeting date, no DOB keyword.
        text = "ประชุม 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []

    def test_phone_number_isolated(self):
        # "081-234-5678" — phone, no DOB keyword.
        text = "081-234-5678"
        matches = self.det.detect(text)
        assert matches == []

    def test_thirteen_digit_id_isolated(self):
        # 13-digit Thai NID-shaped run, no DOB keyword.
        text = "1101700230708"
        matches = self.det.detect(text)
        assert matches == []

    def test_third_person_month_year(self):
        # "การประชุมเดือนมีนาคม 2532 ที่ผ่านมา" — "the March-2532
        # meeting that just passed". Past-tense month-year reference,
        # no DOB keyword.
        text = "การประชุมเดือนมีนาคม 2532 ที่ผ่านมา"
        matches = self.det.detect(text)
        assert matches == []

    # --- Production-leaning negatives Beat asked to add explicitly ----

    def test_kerd_about_phone_not_dob(self):
        # 'เกิดอะไรขึ้นกับเบอร์ 081-234-5678' — "what happened to the
        # phone (number)". 'เกิด' starts the sentence (lookbehind
        # passes), but the digit shape "081-234-5678" is 3-3-4 which
        # doesn't match any of our date branches. Tests the date
        # branch's anchoring on \d{1,2} for the leading group.
        text = "เกิดอะไรขึ้นกับเบอร์ 081-234-5678"
        matches = self.det.detect(text)
        assert matches == []

    def test_announcement_date_not_dob(self):
        # 'ประกาศวันที่ 15 มีนาคม 2568 บริษัทขอแจ้ง' — corporate
        # announcement. Uses "วันที่" but NOT "วันเกิด" / "เกิดวันที่".
        # No DOB keyword present so the detector must skip.
        text = "ประกาศวันที่ 15 มีนาคม 2568 บริษัทขอแจ้ง"
        matches = self.det.detect(text)
        assert matches == []

    # --- Distance edge: keyword too far from the date ----------------

    def test_keyword_too_far_from_date(self):
        # The detector's pre-date wedge tolerates only ~5 chars of
        # connective whitespace/punctuation. 80 filler chars between
        # keyword and date must reject regardless of date shape.
        text = "เกิด " + "x" * 80 + " 15 มีนาคม 2532"
        matches = self.det.detect(text)
        assert matches == []
