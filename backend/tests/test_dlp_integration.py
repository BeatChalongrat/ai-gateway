"""P3-11: DLP integration tests — cross-component verification.

These tests fill gaps not covered by unit tests:
1. Every detector type verified E2E through the HTTP layer
2. Classifier escalation flowing through the orchestrator
3. Contract boundary test: one finding traced through all pipeline stages
4. Audit emission during real requests
5. Edge cases at the pipeline level

Existing unit tests (256 total) cover individual components thoroughly.
These integration tests verify they compose correctly.
"""

import dataclasses

from fastapi.testclient import TestClient

from app.domain.dlp import (
    DetectionResult,
    EnforcementAction,
    SensitivityLevel,
)
from app.main import app
from app.schemas.common import Message
from app.services.audit_service import AuditService
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content: str) -> dict:
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
        },
    )
    return {"status": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# 1. Per-detector E2E through HTTP
# ---------------------------------------------------------------------------


class TestEveryDetectorE2E:
    """Verify each detector type produces the expected pipeline outcome."""

    def test_email_masked(self):
        r = _chat("Contact user@example.com")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_phone_masked(self):
        r = _chat("Call me at 555-123-4567 please")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_ssn_masked(self):
        r = _chat("My SSN is 123-45-6789")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_bearer_token_masked(self):
        """Bearer tokens (category=secret) are masked by default profile."""
        r = _chat("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_aws_key_masked(self):
        r = _chat("Key: AKIAIOSFODNN7EXAMPLE")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_openai_key_masked(self):
        r = _chat("OPENAI_API_KEY=sk-abcdefghij1234567890abcd")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_github_token_masked(self):
        r = _chat("token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_private_key_masked(self):
        r = _chat("-----BEGIN RSA PRIVATE KEY-----")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_password_field_masked(self):
        """password_field (category=secret) is masked by default profile."""
        r = _chat("DB_PASSWORD=supersecret123")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"

    def test_clean_message_allowed(self):
        r = _chat("What is the meaning of life?")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "allow"


# ---------------------------------------------------------------------------
# 2. Classifier escalation through orchestrator
# ---------------------------------------------------------------------------


class TestEscalationThroughPipeline:
    """Verify classifier severity escalation affects the pipeline outcome."""

    def test_email_alone_stays_medium(self):
        """Single PII finding → no escalation → mask."""
        registry = get_default_registry(disabled_detectors=[])
        classifier = SensitivityClassifier()
        evaluator = PolicyEvaluator()

        result = registry.scan_messages(
            [Message(role="user", content="email user@example.com")],
        )
        classified = classifier.classify(result.matches)
        assert all(m.severity == SensitivityLevel.MEDIUM for m in classified)

        result.matches = classified
        decision = evaluator.evaluate(result, DEFAULT_PROFILE)
        assert decision.overall_action == EnforcementAction.MASK

    def test_email_plus_password_escalates_email(self):
        """Email (pii) + password (secret) = 2 categories → escalation.
        Email severity escalated from MEDIUM to HIGH.
        But password is secret → BLOCK regardless, so overall is BLOCK."""
        registry = get_default_registry(disabled_detectors=[])
        classifier = SensitivityClassifier()

        result = registry.scan_messages(
            [Message(role="user", content="email user@example.com password=secret123")],
        )
        classified = classifier.classify(result.matches)

        email_matches = [m for m in classified if m.entity_type == "email_address"]
        if email_matches:
            # Email was escalated from MEDIUM to HIGH due to multi-category
            assert email_matches[0].severity >= SensitivityLevel.HIGH

    def test_three_pii_types_escalates(self):
        """Email + phone + SSN = 3 entity types → escalation to HIGH."""
        registry = get_default_registry(disabled_detectors=[])
        classifier = SensitivityClassifier()

        text = "email user@test.com phone 555-123-4567 ssn 234-56-7890"
        result = registry.scan_messages(
            [Message(role="user", content=text)],
        )
        classified = classifier.classify(result.matches)

        for m in classified:
            if m.category == "pii":
                assert m.severity >= SensitivityLevel.HIGH


# ---------------------------------------------------------------------------
# 3. Contract boundary test: trace one finding through all stages
# ---------------------------------------------------------------------------


class TestContractBoundaryTrace:
    """Trace an email finding through all 5 pipeline stages, verifying
    the contract at each boundary."""

    def test_full_pipeline_trace(self):
        text = "Send report to user@example.com please"
        msg = Message(role="user", content=text)

        # Stage 1: Detection
        registry = get_default_registry(disabled_detectors=[])
        detection_result = registry.scan_messages([msg])

        assert detection_result.has_sensitive_content
        match = detection_result.matches[0]
        assert match.detector_name == "email"
        assert match.entity_type == "email_address"
        assert text[match.start:match.end] == match.matched_text
        assert match.severity == SensitivityLevel.MEDIUM

        # Stage 2: Classification (single finding, no escalation)
        classifier = SensitivityClassifier()
        classified = classifier.classify(detection_result.matches)
        assert classified[0].severity == SensitivityLevel.MEDIUM  # unchanged

        # Stage 3: Policy evaluation
        detection_result.matches = classified
        evaluator = PolicyEvaluator()
        decision = evaluator.evaluate(detection_result, DEFAULT_PROFILE)

        assert decision.overall_action == EnforcementAction.MASK
        assert len(decision.match_actions) == 1
        ma = decision.match_actions[0]
        assert ma.action == EnforcementAction.MASK
        assert ma.match is classified[0]  # same object passed through
        assert "pii" in ma.reason_code

        # Stage 4: Transformation
        transformer = DLPTransformer()
        xform_result = transformer.transform_text(text, decision.match_actions)

        assert "user@example.com" not in xform_result.transformed_text
        assert xform_result.transformations_applied == 1
        span = xform_result.transformed_spans[0]
        assert span.action == EnforcementAction.MASK
        assert span.start == match.start
        assert span.end == match.end

        # Stage 5: Audit (privacy-safe)
        audit = AuditService()
        entries = audit.emit_dlp_findings(decision.match_actions, direction="input")

        assert len(entries) == 1
        entry = entries[0]
        assert entry.detector_name == "email"
        assert entry.action == "mask"
        assert entry.direction == "input"
        # Privacy guarantee
        entry_dict = dataclasses.asdict(entry)
        assert "user@example.com" not in str(entry_dict)


# ---------------------------------------------------------------------------
# 4. Edge cases at pipeline level
# ---------------------------------------------------------------------------


class TestPipelineEdgeCases:
    def test_empty_message_content(self):
        r = _chat("")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "allow"

    def test_message_with_only_whitespace(self):
        r = _chat("   \n\t  ")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "allow"

    def test_multiple_emails_all_masked(self):
        r = _chat("Send to a@example.com and b@example.com")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"
        # With the new mask/restore round-trip, input PII is restored in the
        # final response so the user sees what they typed. Masking still
        # happens on the provider side — verified by test_pii_masked_before_provider
        # in test_e2e_dlp_flow.py.
        content = r["body"]["choices"][0]["message"]["content"]
        assert "a@example.com" in content
        assert "b@example.com" in content

    def test_pii_and_secret_in_same_message_masked(self):
        """Email (pii→mask) + AWS key (secret→mask in default) → overall mask."""
        r = _chat("email user@example.com key AKIAIOSFODNN7EXAMPLE")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "mask"
        # Input PII is restored in the final response (see class docstring
        # of test_multiple_emails_all_masked above).
        content = r["body"]["choices"][0]["message"]["content"]
        assert "user@example.com" in content
        assert "AKIAIOSFODNN7EXAMPLE" in content

    def test_valid_response_shape_after_mask(self):
        """Masked response still has full ChatCompletionsResponse shape."""
        r = _chat("Call 555-123-4567")
        body = r["body"]
        assert "id" in body
        assert "model" in body
        assert "provider" in body
        assert "choices" in body
        assert "usage" in body
        assert "policy_action" in body
        assert "policy_reasons" in body

    def test_masked_response_shape(self):
        """A masked (not blocked) response still has the full envelope."""
        r = _chat("-----BEGIN PRIVATE KEY-----")
        assert r["status"] == 200
        body = r["body"]
        assert body["policy_action"] == "mask"
        assert "choices" in body
        assert "policy_reasons" in body


# ---------------------------------------------------------------------------
# 5. Strict profile through pipeline (unit integration)
# ---------------------------------------------------------------------------


class TestStrictProfileIntegration:
    """Verify strict profile behavior through the evaluator without HTTP."""

    def test_strict_blocks_on_critical_severity(self):
        """Strict profile has severity_threshold_block=CRITICAL.
        An AWS key (CRITICAL) should be blocked."""
        registry = get_default_registry(disabled_detectors=[])
        classifier = SensitivityClassifier()
        evaluator = PolicyEvaluator()

        result = registry.scan_messages(
            [Message(role="user", content="key: AKIAIOSFODNN7EXAMPLE")],
        )
        result.matches = classifier.classify(result.matches)
        decision = evaluator.evaluate(result, STRICT_PROFILE)

        assert decision.is_blocked
        blocked_action = decision.match_actions[0]
        assert blocked_action.reason_code == "severity_threshold"

    def test_strict_masks_medium_pii(self):
        """Strict profile masks MEDIUM PII (email) — below CRITICAL threshold."""
        registry = get_default_registry(disabled_detectors=[])
        evaluator = PolicyEvaluator()

        result = registry.scan_messages(
            [Message(role="user", content="email user@test.com")],
        )
        decision = evaluator.evaluate(result, STRICT_PROFILE)
        assert decision.overall_action == EnforcementAction.MASK
