"""Tests for DLP system-prompt injection (mask-token explanation)."""

from app.schemas.common import Message
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.transformer import DLPTransformer
from app.services.dlp_prompt import (
    DLP_PLACEHOLDER_PROMPT,
    has_mask_tokens,
    inject_dlp_system_prompt,
)


class TestHasMaskTokens:
    def test_detects_single_token(self):
        msgs = [Message(role="user", content="Hello [NAME_0]")]
        assert has_mask_tokens(msgs) is True

    def test_detects_multiple_tokens(self):
        msgs = [Message(role="user", content="[NAME_0] email [EMAIL_0] phone [PHONE_NUMBER_0]")]
        assert has_mask_tokens(msgs) is True

    def test_clean_text_returns_false(self):
        msgs = [Message(role="user", content="What is the capital of Thailand?")]
        assert has_mask_tokens(msgs) is False

    def test_empty_messages(self):
        assert has_mask_tokens([]) is False

    def test_system_message_with_token(self):
        msgs = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Help [NAME_0] with their order"),
        ]
        assert has_mask_tokens(msgs) is True

    def test_bracket_text_without_index_ignored(self):
        msgs = [Message(role="user", content="Use [PLACEHOLDER] in your code")]
        assert has_mask_tokens(msgs) is False


class TestInjectDlpSystemPrompt:
    def test_injected_when_masked(self):
        msgs = [Message(role="user", content="Hello [NAME_0]")]
        result = inject_dlp_system_prompt(msgs)
        assert len(result) == 2
        assert result[0].role == "system"
        assert DLP_PLACEHOLDER_PROMPT in result[0].content
        assert result[1].content == "Hello [NAME_0]"

    def test_not_injected_when_clean(self):
        msgs = [Message(role="user", content="Hello world")]
        result = inject_dlp_system_prompt(msgs)
        assert result is msgs
        assert len(result) == 1

    def test_existing_system_prompt_preserved(self):
        msgs = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Help [NAME_0]"),
        ]
        result = inject_dlp_system_prompt(msgs)
        assert len(result) == 2
        assert result[0].role == "system"
        assert result[0].content.startswith(DLP_PLACEHOLDER_PROMPT)
        assert "You are a helpful assistant." in result[0].content

    def test_multiple_tokens_single_injection(self):
        msgs = [
            Message(role="user", content="[NAME_0] email [EMAIL_0] phone [PHONE_NUMBER_0]"),
        ]
        result = inject_dlp_system_prompt(msgs)
        assert len(result) == 2
        system_count = sum(1 for m in result if m.role == "system")
        assert system_count == 1

    def test_original_list_not_mutated(self):
        msgs = [Message(role="user", content="Hello [NAME_0]")]
        original_len = len(msgs)
        inject_dlp_system_prompt(msgs)
        assert len(msgs) == original_len

    def test_no_mutation_when_clean(self):
        msgs = [Message(role="user", content="Hello")]
        result = inject_dlp_system_prompt(msgs)
        assert result is msgs


class TestRedactedRuleInPrompt:
    """Verify the [REDACTED] critical rule is present in the system prompt.

    Locks in the post-diagnosis fix that tells the model to use [REDACTED]
    (rather than invent new bracketed tokens) for sensitive-looking
    content the gateway didn't already mask. A regression here would
    re-open the orphan-token hallucination behavior observed in
    sran-pii-test scenarios 2 + 5.
    """

    def test_critical_rule_present(self):
        assert "[REDACTED]" in DLP_PLACEHOLDER_PROMPT
        assert "Critical rule" in DLP_PLACEHOLDER_PROMPT

    def test_explicitly_forbids_new_tokens(self):
        # Must instruct the model NOT to invent placeholder tokens.
        # Phrasing can change; the negation must remain.
        lower = DLP_PLACEHOLDER_PROMPT.lower()
        assert "do not invent" in lower or "do not echo" in lower


