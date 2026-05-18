"""Mask → restore round-trip identity.

For every fixture, ``restore(mask(text)) == text`` MUST hold. This is
the contract the orchestrator relies on to give users back exactly
what they typed after a provider call: the mask side hides PII from
the model, the restore side puts it back before the response leaves
the gateway.

If a future detector change causes the spans to drift (e.g. an
off-by-one on a label boundary), the round-trip assertion will fail
even if the masking still ``looks right`` to the eye — the diff in
``masked`` vs ``original`` is the most reliable canary we have.
"""

from __future__ import annotations

from ..test_dlp_detectors._helpers import mask, restore


def _assert_roundtrip(text: str) -> None:
    masked, span_map, _ = mask(text)
    assert restore(masked, span_map) == text, (
        f"round-trip mismatch:\n"
        f"  original: {text!r}\n"
        f"  masked:   {masked!r}\n"
        f"  restored: {restore(masked, span_map)!r}"
    )


def test_single_email_en():
    _assert_roundtrip("alice@example.com")


def test_single_email_th():
    _assert_roundtrip("อีเมล somchai@example.co.th")


def test_three_different_emails():
    _assert_roundtrip(
        "Contact john@a.com, sarah@b.com, or alice@c.com please"
    )


def test_mixed_en_email_phone_card():
    _assert_roundtrip(
        "Email user@example.com Phone: +66 81-234-5678 "
        "Credit card: 4111 1111 1111 1111"
    )


def test_mixed_th_profile():
    _assert_roundtrip(
        "เบอร์โทร: 081-234-5678 เลขบัญชี: 123-4-56789-0 "
        "ชื่อ: นาย สมชาย ใจดี ที่อยู่: 99/88 ถนนสุขุมวิท กรุงเทพ 10110"
    )


def test_full_en_profile():
    _assert_roundtrip(
        "Customer: Somchai Jaidee, "
        "Email: somchai@example.com, "
        "Phone: +66 81-234-5678, "
        "DOB: 15/03/1990, "
        "SSN: 123-45-6789, "
        "Credit card: 4111 1111 1111 1111, "
        "IBAN: GB82 WEST 1234 5698 7654 32, "
        "Address: 123 Main Street, New York, NY 10001, "
        "IP: 172.16.0.1, "
        "MAC: 00:1A:2B:3C:4D:5E, "
        "Password: P@ssw0rd123!"
    )


def test_full_th_profile():
    _assert_roundtrip(
        "ชื่อ: นาย สมชาย ใจดี "
        "เบอร์โทร: 081-234-5678 "
        "อีเมล: somchai@example.com "
        "เลขบัตรประชาชน: 3-1001-12345-67-4 "
        "เลขบัญชี: 123-4-56789-0 "
        "ที่อยู่: 99/88 ถนนสุขุมวิท กรุงเทพฯ 10110 "
        "เงินเดือน: 85,000 บาท/เดือน "
        "LINE: @somchai_99"
    )


def test_clean_text_no_pii():
    """No PII → masked text equals original AND span_map is empty."""
    text = "What is the capital of Thailand? The weather is sunny."
    masked, span_map, _ = mask(text)
    assert masked == text
    assert span_map == {}
