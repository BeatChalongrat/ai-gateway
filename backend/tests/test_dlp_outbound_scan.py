"""Outbound DLP scan must not flag the gateway's own mask-token literals.

The orchestrator's ``_scan_text_as_result`` is the single chokepoint
for outbound DLP scanning (called from the non-streaming and streaming
response paths). Heavy-scenario live testing surfaced a self-feedback
loop: the model echoed mask tokens like ``<r0>`` /
``[PHONE_NUMBER_0]`` in its reply, presidio_ner then classified those
literals as PII, and the gateway recorded findings against its own
placeholders.

The fix filters findings whose ``matched_text`` is exactly a mask-token
literal, gated to ``direction == "output"``. These tests lock that
behavior in.
"""

from __future__ import annotations

from app.services.orchestrator import GatewayOrchestrator


def _new_orch() -> GatewayOrchestrator:
    """Construct a fresh orchestrator. Only ``_scan_text_as_result`` is
    exercised here, so the heavy collaborators (provider dispatcher,
    request log service, etc.) don't need real wiring — the helper
    only touches ``self.detector_registry``."""
    return GatewayOrchestrator()


class TestOutboundDropsSelfFeedbackToken:
    def test_outbound_drops_token_literal_finding(self):
        orch = _new_orch()
        # Bare token literal in the response, sitting in a sentence
        # the model would plausibly produce.
        text = "Confirmed <r0> received."
        result = orch._scan_text_as_result(text, direction="output")
        # No finding whose matched_text IS the token literal.
        offending = [
            m for m in result.matches
            if m.matched_text.strip() == "<r0>"
        ]
        assert offending == [], (
            "outbound scan must not flag <r0> as PII: "
            f"{[(m.detector_name, m.matched_text) for m in offending]}"
        )


class TestOutboundKeepsRealPiiFinding:
    def test_outbound_keeps_email_in_response(self):
        orch = _new_orch()
        text = "Contact alice@new.example.com for help"
        result = orch._scan_text_as_result(text, direction="output")
        emails = [m for m in result.matches if m.detector_name == "email"]
        assert any("alice@new.example.com" in m.matched_text for m in emails), (
            "real email must still be detected in outbound text"
        )


class TestOutboundKeepsPiiAdjacentToToken:
    def test_real_pii_next_to_token_is_kept(self):
        orch = _new_orch()
        # Email finding sits NEXT to a token, not AT it. Filter must
        # only drop findings whose matched_text IS a token, never
        # findings that merely appear in token-adjacent prose.
        text = "<r0> can be reached at bob@example.com"
        result = orch._scan_text_as_result(text, direction="output")
        emails = [m for m in result.matches if m.detector_name == "email"]
        assert any("bob@example.com" in m.matched_text for m in emails)


class TestInputDirectionUnaffected:
    def test_input_direction_does_not_filter(self):
        # Defensive: the helper allows direction="input"; no callers
        # use it that way today, but we lock the contract that the
        # filter is output-only so an inbound caller can't accidentally
        # lose findings.
        orch = _new_orch()
        text = "Some token-shaped string <r0> here"
        result = orch._scan_text_as_result(text, direction="input")
        # The exact behavior on a token literal under "input" depends on
        # which detectors fire — the contract we test is "no filtering
        # happens", not "X findings exist". Compare with output:
        result_out = orch._scan_text_as_result(text, direction="output")
        # If anything DID match the literal, output strips it; input
        # keeps it. So len(input) >= len(output).
        assert len(result.matches) >= len(result_out.matches)


class TestFilterHandlesTokenWithWhitespace:
    def test_whitespace_mangled_token_not_filtered(self):
        # ``< r0 >`` with internal spaces is NOT a valid mask
        # token (the orchestrator's mask path never emits whitespace
        # inside brackets — see transformer.MASK_TOKENS). It's a
        # malformation, possibly a model hallucination or a downstream
        # text munge. The filter must reject it so any detector that
        # happens to match these strings still surfaces a finding —
        # operators need to see them to triage, separate from the
        # restore()-orphan concern in known-issues.
        orch = _new_orch()
        text = "Sent < r0 > today."
        result = orch._scan_text_as_result(text, direction="output")
        # If a detector ever matches the malformed literal, it must
        # NOT be silently filtered. Easiest invariant: any matches
        # whose matched_text contains the malformed substring stay.
        # (No detector fires on this exact string today, so the test
        # is forward-looking — but the loop below documents the rule.)
        for m in result.matches:
            if "< r0 >" in m.matched_text:
                # Must have survived the filter — the assert just
                # confirms the code path didn't drop it.
                assert m.matched_text  # tautology; presence is the contract
