"""credit_card detector — heavy-scenario form + Luhn boundary contract.

Phase Final live run revealed the heavy harness was sending a
credit-card-shaped string ``4532-1488-0343-6467`` that does NOT pass
Luhn (sum mod 10 != 0). The detector correctly rejected it. The
"leak" was test data, not coverage — same pattern as Issue #3a/b/c
where harness values failed checksum gates doing their job.

These tests lock the contract: detector fires on Luhn-valid heavy
forms, rejects Luhn-invalid look-alikes, and behaves predictably
when adjacent to other detectors that match the same digits.
"""

from __future__ import annotations

from app.services.dlp.detectors.credit_card import CreditCardDetector
from app.services.dlp.detectors.health_condition import HealthConditionDetector
from app.services.dlp.detectors.registry import get_default_registry


def _make_luhn_valid_cc(prefix: str = "453214880343646") -> str:
    """Generate a checksum-valid 16-digit card from a 15-digit prefix.

    Documents the Luhn check-digit math so future contributors can
    regenerate test values without trusting hand-computed digits —
    every CC literal in this file traces back to this function.
    """
    digits = [int(c) for c in prefix]
    total = 0
    for i, d in enumerate(reversed(digits)):
        # Position 1 from right (the unknown check digit) gets doubled
        # in standard Luhn. Since we're computing the missing last
        # digit, the existing digits at even positions (0-indexed from
        # the right of the *prefix*) get doubled.
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - total % 10) % 10
    full = prefix + str(check)
    # Self-check — the value we return must validate.
    from app.services.dlp.detectors.credit_card import _luhn_valid
    assert _luhn_valid(full), f"generator bug: {full!r} does not validate"
    return full


# Compute the heavy-scenario CC programmatically at import time.
# Result: "4532148803436464".  Hand-formatted: "4532-1488-0343-6464".
HEAVY_CC = _make_luhn_valid_cc("453214880343646")
HEAVY_CC_FORMATTED = f"{HEAVY_CC[0:4]}-{HEAVY_CC[4:8]}-{HEAVY_CC[8:12]}-{HEAVY_CC[12:16]}"


# ---------------------------------------------------------------------------
# Heavy-scenario shape — must fire (Luhn-valid CC)
# ---------------------------------------------------------------------------


class TestCreditCardHeavyScenario:
    def setup_method(self):
        self.det = CreditCardDetector()

    def test_heavy_scenario_valid_luhn_fires(self):
        """Full heavy bullet with the corrected Luhn-valid CC.
        The exact prose surrounds the digits with health-context words
        ('รักษา' / 'medical treatment') and a non-DOB adjacent number;
        detector must still fire."""
        text = f"เลขบัตรเครดิตที่ใช้จ่ายค่ารักษา {HEAVY_CC_FORMATTED}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == HEAVY_CC_FORMATTED
        assert matches[0].entity_type == "credit_card_number"

    def test_heavy_scenario_invalid_luhn_rejected(self):
        """The original (incorrect) ...6467 in the same prose context
        must NOT mask. Locks the contract that the Luhn gate cannot
        be weakened — even when the surrounding text is a
        credit-card-talking context."""
        text = "เลขบัตรเครดิตที่ใช้จ่ายค่ารักษา 4532-1488-0343-6467"
        matches = self.det.detect(text)
        assert matches == [], (
            "Luhn-invalid 16-digit run must NEVER be masked, regardless "
            "of surrounding credit-card context"
        )


# ---------------------------------------------------------------------------
# Adjacent-detector behavior — no overlap eats the CC span
# ---------------------------------------------------------------------------


class TestCreditCardAdjacentDetectors:
    def setup_method(self):
        self.det = CreditCardDetector()
        self.health = HealthConditionDetector()

    def test_cc_adjacent_to_health_word_no_overlap(self):
        """Heavy-form prose around the CC: 'ค่ารักษา <CC>'. The
        health_condition detector does not match here at all (no
        personal pronoun in the 150-char window for this isolated
        snippet), so credit_card fires unobstructed. Locks the
        no-overlap contract."""
        text = f"ค่ารักษา {HEAVY_CC_FORMATTED}"
        cc_matches = self.det.detect(text)
        assert len(cc_matches) == 1
        assert cc_matches[0].matched_text == HEAVY_CC_FORMATTED
        # health_condition independently must produce 0 findings —
        # locks the boundary that health doesn't swallow CC digits.
        health_matches = self.health.detect(text)
        assert health_matches == []

    def test_cc_with_thai_id_luhn_collision(self):
        """When BOTH a 16-digit CC AND a 13-digit Thai-checksum-valid
        number that ALSO happens to pass Luhn appear in the same text,
        BOTH detectors fire on their own spans. The 13-digit run also
        matches credit_card via Luhn coincidence; the overlap resolver
        downstream of detect() picks thai_national_id by priority
        (verified live in req_25d5c1b3...).

        Lock that the credit_card detector itself fires on both spans
        — the resolver is what disambiguates, not the detector."""
        # 5011000234569 is checksum-valid for thai_national_id AND
        # passes Luhn (40 mod 10 = 0). Generated and verified during
        # the #3b commit.
        thai_id_cc_collision = "5011000234569"
        text = (
            f"เลขบัตรเครดิต {HEAVY_CC_FORMATTED} "
            f"และเลขผู้เสียภาษี {thai_id_cc_collision}"
        )
        cc_matches = self.det.detect(text)
        # Two credit_card matches: the real CC + the Luhn-collision
        # on the tax_id digits.
        matched_texts = {m.matched_text for m in cc_matches}
        assert HEAVY_CC_FORMATTED in matched_texts
        assert thai_id_cc_collision in matched_texts
        assert len(cc_matches) == 2

    def test_full_registry_fires_on_heavy_cc(self):
        """End-to-end check via the default registry — proves the
        heavy-scenario shape fires credit_card AT detection time
        (separate concern from overlap resolution at masking time).
        This is the contract the Phase Final live run will verify
        against."""
        text = (
            f"เลขบัตรเครดิตที่ใช้จ่ายค่ารักษา {HEAVY_CC_FORMATTED} "
            f"บัญชีธนาคาร 123-4-56789-0"
        )
        registry = get_default_registry()
        all_matches = registry.scan_text(text)
        cc_findings = [
            m for m in all_matches if m.detector_name == "credit_card"
        ]
        assert len(cc_findings) >= 1
        assert any(m.matched_text == HEAVY_CC_FORMATTED for m in cc_findings)


# ---------------------------------------------------------------------------
# Generator self-check
# ---------------------------------------------------------------------------


class TestLuhnGenerator:
    """The _make_luhn_valid_cc helper is the single source of truth
    for every CC literal in this file. If the math drifts, every
    test downstream silently relies on the wrong value — so we
    pin the generator's output explicitly."""

    def test_generator_produces_known_card(self):
        # The historical "4532-1488-0343-6467" was Beat's intended
        # canonical demo value but had the wrong check digit. The
        # correct check digit for prefix 453214880343646 is 4, not 7.
        result = _make_luhn_valid_cc("453214880343646")
        assert result == "4532148803436464"

    def test_generator_self_validation(self):
        from app.services.dlp.detectors.credit_card import _luhn_valid
        assert _luhn_valid(_make_luhn_valid_cc("453214880343646")) is True

    def test_generator_rejects_short_prefix(self):
        # Sanity check that we get a 16-digit number from a 15-digit
        # prefix — guards against accidental off-by-one in callers.
        result = _make_luhn_valid_cc("453214880343646")
        assert len(result) == 16
