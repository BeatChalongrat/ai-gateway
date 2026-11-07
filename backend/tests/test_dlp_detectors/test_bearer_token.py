"""Bearer-token / API-key detectors — coverage via raw detection.

Bearer tokens carry severity=CRITICAL. Under the default profile they
are MASKed (category=secret/credential → MASK), so the masking pipeline
produces tokens. Tests below mostly assert the detector fired; the
mask token shape is incidental.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestBearerTokenHappyPath:
    def test_openai_style_key(self):
        # 20+ chars after sk- — fires the bearer_token openai_api_key
        # branch.
        names = detector_names_for("sk-test-1234567890abcdefghij")
        assert "bearer_token" in names

    def test_openai_live_key(self):
        # sk-live-* is api_key_secret (credential), not bearer_token.
        names = detector_names_for("sk-live-abcdef1234567890")
        assert "api_key_secret" in names

    def test_api_key_label_preserved(self):
        # The credential_assignment branch of api_key_secret masks the
        # value; the label survives.
        from ._helpers import has_match
        masked, _, matches = mask("API Key: sk-live-abc12345abcdef")
        assert "API Key:" in masked
        assert has_match(matches, "api_key_secret", "bearer_token", "db_connection_string")


class TestBearerTokenBoundary:
    def test_jwt_bearer_token(self):
        names = detector_names_for(
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.test"
        )
        # JWT shape OR bearer prefix — either detector path fires.
        assert "bearer_token" in names or "api_key_secret" in names

    def test_aws_access_key(self):
        assert "bearer_token" in detector_names_for(
            "AKIAIOSFODNN7EXAMPLE"
        )


class TestBearerTokenNegative:
    def test_bare_word_no_token(self):
        # "Bearer" with no value — must not fire.
        assert "bearer_token" not in detector_names_for("Bearer")
