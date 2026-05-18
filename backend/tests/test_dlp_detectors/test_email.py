"""Email detector — EN + TH coverage.

Detector contract verified here:
- Single emails get a neutral ``<rN>`` token bound to the email value.
- Duplicate emails dedup to one token only when the deterministic-token
  mode is on; in counter mode each occurrence still resolves to the
  same surface form because there's only one ``<r0>`` available for the
  first email's value. We assert the post-restore identity instead of
  digging into counter internals.
- Labels (``Email:``, ``อีเมล:``) are not part of the matched span and
  therefore survive masking.
- The regex needs ``local@host.tld`` shape — bare strings and ``@host``
  prefixes don't fire.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask, restore, tokens_in


def _is_email_value(value: str) -> bool:
    return "@" in value and "." in value.split("@", 1)[1]


class TestEmailHappyPath:
    def test_bare_email_en(self):
        _, span_map, matches = mask("alice@example.com")
        assert tokens_in(span_map, "EMAIL", matches) == ["<r0>"]
        assert span_map["<r0>"] == "alice@example.com"

    def test_labelled_email_preserves_label(self):
        masked, span_map, matches = mask("Email: alice@example.com")
        assert "Email:" in masked
        assert tokens_in(span_map, "EMAIL", matches) == ["<r0>"]

    def test_two_distinct_emails_get_two_tokens(self):
        text = "john.doe@company.com and sarah.smith@company.com"
        masked, span_map, matches = mask(text)
        tokens = tokens_in(span_map, "EMAIL", matches)
        assert tokens == ["<r0>", "<r1>"]
        # Restore is the round-trip guarantee.
        assert restore(masked, span_map) == text

    def test_repeated_email_round_trips(self):
        text = "alice@example.com ... alice@example.com"
        masked, span_map, _ = mask(text)
        assert restore(masked, span_map) == text


class TestEmailBoundary:
    def test_subdomain_en(self):
        _, span_map, _ = mask("user@mail.company.co.th")
        assert any(_is_email_value(v) for v in span_map.values())

    def test_plus_addressing_en(self):
        _, span_map, _ = mask("user+tag@example.com")
        assert "user+tag@example.com" in span_map.values()

    def test_uppercase_en(self):
        _, span_map, _ = mask("USER@EXAMPLE.COM")
        assert any(_is_email_value(v) for v in span_map.values())

    def test_thai_prefix(self):
        text = "ส่งอีเมลไปที่ somchai@example.co.th"
        masked, span_map, _ = mask(text)
        assert "<r0>" in masked
        assert restore(masked, span_map) == text

    def test_thai_label_preserved(self):
        masked, _, _ = mask("อีเมล: test.user@company.th")
        assert "อีเมล:" in masked
        assert "<r0>" in masked


class TestEmailNegative:
    def test_not_an_email(self):
        assert "email" not in detector_names_for("not-an-email")

    def test_missing_local_part(self):
        assert "email" not in detector_names_for("@example.com")
