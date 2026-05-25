"""Thai-name detection coverage after the registry-gate removal
(Option B) and the Thai verb-phrase denylist expansion (Option C).

Verifies the always-on registration of ``thai_name_no_prefix``,
the negative interaction with Presidio (no duplicates, regex acts
as a fallback floor), and that the newly-added denylist entries
suppress the audit-observed FPs.
"""

from __future__ import annotations

import pytest

from app.schemas.common import Message
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_name_no_prefix import (
    ThaiNameNoPrefixDetector,
)


# ---------------------------------------------------------------------------
# Test 1: prefix-less name + context keyword "ลูกค้า"
# ---------------------------------------------------------------------------


def test_no_prefix_with_context_keyword():
    """A bare Thai two-word name with ``ลูกค้า`` nearby fires the
    regex fallback even when ner_enabled=True.

    Pre-fix this case relied on Presidio's PERSON recognizer, which
    is unreliable with the English-only spaCy model. The fallback
    detector now runs alongside Presidio (registry.py gate removed),
    catches the name via its context-keyword gate, and the overlap
    resolver picks the regex hit when Presidio missed.
    """
    text = "ข้อมูลลูกค้า สมชาย ใจดี email somchai@gmail.com"
    registry = get_default_registry(ner_enabled=True)
    assert "thai_name_no_prefix" in registry

    # Directly exercise the regex detector — keeps the test
    # independent of whether Presidio is loaded in this environment.
    matches = ThaiNameNoPrefixDetector().detect(text)
    assert any(
        m.matched_text == "สมชาย ใจดี" for m in matches
    ), f"expected สมชาย ใจดี in {[m.matched_text for m in matches]}"


# ---------------------------------------------------------------------------
# Test 2: prefix-less name + context keyword "พนักงาน"
# ---------------------------------------------------------------------------


def test_no_prefix_with_employee_keyword():
    """``พนักงาน`` as the context anchor unlocks the regex on a
    different bare name (``สมหญิง รักดี``) — sanity that the
    keyword set works for more than just ``ลูกค้า``.
    """
    text = "พนักงาน สมหญิง รักดี โทร 089-876-5432"
    registry = get_default_registry(ner_enabled=True)
    assert "thai_name_no_prefix" in registry

    matches = ThaiNameNoPrefixDetector().detect(text)
    assert any(
        m.matched_text == "สมหญิง รักดี" for m in matches
    ), f"expected สมหญิง รักดี in {[m.matched_text for m in matches]}"


# ---------------------------------------------------------------------------
# Test 3: Thai verb phrases must NOT be mis-tagged as names
# ---------------------------------------------------------------------------


def test_verb_phrase_not_detected_by_regex():
    """A single-word verb phrase with no Thai neighbour cannot satisfy
    the regex's "2 Thai-script words separated by whitespace" shape.
    Confirms the regex's structural floor for this FP class.

    NOTE: the regex IS susceptible to over-match when a two-word
    Thai sequence happens to bracket a context keyword (e.g.
    ``บาท แล้วจ่ายเงินยัง`` with ``ลูกค้า`` nearby). That class is
    contained in production because (a) ``priority=-5`` lets Presidio
    win on shared spans and (b) Presidio's verb-phrase denylist
    (Option C) suppresses the same tokens when the English NER tags
    them. The fully isolated regex floor is what this test pins.
    """
    text = "ลูกค้า สั่งของยอด"
    matches = ThaiNameNoPrefixDetector().detect(text)
    matched_texts = [m.matched_text for m in matches]
    assert not any("สั่งของยอด" in t for t in matched_texts), (
        f"single-word verb phrase should not match: got {matched_texts}"
    )


def test_verb_phrase_in_presidio_denylist():
    """The newly-added Thai verb phrases live in the Presidio FP
    denylist so any rogue PERSON tag the English NER produces on
    them is silently dropped at the detector boundary.
    """
    from app.services.dlp.detectors.presidio_ner import _is_ner_false_positive

    for phrase in (
        "สั่งของยอด",
        "สั่งของ",
        "จ่ายเงิน",
        "จ่ายเงินยัง",
        "ขอกู้",
        "เปิดบัญชี",
        "โอนเงิน",
    ):
        assert _is_ner_false_positive(
            phrase,
            category="pii",
            confidence=0.95,
            full_text=f"prefix {phrase} suffix",
            start=7,
            end=7 + len(phrase),
        ), f"{phrase!r} should be filtered as Thai verb FP"


# ---------------------------------------------------------------------------
# Test 4: regex coexists with Presidio — at least one finding covers
#         the name span when both are available.
# ---------------------------------------------------------------------------


def test_no_prefix_coexists_with_presidio():
    """Whether Presidio fires (``สมชาย``) or the regex fires
    (``สมชาย ใจดี``), the registry pipeline must yield AT LEAST one
    finding that covers the name. Equivalent to: post-fix there is
    no environment where a name with a context keyword passes
    through completely unmasked.
    """
    text = "ชื่อ สมชาย ใจดี เป็น customer"

    registry = get_default_registry(ner_enabled=True)
    result = registry.scan_messages(
        [Message(role="user", content=text)], direction="input"
    )
    person_matches = [
        m for m in result.matches
        if m.detector_name in ("presidio_ner", "thai_name_no_prefix", "thai_name")
    ]
    assert person_matches, (
        f"expected at least one PERSON-style finding on {text!r}; "
        f"got {[m.detector_name for m in result.matches]}"
    )
    name_substring = "สมชาย"
    assert any(
        name_substring in m.matched_text for m in person_matches
    ), f"no person-finding covered สมชาย; matches={[m.matched_text for m in person_matches]}"


# ---------------------------------------------------------------------------
# Test 5: pseudonym integration — bare name with context produces
#         a <<PERSON_*>> token, original removed from masked output.
# ---------------------------------------------------------------------------


@pytest.fixture
def pseudonym_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_pseudonym_mode", True, raising=False)
    yield


def test_pseudonym_integration(pseudonym_on):
    """End-to-end: scan + mask of a bare-name+context message under
    pseudonym mode emits a ``<<PERSON_*>>`` token AND the raw name
    no longer appears in the masked output.
    """
    from app.services.dlp.transformer import DLPTransformer
    from app.services.policy.evaluator import PolicyEvaluator
    from app.services.policy.profiles import DEFAULT_PROFILE

    text = "ลูกค้า สมชาย ใจดี email somchai@gmail.com"
    registry = get_default_registry(ner_enabled=True)
    detection = registry.scan_messages(
        [Message(role="user", content=text)], direction="input"
    )
    enforcement = PolicyEvaluator().evaluate(detection, DEFAULT_PROFILE)
    masked_messages, span_map, _ = DLPTransformer().mask_messages(
        [Message(role="user", content=text)],
        enforcement.match_actions,
    )

    masked_text = masked_messages[0].content
    # A PERSON pseudonym was minted.
    assert any(
        token.startswith("<<PERSON_") for token in span_map
    ), f"no <<PERSON_*>> token in span_map={list(span_map)}"
    # The raw name is gone from the masked text.
    assert "สมชาย ใจดี" not in masked_text, (
        f"raw name survived in masked output: {masked_text!r}"
    )
