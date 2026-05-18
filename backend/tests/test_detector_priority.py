"""Tests for the priority tiebreak in ``transformer._resolve_overlaps``.

Covers the three resolution rules:

    1. Safety first — higher ``EnforcementAction`` always wins.
    2. Specificity — equal actions fall back to ``DetectionMatch.priority``.
    3. Position — equal actions + equal priority fall back to the
       earlier start offset.

Also checks two integration-level invariants:

    - The 5 specific detectors (``thai_national_id``, ``thai_tax_id``,
      ``thai_bank_account``, ``thai_promptpay``, ``medical_record``)
      declare ``priority = 10`` on the class.
    - An end-to-end scan through the default registry + transformer
      masks a 13-digit Thai ID — the priority system ensures the
      thai_national_id detector wins on the shared span, not phone.
"""

from __future__ import annotations

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.services.dlp.detectors.medical_record import MedicalRecordDetector
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector
from app.services.dlp.detectors.thai_national_id import ThaiNationalIdDetector
from app.services.dlp.detectors.thai_promptpay import ThaiPromptPayDetector
from app.services.dlp.detectors.thai_tax_id import ThaiTaxIdDetector
from app.services.dlp.transformer import DLPTransformer, _resolve_overlaps


# Valid Thai national ID (see test_new_detectors.py for the computation).
VALID_THAI_ID = "1234567890121"


def _match(
    detector: str,
    start: int,
    end: int,
    *,
    priority: int = 0,
) -> DetectionMatch:
    """Helper to build a DetectionMatch with just the fields that matter here."""
    return DetectionMatch(
        detector_name=detector,
        entity_type=f"{detector}_value",
        category="pii",
        start=start,
        end=end,
        matched_text="x" * (end - start),
        severity=SensitivityLevel.MEDIUM,
        priority=priority,
    )


def _action(
    detector: str,
    start: int,
    end: int,
    *,
    action: EnforcementAction = EnforcementAction.MASK,
    priority: int = 0,
) -> MatchAction:
    return MatchAction(
        match=_match(detector, start, end, priority=priority),
        action=action,
        reason_code="test",
        reason_message="test",
    )


# ---------------------------------------------------------------------------
# Rule 1 — safety first
# ---------------------------------------------------------------------------


class TestSafetyBeatsPriority:
    def test_higher_action_wins_even_over_higher_priority(self):
        # specific detector has priority=10 but only MASK;
        # generic detector has priority=0 but BLOCK → BLOCK wins.
        specific = _action(
            "specific", 0, 13,
            action=EnforcementAction.MASK,
            priority=10,
        )
        generic = _action(
            "generic", 0, 13,
            action=EnforcementAction.BLOCK,
            priority=0,
        )

        kept = _resolve_overlaps([specific, generic])
        assert len(kept) == 1
        assert kept[0].match.detector_name == "generic"
        assert kept[0].action == EnforcementAction.BLOCK


# ---------------------------------------------------------------------------
# Rule 2 — specificity (priority tiebreak at equal action)
# ---------------------------------------------------------------------------


class TestPriorityBreaksEqualActionTies:
    def test_higher_priority_wins_on_same_span(self):
        # Exact same span, same action, different priority.
        high = _action("high", 3, 16, priority=10)
        low = _action("low", 3, 16, priority=0)

        kept = _resolve_overlaps([low, high])
        assert len(kept) == 1
        assert kept[0].match.detector_name == "high"

    def test_higher_priority_wins_when_starting_later(self):
        # Generic detector starts earlier but specific starts later
        # and overlaps — priority should still flip the winner.
        early_generic = _action("generic", 3, 16, priority=0)
        late_specific = _action("specific", 5, 18, priority=10)

        kept = _resolve_overlaps([early_generic, late_specific])
        assert len(kept) == 1
        assert kept[0].match.detector_name == "specific"


# ---------------------------------------------------------------------------
# Rule 3 — position fallback (equal action, equal priority)
# ---------------------------------------------------------------------------


class TestPositionFallback:
    def test_earlier_start_wins_when_priority_and_action_are_equal(self):
        first = _action("first", 3, 16, priority=0)
        second = _action("second", 3, 16, priority=0)

        kept = _resolve_overlaps([first, second])
        assert len(kept) == 1
        # ``first`` was given first; the sort is stable enough that
        # ties fall to the earlier list entry after sorting by start.
        assert kept[0].match.detector_name == "first"


# ---------------------------------------------------------------------------
# Non-overlapping spans are all kept
# ---------------------------------------------------------------------------


class TestNonOverlapping:
    def test_non_overlapping_spans_all_kept(self):
        a = _action("a", 0, 5, priority=10)
        b = _action("b", 10, 20, priority=0)
        c = _action("c", 25, 30, priority=5)

        kept = _resolve_overlaps([a, b, c])
        assert {k.match.detector_name for k in kept} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Class-level invariants — the 5 specific detectors declare priority=10
# ---------------------------------------------------------------------------


class TestDetectorClassPriorities:
    def test_thai_national_id_priority(self):
        assert ThaiNationalIdDetector.priority == 10

    def test_thai_tax_id_priority(self):
        assert ThaiTaxIdDetector.priority == 10

    def test_thai_bank_account_priority(self):
        assert ThaiBankAccountDetector.priority == 10

    def test_thai_promptpay_priority(self):
        assert ThaiPromptPayDetector.priority == 10

    def test_medical_record_priority(self):
        assert MedicalRecordDetector.priority == 10


# ---------------------------------------------------------------------------
# End-to-end — Thai ID now wins over phone via registry + transformer
# ---------------------------------------------------------------------------


class TestThaiIdBeatsPhoneEndToEnd:
    def test_thai_id_mask_token_wins_over_phone_on_13_digit_span(self):
        """The 13-digit Thai ID must be masked, never picked up as phone.

        History: pre-priority-fix, phone.py's bare ``\\b\\d{10,15}\\b``
        branch matched the 13-digit span and the overlap resolver picked
        phone. The priority system was added so ``thai_national_id``
        (priority=10) beats phone on shared spans.

        Post Fix 2 (context-gated bare digits), phone no longer fires on
        this input at all — there is no phone-context keyword near the
        digits — so the overlap doesn't even arise. The test asserts
        the user-visible guarantee under the neutral ``<rN>`` shape:
        the ID is the only span that's masked.
        """
        text = f"citizen id {VALID_THAI_ID}"
        registry = get_default_registry(disabled_detectors=[])
        matches = registry.scan_text(text)

        names = {m.detector_name for m in matches}
        assert "thai_national_id" in names
        # "phone" no longer matches bare 13-digit runs without phone
        # context (Fix 2). The priority system still protects the
        # user-visible invariant below even if phone ever re-matches
        # later — the transformer would still pick thai_national_id.
        assert "phone" not in names

        from app.domain.dlp import EnforcementAction, MatchAction
        actions = [
            MatchAction(
                match=m,
                action=EnforcementAction.MASK,
                reason_code="test",
                reason_message="test",
            )
            for m in matches
        ]
        masked_text, span_map, _ = DLPTransformer().mask_text(text, actions)

        assert VALID_THAI_ID not in masked_text
        assert "<r0>" in masked_text
        assert VALID_THAI_ID in span_map.values()
