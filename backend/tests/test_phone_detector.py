"""Phone detector tests — covers Fix 2 (context gate on bare digits).

Before Fix 2, ``\\b\\d{10,15}\\b`` matched any 10-15 digit run with no
context requirement, false-positiving on Unix epoch timestamps, order
numbers, account IDs, and any other bare digit sequence. Fix 2 split
the regex into a formatted branch (separators self-disambiguate) and a
bare-digits branch that now requires a phone-context keyword (``call``,
``phone``, ``tel``, Thai ``โทร``/``เบอร์``, …) within ±25 chars.
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.phone import PhoneDetector


SHOULD_MATCH: list[str] = [
    # Thai context, bare digits
    "โทร 0812345678",
    "เบอร์ 0891234567",
    "ติดต่อ 0898765432 สำหรับนัดหมาย",
    # English context, bare digits
    "phone: 0812345678",
    "call me at 0812345678",
    "tel: +66812345678",
    "mobile 0898765432 please",
    "hotline 1800123456",
    # Formatted numbers — context not required
    "08-1234-5678",
    "081 234 5678",
    "+66-81-234-5678",
    # Thai landline with 10-digit subscriber range. The detector has a
    # pre-existing ≥10-digit floor (unchanged by Fix 2), so the older
    # 9-digit landline form "02-xxx-xxxx" is not covered — see the
    # design-audit note in the current session. Use the 10-digit shape
    # here so the formatted branch can match.
    "02-2345-6789",
    "555-123-4567",
    "(555) 123-4567",
    "+1-555-123-4567",
]

SHOULD_NOT_MATCH: list[str] = [
    # Epoch timestamp — the case that motivated the fix.
    "1776398778",
    # Cache-buster tag used by the live DLP test harness.
    "[run:1776398778:25]",
    "session tag [run:1776398778:25] here",
    # Order / account IDs without phone context.
    "order ID 0012345678",
    "account 1234567890 is closed",
    "reference number 9876543210",
    # Hospital number — too short (< 10 digits), no context anyway.
    "HN: 0012345",
    # 13-digit Thai ID — a different detector's job, even with noise around it.
    "1234567890123",
    # Credit card style 16-digit run — not a phone.
    "4532015112830366",
    # Clean prose, no digits.
    "no phone here",
]


@pytest.mark.parametrize("text", SHOULD_MATCH)
def test_phone_matches(text: str) -> None:
    detector = PhoneDetector()
    matches = detector.detect(text)
    assert matches, f"Expected match for: {text!r}"
    for m in matches:
        # Basic span integrity check.
        assert text[m.start:m.end] == m.matched_text
        assert m.entity_type == "phone_number"
        assert m.category == "pii"


@pytest.mark.parametrize("text", SHOULD_NOT_MATCH)
def test_phone_no_false_positives(text: str) -> None:
    detector = PhoneDetector()
    matches = detector.detect(text)
    assert not matches, (
        f"Expected NO match for: {text!r}, got: "
        f"{[m.matched_text for m in matches]}"
    )


def test_epoch_suffix_in_tag_is_ignored() -> None:
    """Exact reproduction of the symptom observed in the live test harness."""
    detector = PhoneDetector()
    text = "Device MAC is 00:1A:2B:3C:4D:5E [run:1776398778:25]"
    matches = detector.detect(text)
    assert not matches


def test_bare_digits_with_phone_word_across_window_boundary() -> None:
    """Context keyword just inside the ±25 char window still triggers."""
    detector = PhoneDetector()
    # 20 chars of context then the digits — within the 25-char window.
    text = "please give me a call " + "0812345678" + " now"
    matches = detector.detect(text)
    assert matches, "context keyword within window must still match"


def test_bare_digits_with_phone_word_outside_window_is_ignored() -> None:
    """Context keyword 40 chars away is too far — no match."""
    detector = PhoneDetector()
    filler = "x" * 40
    text = "call me: " + filler + " and here: 0812345678"
    matches = detector.detect(text)
    # "call" is 40+ chars before the digits — outside the ±25 char window.
    assert not matches
