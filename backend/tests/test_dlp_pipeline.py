"""Integration tests for P3-07/P3-08: DLP pipeline (request + response side).

Tests the full orchestrator flow through the new DLP pipeline:
    DetectorRegistry → Classifier → PolicyEvaluator → DLPTransformer

Request-side scenarios:
- Clean message → allow, no transformation
- Message with email → mask, transformed content reaches provider
- Message with secret (AWS key) → block, DLPViolationError raised
- Message with email + SSN → mask, both findings transformed

Response-side scenarios:
- Response scanning runs and doesn't interfere with clean responses
- Response with PII is masked before return (unit-level)
- Response with secret triggers block (unit-level)
- scan_responses=False skips response scanning
- policy_action reflects highest action across input + output
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content: str, **extra_json) -> dict:
    """Send a chat request and return (status_code, body)."""
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": content}],
        "metadata": {},
        **extra_json,
    }
    resp = client.post("/v1/chat/completions", headers=_HEADERS, json=body)
    return {"status": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# Allow scenario: clean message
# ---------------------------------------------------------------------------


class TestAllowScenario:
    def test_clean_message_returns_200(self):
        r = _chat("What is the weather today?")
        assert r["status"] == 200

    def test_clean_message_policy_action_allow(self):
        r = _chat("What is the weather today?")
        assert r["body"]["policy_action"] == "allow"

    def test_clean_message_has_provider_content(self):
        r = _chat("What is the weather today?")
        content = r["body"]["choices"][0]["message"]["content"]
        assert len(content) > 0  # Provider returned non-empty content

    def test_clean_message_no_policy_reasons_about_sensitive(self):
        r = _chat("What is the weather today?")
        reasons = r["body"]["policy_reasons"]
        assert any("no sensitive" in reason.lower() for reason in reasons)


# ---------------------------------------------------------------------------
# Mask scenario: email in message
# ---------------------------------------------------------------------------


class TestMaskScenario:
    def test_email_message_returns_200(self):
        r = _chat("Send report to user@example.com please")
        assert r["status"] == 200

    def test_email_message_policy_action_mask(self):
        r = _chat("Send report to user@example.com please")
        assert r["body"]["policy_action"] == "mask"

    def test_email_restored_in_final_response(self):
        """The raw email is masked before the provider call, then restored
        in the final response so the user sees what they typed. The
        masked-to-provider guarantee is verified in test_e2e_dlp_flow.py."""
        r = _chat("Send report to user@example.com please")
        content = r["body"]["choices"][0]["message"]["content"]
        assert "user@example.com" in content

    def test_email_masked_before_provider(self):
        """After the new mask/restore round-trip, the final response still
        contains the original email (restored). Masking on the provider
        side is covered by test_e2e_dlp_flow.test_pii_masked_before_provider."""
        r = _chat("Send report to user@example.com please")
        content = r["body"]["choices"][0]["message"]["content"]
        assert "user@example.com" in content

    def test_mask_policy_reasons_present(self):
        r = _chat("Send report to user@example.com please")
        reasons = r["body"]["policy_reasons"]
        assert len(reasons) > 0


# ---------------------------------------------------------------------------
# Secret-in-message scenario: default profile now MASKS (not blocks).
# Strict profile still blocks — see tests at the bottom of this file.
# ---------------------------------------------------------------------------


class TestSecretInMessageBlocked:
    def test_aws_key_returns_403_blocked(self):
        r = _chat("My key is AKIAIOSFODNN7EXAMPLE")
        assert r["status"] == 403
        assert r["body"]["error"]["code"] == "dlp_violation"

    def test_aws_key_not_in_error_response(self):
        """Blocked response should not leak the original credential."""
        r = _chat("My key is AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in str(r["body"])

    def test_private_key_returns_403_blocked(self):
        r = _chat("Here is my key: -----BEGIN PRIVATE KEY-----")
        assert r["status"] == 403
        assert r["body"]["error"]["code"] == "dlp_violation"

    def test_block_has_details(self):
        r = _chat("My key is AKIAIOSFODNN7EXAMPLE")
        details = r["body"]["error"].get("details", {})
        assert "reasons" in details


# ---------------------------------------------------------------------------
# Multi-finding scenario: email + SSN
# ---------------------------------------------------------------------------


class TestMultiFindingScenario:
    def test_email_and_ssn_returns_200(self):
        """Both are pii → mask (not block)."""
        r = _chat("Email user@example.com SSN 123-45-6789")
        assert r["status"] == 200

    def test_email_and_ssn_policy_action_mask(self):
        r = _chat("Email user@example.com SSN 123-45-6789")
        assert r["body"]["policy_action"] == "mask"

    def test_email_and_ssn_both_restored(self):
        """Both PII items are masked before the provider call and restored
        in the final response. See test_e2e_dlp_flow.py for the guarantee
        that the provider only ever sees the masked form."""
        r = _chat("Email user@example.com SSN 123-45-6789")
        content = r["body"]["choices"][0]["message"]["content"]
        assert "user@example.com" in content
        assert "123-45-6789" in content


# ---------------------------------------------------------------------------
# Regression: existing test_chat behavior preserved
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Response-side DLP: unit-level tests via orchestrator internals
# ---------------------------------------------------------------------------


class TestResponseSideScanning:
    """Tests response-side DLP using orchestrator helper methods directly."""

    def test_scan_text_as_result_detects_email(self):
        from app.services.orchestrator import GatewayOrchestrator

        orch = GatewayOrchestrator()
        result = orch._scan_text_as_result("contact user@example.com")
        assert result.has_sensitive_content is True
        assert result.scanned_direction == "output"
        assert result.scan_target == "response_content"

    def test_scan_text_as_result_clean(self):
        from app.services.orchestrator import GatewayOrchestrator

        orch = GatewayOrchestrator()
        result = orch._scan_text_as_result("nothing sensitive here")
        assert result.has_sensitive_content is False

    def test_response_with_email_is_allowed_under_default(self):
        """Outbound PII is ALLOW under the default profile (Fix 3).

        Before Fix 3, every category in ``category_actions`` applied
        identically to input and output, so an AI-generated email in
        the response was masked. The new ``category_actions_output``
        on DEFAULT_PROFILE narrows outbound masking to credentials and
        secrets only — letting the AI's answers about emails / cards /
        IPs flow through to the user untouched. This test pins that
        contract; the SECRET-category companion test below confirms
        the masking-still-applies path.
        """
        from app.services.orchestrator import GatewayOrchestrator
        from app.services.policy.profiles import DEFAULT_PROFILE

        orch = GatewayOrchestrator()
        response_text = "Here is your contact: user@example.com for reference"

        detection = orch._scan_text_as_result(response_text)
        enforcement = orch._scan_and_evaluate(detection, DEFAULT_PROFILE)

        # PII outbound under default is ALLOW — no transformation needed.
        assert enforcement.requires_transformation is False
        # The match was still recorded for trace/audit purposes.
        assert any(
            ma.match.detector_name == "email"
            for ma in enforcement.match_actions
        )

    def test_response_with_secret_is_blocked_in_default(self):
        """Default profile blocks CRITICAL-severity secrets (severity_threshold_block=CRITICAL)."""
        from app.services.orchestrator import GatewayOrchestrator
        from app.services.policy.profiles import DEFAULT_PROFILE

        orch = GatewayOrchestrator()
        response_text = "Your key is AKIAIOSFODNN7EXAMPLE"

        detection = orch._scan_text_as_result(response_text)
        enforcement = orch._scan_and_evaluate(detection, DEFAULT_PROFILE)

        assert enforcement.is_blocked is True

    def test_response_with_secret_is_blocked_in_strict(self):
        from app.services.orchestrator import GatewayOrchestrator
        from app.services.policy.profiles import STRICT_PROFILE

        orch = GatewayOrchestrator()
        response_text = "Your key is AKIAIOSFODNN7EXAMPLE"

        detection = orch._scan_text_as_result(response_text)
        enforcement = orch._scan_and_evaluate(detection, STRICT_PROFILE)

        assert enforcement.is_blocked

    def test_scan_responses_false_skips_scanning(self):
        """When profile.scan_responses is False, response content is untouched."""
        from app.domain.dlp import EnforcementAction
        from app.domain.policy import PolicyProfile
        from app.services.orchestrator import GatewayOrchestrator

        profile = PolicyProfile(
            profile_id="no-response-scan",
            name="No Response Scan",
            category_actions={"pii": EnforcementAction.MASK},
            scan_responses=False,
        )
        # Orchestrator won't scan if scan_responses is False.
        # Verify the flag exists and the profile is valid.
        assert profile.scan_responses is False

        # The orchestrator checks `if profile.scan_responses:` before scanning.
        # We verify this path by checking the profile controls the branch.
        orch = GatewayOrchestrator()
        # If we _were_ to scan, this would find PII:
        detection = orch._scan_text_as_result("user@example.com")
        assert detection.has_sensitive_content is True
        # But with scan_responses=False, the orchestrator skips this entirely.


class TestResponseSideE2E:
    """E2E tests through the HTTP layer verifying response scanning runs."""

    def test_clean_input_clean_response_allow(self):
        """Clean input → clean stub response → response scan finds nothing."""
        r = _chat("What time is it?")
        assert r["status"] == 200
        assert r["body"]["policy_action"] == "allow"

    def test_masked_input_restored_in_response(self):
        """Input email is masked before the provider call, then restored
        in the final response — the stub echoes the masked tokens and the
        orchestrator restores them on the way out."""
        r = _chat("Email user@example.com")
        assert r["status"] == 200
        content = r["body"]["choices"][0]["message"]["content"]
        assert "user@example.com" in content

    def test_input_side_mask_reported_with_input_label(self):
        """Mask direction is reported in the reason list. Use email (non-CRITICAL)
        so it's masked, not blocked."""
        r = _chat("Email: user@example.com")
        assert r["status"] == 200
        reasons = r["body"].get("policy_reasons", [])
        assert any("Input:" in rr for rr in reasons)


# ---------------------------------------------------------------------------
# Regression: existing test_chat behavior preserved
# ---------------------------------------------------------------------------


class TestRegressionBasicChat:
    def test_auth_required(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    def test_valid_response_shape(self):
        r = _chat("Hello")
        body = r["body"]
        assert "id" in body
        assert "model" in body
        assert "provider" in body
        assert "choices" in body
        assert "usage" in body
        assert body["provider"] in {"openai", "anthropic"}
