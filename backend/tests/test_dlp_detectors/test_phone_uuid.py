"""FIX27 Part 3 — a UUID is not a phone number (the attachment envelope's
fileId was masked as <<PHONE_n>> on every document turn)."""
from app.services.dlp.detectors.phone import PhoneDetector


def _phones(text):
    return [text[m.start:m.end] for m in PhoneDetector().detect(text)]


def test_uuid_alone_is_not_a_phone():
    assert _phones("fileId 7a86d915-4821-4469-9475-efbb535a5916 uploaded") == []


def test_uuid_inside_the_observation_envelope_is_ignored_but_a_real_phone_still_masks():
    text = '{"origin":{"fileId":"7a86d915-4821-4469-9475-efbb535a5916"},"content":"phone 081-234-5678"}'
    assert _phones(text) == ["081-234-5678"]


def test_a_formatted_phone_that_merely_looks_hex_still_masks():
    assert _phones("โทร 081-234-5678 ครับ") == ["081-234-5678"]
