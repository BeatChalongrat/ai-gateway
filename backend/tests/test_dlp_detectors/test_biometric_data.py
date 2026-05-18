"""Biometric data detector — PDPA Section 26."""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask


class TestBiometricHappyPath:
    def test_english_fingerprint_personal(self):
        text = "I registered my fingerprint at the office"
        _, _, matches = mask(text)
        assert has_match(matches, "biometric_data")

    def test_english_face_scan(self):
        # The English biometric regex expects ``face`` followed by
        # ``scan``/``recognition``/``ID`` etc. — i.e. the keyword
        # cluster, not a verb-object phrase. ``face scan`` with a
        # registration verb is the canonical form.
        text = "We register their face scan for entry"
        _, _, matches = mask(text)
        assert has_match(matches, "biometric_data")

    def test_thai_fingerprint(self):
        text = "ผมลงทะเบียนลายนิ้วมือกับบริษัท"
        _, _, matches = mask(text)
        assert has_match(matches, "biometric_data")

    def test_thai_face_scan(self):
        text = "เราใช้สแกนใบหน้าเข้าออฟฟิศ"
        _, _, matches = mask(text)
        assert has_match(matches, "biometric_data")


class TestBiometricBoundary:
    def test_retinal_scan_english(self):
        text = "We register her retina scan for the vault"
        _, _, matches = mask(text)
        assert has_match(matches, "biometric_data")


class TestBiometricNegative:
    def test_metaphorical_fingerprint(self):
        # ``fingerprint of the killer`` — figurative, no personal
        # context pronoun owning the fingerprint.
        assert "biometric_data" not in detector_names_for(
            "the fingerprint of the killer"
        )

    def test_document_scan_thai(self):
        # ``สแกนเอกสาร`` = document scan — not biometric.
        assert "biometric_data" not in detector_names_for("สแกนเอกสาร")
