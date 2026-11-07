"""Phase 12 A3 — restore() must be single-pass.

The old ``restore`` ran N sequential ``str.replace`` passes over the
whole string. A restored VALUE that itself contained another
still-pending token literal was then re-scanned by a later pass and
double-substituted, corrupting the user's original data. The fix is one
compiled alternation (escaped tokens, longest-first) applied in a single
scan.

Fails-before: revert ``_substitute_tokens_single_pass`` in
``restore`` back to the per-token replace loop and the two pathological
tests below fail (the embedded token literal inside a restored value
gets substituted a second time). The round-trip and semantics tests pin
that the refactor is behaviour-identical for normal inputs.
"""

from __future__ import annotations

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.services.dlp.transformer import DLPTransformer


@pytest.fixture
def transformer():
    return DLPTransformer()


def _mask_action(text: str, value: str, detector: str = "email") -> MatchAction:
    start = text.index(value)
    return MatchAction(
        match=DetectionMatch(
            detector_name=detector,
            entity_type=detector,
            category="pii",
            start=start,
            end=start + len(value),
            matched_text=value,
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message="test",
    )


class TestPathologicalNestedTokens:
    def test_value_containing_pending_legacy_token_is_not_resubstituted(
        self, transformer
    ):
        # <r0>'s ORIGINAL VALUE contains the literal text "<r1>", and
        # <r1> is also pending in the span_map. Insertion order puts
        # <r0> first in the old (stable-sorted, equal-length) replace
        # loop, so the old code substituted "<r1>" INSIDE the restored
        # value on the second pass.
        span_map = {
            "<r0>": "call <r1> later",
            "<r1>": "bob@example.com",
        }
        text = "A: <r0> B: <r1>"
        restored = transformer.restore(text, span_map)
        assert restored == "A: call <r1> later B: bob@example.com"

    def test_value_containing_pending_pseudonym_token_is_not_resubstituted(
        self, transformer
    ):
        # The prompt's canonical example: the value being restored for
        # the (longer, hence earlier-replaced) PERSON token contains the
        # shorter EMAIL token literal while EMAIL is also pending.
        span_map = {
            "<<PERSON_A>>": "call <<EMAIL_1>> later",
            "<<EMAIL_1>>": "a@x.com",
        }
        text = "hi <<PERSON_A>>, then <<EMAIL_1>>"
        restored = transformer.restore(text, span_map)
        assert restored == "hi call <<EMAIL_1>> later, then a@x.com"

    def test_injected_literal_not_fed_to_vault_resolver(self, transformer):
        """The surviving literal inside a restored value is the user's
        original data — it must NOT be treated as an orphan and offered
        to the cross-turn vault for a second substitution."""
        span_map = {
            "<r0>": "call <r1> later",
            "<r1>": "bob@example.com",
        }
        seen: list[list[str]] = []

        def resolver(tokens):
            seen.append(list(tokens))
            return {t: "FROM_VAULT" for t in tokens}

        restored = transformer.restore("X <r0> Y <r1>", span_map, resolver)
        assert restored == "X call <r1> later Y bob@example.com"
        # No orphan call at all: every real token was locally restored.
        assert seen == []


class TestOrderingSemanticsPreserved:
    def test_longest_first_prefix_tokens(self, transformer):
        # <r1> is a prefix of <r10>; the alternation must pick the
        # longer token at the same position (same guarantee the old
        # ``sorted(key=len, reverse=True)`` loop provided).
        span_map = {"<r1>": "one", "<r10>": "ten"}
        assert transformer.restore("<r10> <r1>", span_map) == "ten one"

    def test_allowed_tokens_filter_still_enforced(self, transformer):
        span_map = {"<r0>": "alice@x.com", "<r1>": "sk-secret-key"}
        restored = transformer.restore(
            "a=<r0> b=<r1>", span_map, allowed_tokens={"<r0>"}
        )
        assert restored == "a=alice@x.com b=<r1>"  # disallowed token survives

    def test_normalize_still_runs_before_substitution(self, transformer):
        # Fuzzy variants (case / padding) must still canonicalise and
        # then restore in the same call.
        span_map = {"<r0>": "alice@x.com"}
        assert transformer.restore("v: < R0 >", span_map) == "v: alice@x.com"

    def test_vault_resolver_path_still_restores_orphans(self, transformer):
        restored = transformer.restore(
            "echo <r7>", {}, lambda toks: {"<r7>": "prior-turn-value"}
        )
        assert restored == "echo prior-turn-value"


class TestMaskRestoreRoundTrip:
    def test_multi_entity_round_trip_is_exact(self, transformer):
        original = (
            "Contact สมชาย ใจดี at somchai@example.com or 081-234-5678; "
            "backup somchai@example.com and card 4111 1111 1111 1111."
        )
        actions = [
            _mask_action(original, "สมชาย ใจดี", "thai_name"),
            _mask_action(original, "somchai@example.com", "email"),
            _mask_action(original, "081-234-5678", "phone"),
            _mask_action(original, "4111 1111 1111 1111", "credit_card"),
        ]
        # Second occurrence of the email needs its own span.
        second = original.rindex("somchai@example.com")
        actions.append(
            MatchAction(
                match=DetectionMatch(
                    detector_name="email",
                    entity_type="email",
                    category="pii",
                    start=second,
                    end=second + len("somchai@example.com"),
                    matched_text="somchai@example.com",
                    severity=SensitivityLevel.MEDIUM,
                ),
                action=EnforcementAction.MASK,
                reason_code="test",
                reason_message="test",
            )
        )

        masked, span_map, _counters = transformer.mask_text(original, actions)
        assert masked != original
        assert "somchai@example.com" not in masked
        assert "081-234-5678" not in masked

        assert transformer.restore(masked, span_map) == original

    def test_round_trip_when_original_contains_token_shaped_text(
        self, transformer
    ):
        """A user whose ORIGINAL text contains a token-shaped literal
        must get it back verbatim after mask → restore. (``<rZZZ>``
        matches the mask-token regex but can never be minted: counter
        tokens are numeric and deterministic suffixes are 8 chars.)"""
        original = "note <rZZZ> is literal; mail me: alice@example.com"
        actions = [_mask_action(original, "alice@example.com", "email")]
        masked, span_map, _ = transformer.mask_text(original, actions)
        assert transformer.restore(masked, span_map) == original
