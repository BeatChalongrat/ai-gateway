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

from ..test_dlp_detectors._helpers import mask

VALID_THAI_ID = "3100112345674"  # passes mod-11


def test_phone_wins_over_ner():
    # A Thai-format phone could theoretically be picked up by NER as
    # an organisation / number entity. The phone detector has
    # priority=5 (NER default is 0), so it wins.
    masked, span_map, _ = mask("+66 81-234-5678")
    assert any("PHONE_NUMBER" in t for t in span_map)
    # NAME token from NER must not steal the same span.
    assert not any("NAME" in t for t in span_map)


def test_credential_wins_over_ip_inside_url():
    text = "postgresql://admin:pass@192.168.1.105:5432/prod"
    _, span_map, _ = mask(text)
    cred_count = sum(1 for t in span_map if "CREDENTIAL" in t)
    ip_count = sum(1 for t in span_map if "IP_ADDRESS" in t)
    assert cred_count == 1
    assert ip_count == 0


def test_health_condition_wins_over_ner_person():
    # The labelled health regex covers ``Medical: diabetes and high
    # blood pressure`` as a single span. NER might tag a substring as
    # ORG/PERSON; the longer health span wins by overlap resolution.
    text = "Medical: diabetes and high blood pressure"
    _, span_map, _ = mask(text)
    assert any("HEALTH_CONDITION" in t for t in span_map)


def test_thai_name_wins_over_presidio_ner():
    # ``นาย สมชาย ใจดี`` — the regex-based thai_name detector and the
    # NER PERSON both target the name. The transformer's overlap
    # resolution must produce exactly one NAME-family token per name.
    text = "นาย สมชาย ใจดี ติดต่อ"
    _, span_map, _ = mask(text)
    name_tokens = [t for t in span_map if "NAME" in t]
    assert len(name_tokens) == 1


def test_thai_id_priority_over_generic_number():
    # Valid Thai ID inside a sentence; the thai_national_id detector
    # has priority=10 so it beats the generic phone matcher on the
    # same 13-digit span.
    text = f"เลขประจำตัวประชาชน {VALID_THAI_ID}"
    _, span_map, _ = mask(text)
    # We accept either THAI_ID or PROMPTPAY winning (both have
    # priority 10 and the keyword path could match either) — the
    # crucial check is that no PHONE_NUMBER token surfaces.
    assert not any("PHONE_NUMBER" in t for t in span_map)
