"""Overlap resolution between detectors.

When two detectors fire on the same span, ``_resolve_overlaps`` keeps:
1. The higher EnforcementAction (BLOCK > MASK > REDACT > ALLOW), then
2. The higher detector priority, then
3. The earlier start offset.

These tests pin the priority/specificity ordering that the gateway
relies on so a future "priority bump" doesn't silently demote a
specific detector.
"""

from __future__ import annotations

from ..test_dlp_detectors._helpers import has_match, mask

VALID_THAI_ID = "3100112345674"  # passes mod-11

_NAME_DETECTORS = ("thai_name", "thai_name_no_prefix", "presidio_ner")


def test_phone_wins_over_ner():
    # A Thai-format phone could theoretically be picked up by NER as
    # an organisation / number entity. The phone detector has
    # priority=5 (NER default is 0), so it wins.
    _, _, matches = mask("+66 81-234-5678")
    assert has_match(matches, "phone")
    assert not has_match(matches, *_NAME_DETECTORS)


def test_credential_wins_over_ip_inside_url():
    text = "postgresql://admin:pass@192.168.1.105:5432/prod"
    _, span_map, _ = mask(text)
    # Overlap resolution keeps a single span — the full URL.
    assert len(span_map) == 1
    [value] = span_map.values()
    assert value == text


def test_health_condition_wins_over_ner_person():
    # The labelled health regex covers ``Medical: diabetes and high
    # blood pressure`` as a single span. NER might tag a substring as
    # ORG/PERSON; the longer health span wins by overlap resolution.
    text = "Medical: diabetes and high blood pressure"
    _, _, matches = mask(text)
    assert has_match(matches, "health_condition")


def test_thai_name_wins_over_presidio_ner():
    # ``นาย สมชาย ใจดี`` — the regex-based thai_name detector and the
    # NER PERSON both target the name. The transformer's overlap
    # resolution must produce exactly one NAME-family token per name.
    text = "นาย สมชาย ใจดี ติดต่อ"
    _, span_map, _ = mask(text)
    # Exactly one mask span — the name.
    assert len(span_map) == 1


def test_thai_id_priority_over_generic_number():
    # Valid Thai ID inside a sentence; the thai_national_id detector
    # has priority=10 so it beats the generic phone matcher on the
    # same 13-digit span.
    text = f"เลขประจำตัวประชาชน {VALID_THAI_ID}"
    _, _, matches = mask(text)
    # The crucial check is that no PHONE_NUMBER finding surfaces.
    assert not has_match(matches, "phone")
