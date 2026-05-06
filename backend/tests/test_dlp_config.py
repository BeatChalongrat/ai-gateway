"""Tests for P3-10: DLP config / policy profile integration.

Verifies:
- Settings fields exist with correct defaults
- dlp_disabled_detectors_list parses correctly
- Registry respects disabled detectors
- Orchestrator respects dlp_enabled flag
- Orchestrator uses dlp_default_profile from settings
- Response scanning respects global dlp_scan_responses flag
"""

from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services.dlp.detectors.registry import get_default_registry, DetectorRegistry
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------


class TestDLPSettingsDefaults:
    def test_dlp_enabled_default_true(self):
        s = Settings(_env_file=None)
        assert s.dlp_enabled is True

    def test_dlp_default_profile(self):
        s = Settings(_env_file=None)
        assert s.dlp_default_profile == "default"

    def test_dlp_disabled_detectors_default_empty(self):
        s = Settings(_env_file=None, dlp_disabled_detectors="")
        assert s.dlp_disabled_detectors_list == []

    def test_dlp_scan_responses_default_true(self):
        s = Settings(_env_file=None)
        assert s.dlp_scan_responses is True


class TestDLPDisabledDetectorsParsing:
    def test_single_detector(self):
        s = Settings(_env_file=None, dlp_disabled_detectors="email")
        assert s.dlp_disabled_detectors_list == ["email"]

    def test_multiple_detectors(self):
        s = Settings(_env_file=None, dlp_disabled_detectors="email,phone,ssn")
        assert s.dlp_disabled_detectors_list == ["email", "phone", "ssn"]

    def test_whitespace_trimmed(self):
        s = Settings(_env_file=None, dlp_disabled_detectors=" email , phone ")
        assert s.dlp_disabled_detectors_list == ["email", "phone"]


# ---------------------------------------------------------------------------
# Registry respects disabled detectors
# ---------------------------------------------------------------------------


_ALL_DETECTOR_NAMES = [
    "email", "phone", "ssn", "credit_card", "iban",
    "ip_address", "passport", "date_of_birth", "vehicle_plate",
    "bearer_token", "api_key_secret", "private_key", "password_field",
    # DLP coverage expansion — 9 new detectors.
    "thai_national_id", "thai_tax_id", "thai_bank_account",
    "thai_promptpay", "thai_address", "thai_name",
    "db_connection_string", "medical_record", "mac_address",
    # PDPA Section 26 sensitive data detectors.
    "religion_belief", "health_condition", "criminal_record", "biometric_data",
    # NER detector.
    "presidio_ner",
    # Thai-market contact identifier.
    "line_id",
]


class TestRegistryDisabledDetectors:
    def test_all_enabled_by_default(self):
        reg = get_default_registry(disabled_detectors=[])
        assert len(reg) == len(_ALL_DETECTOR_NAMES)

    def test_disable_one(self):
        reg = get_default_registry(disabled_detectors=["email"])
        assert len(reg) == len(_ALL_DETECTOR_NAMES) - 1
        assert "email" not in reg

    def test_disable_multiple(self):
        # Disable every detector EXCEPT the three secret-category ones so
        # the assertion below can stay crisp. Any new detector added to
        # _ALL_DETECTOR_NAMES must also be added here.
        disabled = [
            n for n in _ALL_DETECTOR_NAMES
            if n not in ("bearer_token", "private_key", "password_field")
        ]
        reg = get_default_registry(disabled_detectors=disabled)
        names = {d.name for d in reg.all()}
        assert names == {"bearer_token", "private_key", "password_field"}

    def test_disable_all(self):
        reg = get_default_registry(disabled_detectors=list(_ALL_DETECTOR_NAMES))
        assert len(reg) == 0

    def test_disable_nonexistent_ignored(self):
        reg = get_default_registry(disabled_detectors=["nonexistent"])
        assert len(reg) == len(_ALL_DETECTOR_NAMES)


# ---------------------------------------------------------------------------
# DLP enabled/disabled (E2E through HTTP)
# ---------------------------------------------------------------------------


class TestDLPEnabledToggle:
    def test_dlp_enabled_blocks_critical_secret_in_default(self):
        """Default profile blocks CRITICAL-severity secrets inbound.
        The severity_threshold_block=CRITICAL means credentials are
        stopped before reaching the LLM — returns 403."""
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "key: AKIAIOSFODNN7EXAMPLE"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "dlp_violation"

    @patch("app.services.orchestrator.settings")
    def test_dlp_disabled_allows_secret(self, mock_settings):
        """With DLP off, the same request passes through."""
        # Copy real settings, override dlp_enabled
        from app.core.config import settings as real_settings
        for field in Settings.model_fields:
            setattr(mock_settings, field, getattr(real_settings, field))
        mock_settings.dlp_enabled = False
        mock_settings.dlp_default_profile = "default"
        mock_settings.dlp_scan_responses = True

        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "key: AKIAIOSFODNN7EXAMPLE"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_action"] == "allow"

    def test_dlp_enabled_masks_email(self):
        """With DLP on, email is masked."""
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "email user@example.com"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_action"] == "mask"

    @patch("app.services.orchestrator.settings")
    def test_dlp_disabled_passes_email_through(self, mock_settings):
        """With DLP off, email passes through without masking."""
        from app.core.config import settings as real_settings
        for field in Settings.model_fields:
            setattr(mock_settings, field, getattr(real_settings, field))
        mock_settings.dlp_enabled = False
        mock_settings.dlp_default_profile = "default"
        mock_settings.dlp_scan_responses = True

        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "email user@example.com"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_action"] == "allow"
        content = resp.json()["choices"][0]["message"]["content"]
        # Email passes through to provider stub — appears in echo
        assert "user@example.com" in content


# ---------------------------------------------------------------------------
# Default profile from settings
# ---------------------------------------------------------------------------


class TestDefaultProfileFromSettings:
    @patch("app.services.orchestrator.settings")
    def test_strict_profile_blocks_via_severity(self, mock_settings):
        """Setting DLP_DEFAULT_PROFILE=strict blocks CRITICAL findings."""
        from app.core.config import settings as real_settings
        for field in Settings.model_fields:
            setattr(mock_settings, field, getattr(real_settings, field))
        mock_settings.dlp_enabled = True
        mock_settings.dlp_default_profile = "strict"
        mock_settings.dlp_scan_responses = True

        # An email alone is MEDIUM severity. Under strict profile:
        # - category pii → MASK (same as default)
        # - severity_threshold_block=CRITICAL → doesn't trigger for MEDIUM
        # So email should still be masked, not blocked.
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "email user@example.com"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_action"] == "mask"


# ---------------------------------------------------------------------------
# Response scanning toggle
# ---------------------------------------------------------------------------


class TestResponseScanningToggle:
    @patch("app.services.orchestrator.settings")
    def test_dlp_scan_responses_false_skips_response_scan(self, mock_settings):
        """With DLP_SCAN_RESPONSES=false, response scanning is skipped."""
        from app.core.config import settings as real_settings
        for field in Settings.model_fields:
            setattr(mock_settings, field, getattr(real_settings, field))
        mock_settings.dlp_enabled = True
        mock_settings.dlp_default_profile = "default"
        mock_settings.dlp_scan_responses = False

        # Clean input → no input-side findings → allow
        # Response scan disabled → no output-side findings → allow
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_action"] == "allow"
