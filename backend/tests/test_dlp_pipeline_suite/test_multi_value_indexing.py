"""Multi-value indexing: distinguish multiple occurrences in one call.

The counter-mode transformer mints ``<r0>``, ``<r1>``, … in reading
order under a single global counter. Same value seen twice still ends
up with two distinct indices in counter mode — deduplication is the
deterministic-mode behaviour, which we exercise separately in
``test_deterministic_tokenization``. The round-trip assertion is the
contract that matters here: the restore step swaps every token back
to the original it points to, regardless of how the indices were
allocated.
"""

from __future__ import annotations

from ..test_dlp_detectors._helpers import mask, restore, tokens_in


def test_three_different_emails_get_distinct_tokens():
    text = "Email john@a.com, sarah@b.com, alice@c.com please"
    _, span_map, matches = mask(text)
    emails = tokens_in(span_map, "EMAIL", matches)
    assert sorted(emails) == ["<r0>", "<r1>", "<r2>"]
    assert span_map["<r0>"] == "john@a.com"
    assert span_map["<r1>"] == "sarah@b.com"
    assert span_map["<r2>"] == "alice@c.com"


def test_same_email_twice_round_trips():
    text = "alice@example.com ... alice@example.com"
    masked, span_map, _ = mask(text)
    assert restore(masked, span_map) == text


def test_mixed_categories_dont_collide():
    """Email + Phone + another Email — global counter assigns distinct
    indices across categories in position order, and the resulting
    masked text restores losslessly."""
    text = (
        "Email john@a.com Phone: +66 81-234-5678 "
        "and alice@c.com for backup"
    )
    masked, span_map, matches = mask(text)
    emails = tokens_in(span_map, "EMAIL", matches)
    phones = tokens_in(span_map, "PHONE_NUMBER", matches)
    assert len(emails) == 2
    assert len(phones) == 1
    assert restore(masked, span_map) == text


def test_restore_replaces_all_occurrences_of_each_token():
    """If the model echoes the same token twice, restore must replace
    every occurrence — not just the first."""
    _, span_map, _ = mask("alice@example.com")
    # Synthesise a model output that mentions the token twice.
    echoed = "Sending to <r0> and also to <r0>"
    restored = restore(echoed, span_map)
    assert restored.count("alice@example.com") == 2
    assert "<r0>" not in restored
