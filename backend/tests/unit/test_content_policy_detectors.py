"""Content-policy detector unit tests (Sprint 4) — direct detect() calls.

These detectors are wired into the MEDIA_GEN scan path (Batch E), NOT the
default registry, so chat traffic is unaffected — test 4 asserts that.
"""

from app.domain.dlp import SensitivityLevel
from app.services.dlp.detectors.content_policy import (
    CopyrightEntityDetector,
    CSAMKeywordDetector,
    PublicFigureDetector,
)


def test_csam_detector_matches_and_severity():
    det = CSAMKeywordDetector()
    # Known indicator (decoded from the encoded seed) — should match.
    hits = det.detect("a request for child porn please")
    assert len(hits) >= 1
    assert hits[0].severity == SensitivityLevel.CRITICAL
    assert hits[0].category == "content_policy"
    assert hits[0].entity_type == "csam"
    # Offset accuracy contract.
    h = hits[0]
    assert "a request for child porn please"[h.start : h.end] == h.matched_text

    # Clean text → nothing.
    assert det.detect("a photo of a golden retriever puppy") == []

    # Case-insensitive.
    assert len(det.detect("CHILD PORN")) >= 1


def test_copyright_detector_matches_artist_and_character():
    det = CopyrightEntityDetector()
    assert len(det.detect("Make a song like Taylor Swift")) >= 1  # artist
    assert len(det.detect("Draw Mickey Mouse on a skateboard")) >= 1  # character
    # Diacritic-insensitive: data has "Beyoncé", prompt says "beyonce".
    assert len(det.detect("a track in the style of beyonce")) >= 1
    # Clean.
    assert det.detect("a generic cat playing piano") == []
    # Severity / category.
    h = det.detect("Draw Mickey Mouse")[0]
    assert h.severity == SensitivityLevel.HIGH
    assert h.category == "content_policy"
    assert h.entity_type == "copyright"


def test_public_figure_detector_matches():
    det = PublicFigureDetector()
    assert len(det.detect("Photo of Elon Musk at a desk")) >= 1
    assert det.detect("a random person walking a dog") == []
    h = det.detect("a portrait of Donald Trump")[0]
    assert h.severity == SensitivityLevel.HIGH
    assert h.entity_type == "public_figure"
    assert "a portrait of Donald Trump"[h.start : h.end] == h.matched_text


def test_word_boundary_avoids_false_substring():
    # "Mario" should not fire inside an unrelated longer token.
    det = CopyrightEntityDetector()
    assert det.detect("marionette puppet show") == []


def test_detectors_absent_from_default_registry():
    # Content-policy detectors must NOT run on the default (chat) path.
    from app.services.dlp.detectors.registry import get_default_registry

    reg = get_default_registry(ner_enabled=False)
    for name in ("csam_keyword", "copyright_entity", "public_figure"):
        assert name not in reg
