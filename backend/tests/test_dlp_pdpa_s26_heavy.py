"""PDPA Section 26 detector behavior — heavy-scenario regression coverage.

Re-diagnosing the heavy multi-detector live test surfaced that the live
"miss" on religion_belief / health_condition / criminal_record was an
artifact of MSYS Thai-encoding corruption (docs/known-issues #8) rather
than a detector gap. With proper UTF-8 input these detectors fire as
designed. The cases below lock that contract:

* Positive — heavy-scenario forms where a Thai personal pronoun
  ("ผม") appears alongside the keyword. These DO fire today; tests
  guard against future regressions.

* Negation — "ผมไม่มีประวัติอาชญากรรม" still fires. The detector
  treats "I don't have a criminal record" as PII because the
  *status* is itself sensitive (PDPA Section 26 covers criminal-
  record data regardless of polarity). Test locks that.

* Isolation gaps — a bare keyword without any personal pronoun in
  the 150-char window does NOT fire (e.g. "เบาหวาน" alone). This is
  intentional precision-over-recall design: it keeps detectors quiet
  on news articles, medical encyclopedias, and other third-person
  prose. The xfail tests below document the design choice — flip
  to passing if/when a future refactor decides to broaden the gate.
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.criminal_record import CriminalRecordDetector
from app.services.dlp.detectors.health_condition import HealthConditionDetector
from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector


# ---------------------------------------------------------------------------
# Religion / belief
# ---------------------------------------------------------------------------


class TestReligionHeavyForm:
    def setup_method(self):
        self.det = ReligionBeliefDetector()

    def test_fires_on_thai_pronoun_phrase(self):
        # Heavy-scenario bullet form: pronoun + keyword on the same line.
        text = "ผมนับถือศาสนาพุทธ"
        matches = self.det.detect(text)
        assert len(matches) >= 1
        assert any("นับถือ" in m.matched_text for m in matches)

    def test_fires_with_dash_bullet_prefix(self):
        # Bullet form as it appears in the heavy scenario user message.
        text = "- ผมนับถือศาสนาพุทธ"
        matches = self.det.detect(text)
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# Health condition
# ---------------------------------------------------------------------------


class TestHealthHeavyForm:
    def setup_method(self):
        self.det = HealthConditionDetector()

    def test_fires_on_disease_with_pronoun(self):
        # Pronoun on same line as the disease keyword.
        text = "ผมมีโรคประจำตัวคือเบาหวานชนิดที่ 2"
        matches = self.det.detect(text)
        assert len(matches) >= 1
        # The detector emits multiple matches for compound phrasings
        # ("ผมมีโรค" + "เบาหวาน") — assert one of them carries the
        # disease name.
        assert any("เบาหวาน" in m.matched_text for m in matches)

    def test_fires_on_full_heavy_message(self):
        # Models a realistic heavy-scenario layout: pronoun in the
        # opening sentence, disease keyword in a later bullet. The
        # 150-char window must reach back to the pronoun.
        text = (
            "สวัสดีครับ ผมชื่อสมชาย ใจดี ขอแจ้งข้อมูลประกอบเรื่องสินไหม:\n"
            "- มีโรคประจำตัวคือเบาหวานชนิดที่ 2"
        )
        matches = self.det.detect(text)
        assert len(matches) >= 1, (
            "expected at least one finding when pronoun precedes the "
            "disease keyword within 150 chars"
        )


# ---------------------------------------------------------------------------
# Criminal record
# ---------------------------------------------------------------------------


class TestCriminalHeavyForm:
    def setup_method(self):
        self.det = CriminalRecordDetector()

    def test_fires_on_record_phrase_with_pronoun(self):
        text = "ผมเคยมีประวัติอาชญากรรม"
        matches = self.det.detect(text)
        assert len(matches) >= 1
        assert any("ประวัติอาชญากรรม" in m.matched_text for m in matches)

    def test_negation_with_pronoun_still_fires(self):
        # PDPA Section 26 covers criminal-record data regardless of
        # polarity — "I have no record" is still PII because confirming
        # the *status* is sensitive. Lock this in.
        text = "ผมไม่มีประวัติอาชญากรรม"
        matches = self.det.detect(text)
        assert len(matches) >= 1, (
            "negation must NOT exempt criminal_record from PDPA-S26 "
            "coverage when a personal pronoun is present"
        )


# ---------------------------------------------------------------------------
# Documented isolation gaps (xfail — design choice, not bugs)
# ---------------------------------------------------------------------------


class TestPdpaS26IsolationGapsAreIntentional:
    """The personal-pronoun gate is precision-over-recall by design.

    A bare keyword with no pronoun in the 150-char window does not
    fire. This keeps detectors quiet on:
      - news articles ("the patient was diagnosed with diabetes")
      - encyclopedia-style prose ("เบาหวานเป็นโรคที่...")
      - third-person legal summaries ("ประวัติอาชญากรรมตามกฎหมาย...")

    The xfail tests below document that today's behavior IS the
    intended behavior. If a future PR broadens the gate, the xfails
    flip to XPASS and surface naturally — at which point either the
    PR or these tests need to be updated. No xfail(strict=True) here:
    we don't want a green-build today to start failing the moment
    the design changes.
    """

    @pytest.mark.xfail(
        reason=(
            "Design: bare 'เบาหวาน' without personal pronoun in 150-char "
            "window does NOT fire. Avoids medical-encyclopedia false "
            "positives. Flip if the gate is intentionally broadened."
        ),
    )
    def test_bare_disease_word_isolation(self):
        det = HealthConditionDetector()
        matches = det.detect("เบาหวาน")
        assert len(matches) >= 1

    @pytest.mark.xfail(
        reason=(
            "Design: bare 'ประวัติอาชญากรรม' without pronoun does NOT "
            "fire. Avoids news/legal-summary false positives. Flip if "
            "the gate is intentionally broadened."
        ),
    )
    def test_bare_criminal_phrase_isolation(self):
        det = CriminalRecordDetector()
        matches = det.detect("ประวัติอาชญากรรม")
        assert len(matches) >= 1

    @pytest.mark.xfail(
        reason=(
            "Design: bullet line '- ไม่มีประวัติอาชญากรรม' lacks a "
            "personal pronoun on the line. Live heavy scenario hits this "
            "via cross-bullet context (pronoun in an earlier sentence "
            "within the 150-char window) — but the same line in pure "
            "isolation is intentionally skipped."
        ),
    )
    def test_negation_bullet_in_isolation(self):
        det = CriminalRecordDetector()
        matches = det.detect("- ไม่มีประวัติอาชญากรรม")
        assert len(matches) >= 1
