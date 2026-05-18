"""Presidio NER detector — English PERSON / LOCATION / ORG / GPE / NRP.

Skipped wholesale when Presidio/spaCy isn't available (ner_status
returns None) — there's no meaningful coverage to assert in that case.
The denylist tests (``Thai``, ``Salary``) target regressions where NER
mis-classified field labels and nationality adjectives as PERSON.
"""

from __future__ import annotations

import pytest

from app.services.dlp.ner_status import get_analyzer

from ._helpers import detector_names_for, mask, tokens_in

pytestmark = pytest.mark.skipif(
    get_analyzer() is None,
    reason="Presidio analyzer not loaded (no spaCy model)",
)


class TestNerHappyPath:
    def test_english_name_masked(self):
        _, span_map, matches = mask("Customer name: Somchai Jaidee")
        assert tokens_in(span_map, "NAME", matches)

    def test_two_distinct_names(self):
        text = "Customer: John Smith, contact: Jane Doe"
        _, span_map, matches = mask(text)
        assert len(tokens_in(span_map, "NAME", matches)) >= 2


class TestNerNegative:
    def test_thai_label_not_a_person(self):
        # "Thai" is denylisted to stop the analyzer flagging the
        # ``Thai ID:`` label fragment as a PERSON / NRP.
        _, span_map, _ = mask("Thai ID: 3100112345674")
        # No NAME-type token should bind to the literal word "Thai".
        for tok, val in span_map.items():
            assert val.strip().lower() != "thai", (
                f"NER mis-tagged 'Thai' as a name: {tok}"
            )

    def test_salary_label_not_a_person(self):
        _, span_map, _ = mask("Salary: 85,000 THB/month")
        for tok, val in span_map.items():
            assert val.strip().lower() != "salary", (
                f"NER mis-tagged 'Salary' as a name: {tok}"
            )

    def test_existing_mask_token_passes_through(self):
        # The orchestrator scans inbound text after the inbound mask
        # pass; tokens like ``<r0>`` MUST not be re-tagged.
        text = "Your contact is <r0>"
        masked, _, _ = mask(text)
        assert "<r0>" in masked

    def test_common_phrase(self):
        # "the following" must not fire any name token.
        _, span_map, matches = mask("Please review the following")
        assert not tokens_in(span_map, "NAME", matches)
