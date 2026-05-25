"""Thai national ID detector — keyword gate + dashed/spaced digit support.

Pre-fix:
  - The keyword regex listed only ``เลขประจำตัว`` / ``บัตรประชาชน`` /
    ``เลขบัตร`` / English ``national id`` / ``citizen id`` / ``ID``.
    The common Thai abbreviation ``บัตร ปชช`` (often written with a
    dot, ``บัตร ปชช.``) didn't match.
  - The keyword path required ``\\d{13}`` — bare 13 digits — so the
    canonical dashed form ``1-1234-56789-01-0`` slipped through even
    with a keyword present.

This file pins both fixes. The valid-checksum NIDs used below were
computed off-line via the standard Thai mod-11 weighting
``sum(d[i] * (13-i))`` for i=0..11, ``d13 = (11 - sum%11) % 10``.
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.thai_national_id import (
    ThaiNationalIdDetector,
)


# Valid NIDs precomputed for these tests.
VALID_NID_DASHED_A = "1-1234-56789-01-4"     # 1123456789014 — checksum 4
VALID_NID_DASHED_B = "3-1012-34567-89-3"     # 3101234567893 — checksum 3
VALID_NID_DASHED_C = "1-2345-67890-12-1"     # 1234567890121 — checksum 1
VALID_NID_BARE = "1100700123455"             # 1100700123455 — checksum 5
INVALID_NID_BARE = "1100700123456"           # 1100700123456 — wrong checksum (6)


detector = ThaiNationalIdDetector()


# ---------------------------------------------------------------------------
# Test 1 — Thai keyword "บัตรประชาชน" (no abbreviation, no space)
# ---------------------------------------------------------------------------


def test_nid_with_bat_prachachon():
    """``บัตรประชาชน 1-1234-56789-01-4`` — full keyword + dashed NID."""
    text = f"บัตรประชาชน {VALID_NID_DASHED_A}"
    matches = detector.detect(text)
    assert len(matches) == 1, (
        f"expected 1 NID match in {text!r}; got "
        f"{[(m.start, m.end, m.matched_text) for m in matches]}"
    )
    assert VALID_NID_DASHED_A in matches[0].matched_text


# ---------------------------------------------------------------------------
# Test 2 — Thai abbreviation "บัตร ปชช." (with space + dot)
# ---------------------------------------------------------------------------


def test_nid_with_bat_por_chor():
    """``บัตร ปชช. 3-1012-34567-89-3`` — common short-form keyword.

    This was the original repro case from the real-LLM E2E. The
    abbreviation regex ``บัตร\\s*ปชช\\.?`` covers all four written
    variants (with/without space, with/without trailing dot).
    """
    text = f"บัตร ปชช. {VALID_NID_DASHED_B}"
    matches = detector.detect(text)
    assert any(
        VALID_NID_DASHED_B in m.matched_text for m in matches
    ), f"expected NID detected; got {[m.matched_text for m in matches]}"


# ---------------------------------------------------------------------------
# Test 3 — Keyword + dashed format (strip separators before validation)
# ---------------------------------------------------------------------------


def test_nid_with_dashes_stripped():
    """``เลขบัตร 1-2345-67890-12-1`` — keyword + valid dashed NID.

    The keyword-path regex captures the dashed form via
    ``(?:\\d[-\\s]?){12}\\d``; the matched text retains its dashes
    so restore can swap it back verbatim.
    """
    text = f"เลขบัตร {VALID_NID_DASHED_C}"
    matches = detector.detect(text)
    matched_texts = [m.matched_text for m in matches]
    assert any(VALID_NID_DASHED_C in t for t in matched_texts), (
        f"expected dashed NID matched; got {matched_texts}"
    )


# ---------------------------------------------------------------------------
# Test 4 — No keyword → fall back to checksum-only path
# ---------------------------------------------------------------------------


def test_nid_without_keyword_needs_valid_checksum():
    """Bare 13-digit run with no keyword: detector fires only if the
    mod-11 checksum passes.

    The invalid NID below differs from a valid one by just the
    check digit; without the keyword anchor the detector has no
    other signal to anchor on and must rely on the checksum.
    """
    # Invalid checksum, no keyword → NOT detected.
    invalid_text = f"หมายเลข {INVALID_NID_BARE}"
    invalid_matches = detector.detect(invalid_text)
    assert invalid_matches == [], (
        "invalid-checksum NID without keyword should not match; "
        f"got {[m.matched_text for m in invalid_matches]}"
    )

    # Valid checksum, no keyword → detected (checksum path).
    valid_text = f"หมายเลข {VALID_NID_BARE}"
    valid_matches = detector.detect(valid_text)
    assert any(
        VALID_NID_BARE in m.matched_text for m in valid_matches
    ), (
        f"valid-checksum NID should match without keyword; "
        f"got {[m.matched_text for m in valid_matches]}"
    )


# ---------------------------------------------------------------------------
# Test 5 — English keyword "NID" + dashed format
# ---------------------------------------------------------------------------


def test_nid_english_keyword():
    """``NID: 1-1234-56789-01-4`` — English-language label + dashed NID.

    Covers the case where developer-facing prompts (admin logs,
    debug strings) use the English abbreviation rather than the
    Thai form.
    """
    text = f"NID: {VALID_NID_DASHED_A}"
    matches = detector.detect(text)
    assert any(
        VALID_NID_DASHED_A in m.matched_text for m in matches
    ), f"expected NID match for English keyword; got {[m.matched_text for m in matches]}"


# ---------------------------------------------------------------------------
# Sanity — invalid-checksum NID WITH keyword is still detected
# ---------------------------------------------------------------------------


def test_keyword_path_bypasses_checksum():
    """Pinned behaviour: when a keyword precedes the digit run, the
    detector fires regardless of the checksum. Operators put real
    NIDs and test/placeholder NIDs both in fields labelled
    ``บัตรประชาชน``; the keyword is the strongest possible signal
    that the field is an ID, and a leak is worse than over-masking
    a placeholder.
    """
    text = f"บัตร ปชช. {INVALID_NID_BARE}"  # 1100700123456 — bad checksum
    matches = detector.detect(text)
    assert matches, (
        f"keyword path must detect even invalid-checksum NIDs; "
        f"got no matches in {text!r}"
    )