class TestOrphanTokenPassthrough:
    """Document current behavior: hallucinated tokens reach user as literal.

    ``restore()`` only replaces tokens that are in ``span_map``. Anything
    else (orphans / hallucinated tokens) passes through unchanged and
    triggers the ``dlp_orphan_mask_tokens`` warning. A future "strip
    orphans" change would break this test on purpose — that's the
    point of the assertion.
    """

    def test_orphan_token_passes_through_unchanged(self):
        transformer = DLPTransformer()
        span_map = {"[EMAIL_0]": "alice@example.com"}
        text_from_provider = "Sent to [EMAIL_0] and also [PHONE_NUMBER_99]"
        result = transformer.restore(text_from_provider, span_map)
        # Mapped token restored.
        assert "alice@example.com" in result
        assert "[EMAIL_0]" not in result
        # Orphan token survives as a literal.
        assert "[PHONE_NUMBER_99]" in result

    def test_orphan_warning_fires_in_logs(self, caplog):
        # The orphan path emits ``dlp_orphan_mask_tokens`` at WARNING
        # level. Operators rely on this log line to spot stale-history
        # replays and provider-side hallucinations.
        import logging
        transformer = DLPTransformer()
        with caplog.at_level(logging.WARNING):
            transformer.restore(
                "[EMAIL_0] and [PHONE_NUMBER_99]",
                {"[EMAIL_0]": "x@y.z"},
            )
        assert any("dlp_orphan_mask_tokens" in r.message for r in caplog.records)


class TestSystemMessageScanGate:
    """``scan_system_messages`` flag on DetectorRegistry.scan_messages.

    Default False — system prompts (operator-authored, e.g. "Reply in
    Thai.") should NOT be scanned. NER on en_core_web_sm classifies
    "Thai" as a PERSON / NRP entity, which used to bind ``[NAME_0]``
    to the literal "Thai" and pollute every reply with "Dear Thai,".
    Tenants with PII in system prompts can opt back in.
    """

    def test_system_message_not_scanned_by_default(self):
        registry = get_default_registry()
        messages = [
            Message(role="system", content="You are a helpful assistant. Reply in Thai."),
            Message(role="user", content="Email me at alice@example.com"),
        ]
        result = registry.scan_messages(messages, direction="input")
        # Only the user message should produce findings — the email
        # in scenario 5 / 2 reproduction. The literal "Thai" from the
        # system prompt must NOT show up as a NAME / NRP finding.
        emails = [m for m in result.matches if m.detector_name == "email"]
        assert len(emails) >= 1, "expected email detector to fire on user message"
        # No finding may originate from the system message — every
        # match's start offset must be valid against the user message,
        # not the system one. Easiest invariant: no matched_text equals
        # the literal "Thai" (the historical pollution path).
        thai_name_hits = [m for m in result.matches if m.matched_text == "Thai"]
        assert thai_name_hits == [], (
            "system-prompt 'Thai' leaked into matches: "
            f"{[(m.detector_name, m.matched_text) for m in thai_name_hits]}"
        )

    def test_system_message_scanned_when_enabled(self):
        # Opt-in: a tenant whose system prompt legitimately contains
        # PII can flip the flag on their PolicyProfile and recover the
        # pre-fix behavior.
        registry = get_default_registry()
        messages = [
            Message(role="system", content="The customer is contactable at john@acme.com."),
            Message(role="user", content="What's my account status?"),
        ]
        result_off = registry.scan_messages(
            messages, direction="input", scan_system_messages=False,
        )
        result_on = registry.scan_messages(
            messages, direction="input", scan_system_messages=True,
        )
        # Off: no findings (user message has no PII).
        emails_off = [m for m in result_off.matches if m.detector_name == "email"]
        assert emails_off == []
        # On: the system-prompt email is detected.
        emails_on = [m for m in result_on.matches if m.detector_name == "email"]
        assert any(m.matched_text == "john@acme.com" for m in emails_on)
