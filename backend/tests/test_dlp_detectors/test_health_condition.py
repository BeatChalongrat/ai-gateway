"""Health condition detector — PDPA Section 26.

Two entry paths:
1. ``Medical:`` / ``โรค:`` labelled fields with a condition keyword
   inside the value (``diabetes``, ``เบาหวาน`` …).
2. Personal-context-gated keywords (``I was diagnosed with``,
   ``ป่วยเป็น`` …).

The labelled path explicitly stops at ``, <Capitalized>:`` field
boundaries — covered in detail in ``test_dlp_regression`` because that
was a production bug; the boundary smoke test lives here too.
"""

from __future__ import annotations

from ._helpers import has_match, mask


class TestHealthConditionHappyPath:
    def test_labelled_diabetes(self):
        _, _, matches = mask("Medical: diabetes and high blood pressure")
        assert has_match(matches, "health_condition")

    def test_medical_info_label(self):
        # Detector regex label list is ``medical(?:\\s+info)?`` — the
        # ``Medical info:`` form is supported, but ``Medical history``
        # is not (no keyword fallback for the prose noun).
        _, _, matches = mask("Medical info: diabetes")
        assert has_match(matches, "health_condition")

    def test_thai_label(self):
        # ``โรค`` is the labelled-health keyword. The compound
        # ``โรคประจำตัว`` is not in the keyword list, but the
        # personal-context keyword path picks up the condition word
        # itself when ``ผม``/``ฉัน`` etc. is in range.
        text = "ผมป่วยเป็นเบาหวานและความดันโลหิตสูง"
        _, _, matches = mask(text)
        assert has_match(matches, "health_condition")

    def test_thai_diagnosis_with_personal_context(self):
        text = "ผมป่วยเป็นโรคหัวใจมาหลายปี"
        _, _, matches = mask(text)
        assert has_match(matches, "health_condition")


class TestHealthConditionBoundary:
    def test_does_not_swallow_next_field_en(self):
        # The same line carries Medical: + Salary:. The labelled-health
        # regex MUST stop before ``, Salary:`` so the income detector
        # can fire on the second field. Full regression test in
        # test_dlp_regression/test_known_bugs.py.
        text = (
            "Medical: diabetes and high blood pressure, "
            "Salary: 120,000 THB/month"
        )
        _, _, matches = mask(text)
        assert has_match(matches, "health_condition")
        assert has_match(matches, "income")

    def test_newline_separated_fields_thai(self):
        text = "โรค: เบาหวาน, รายได้: 50,000 บาท"
        _, _, matches = mask(text)
        assert has_match(matches, "health_condition")


class TestHealthConditionNegative:
    def test_bare_phrase_no_condition(self):
        # "the following medical conditions" — no specific condition
        # named, no personal context.
        _, _, matches = mask("the following medical conditions")
        assert not has_match(matches, "health_condition")

    def test_department_name_thai(self):
        _, _, matches = mask("แผนกการแพทย์")
        assert not has_match(matches, "health_condition")
