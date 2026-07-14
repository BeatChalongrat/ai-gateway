"""PR-03 + Phase 2A V2: Startup secret validation tests."""

import pytest

from app.core.config import Settings


# 32+ byte strong values used throughout the "valid" paths below.
_STRONG_JWT = "a-real-strong-secret-with-enough-entropy-12345"
_STRONG_ENC = "another-strong-key-with-enough-entropy-67890abc"
_STRONG_PORTAL = "portal-specific-strong-secret-with-more-than-32-bytes"
_STRONG_SESSION = "session-signing-strong-secret-with-more-than-32-bytes"


def _prod_settings(**overrides):
    """Settings in prod with all security fields defaulted to strong values."""
    defaults = dict(
        _env_file=None,
        postgres_password="x",
        app_env="production",
        jwt_secret=_STRONG_JWT,
        master_encryption_key=_STRONG_ENC,
        portal_jwt_secret=_STRONG_PORTAL,
        session_signing_secrets=_STRONG_SESSION,
        # Phase 3.5 C27 — validate_secrets now also checks
        # RESEND_API_KEY in production. Provide a non-empty value so
        # every pre-existing "happy path" test keeps passing; tests
        # that specifically target the Resend gate override this.
        resend_api_key="re_strong_test_key",
        # Later prod gates (Safe Trace audit key, provider-key master
        # key) — same pattern as the Resend default above: strong values
        # so the happy-path tests keep passing; the gates' own tests
        # override these with empty/weak values.
        dlp_audit_key="a" * 64,
        provider_key_master_key="b" * 64,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestSecretValidation:
    def test_dev_env_allows_insecure(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="development", jwt_secret="change-me",
        )
        s.validate_secrets()  # should not raise

    def test_production_rejects_insecure_jwt(self):
        s = _prod_settings(jwt_secret="change-me")
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            s.validate_secrets()

    def test_production_rejects_insecure_encryption_key(self):
        s = _prod_settings(master_encryption_key="change-me")
        with pytest.raises(RuntimeError, match="MASTER_ENCRYPTION_KEY"):
            s.validate_secrets()

    def test_production_accepts_real_secrets(self):
        s = _prod_settings()
        s.validate_secrets()  # should not raise

    def test_test_env_allows_insecure(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="test", jwt_secret="change-me",
        )
        s.validate_secrets()  # should not raise

    def test_error_message_is_clear(self):
        s = _prod_settings(jwt_secret="change-me", master_encryption_key="change-me")
        with pytest.raises(RuntimeError, match="STARTUP BLOCKED"):
            s.validate_secrets()


class TestLengthFloor:
    """Phase 2A V2: enforce minimum byte length on every secret."""

    def test_short_jwt_rejected_even_if_not_a_known_default(self):
        # "abc" is not in _INSECURE_DEFAULTS but has 3 bytes → too short.
        s = _prod_settings(jwt_secret="abc")
        with pytest.raises(RuntimeError, match="JWT_SECRET is too short"):
            s.validate_secrets()

    def test_short_master_key_rejected(self):
        s = _prod_settings(master_encryption_key="onlyten123")
        with pytest.raises(RuntimeError, match="MASTER_ENCRYPTION_KEY is too short"):
            s.validate_secrets()

    def test_short_portal_jwt_rejected(self):
        s = _prod_settings(portal_jwt_secret="too-short")
        with pytest.raises(RuntimeError, match="PORTAL_JWT_SECRET is too short"):
            s.validate_secrets()

    def test_secret_at_minimum_length_accepted(self):
        # Exactly 32 bytes must pass.
        exactly_32 = "a" * 32
        s = _prod_settings(jwt_secret=exactly_32, master_encryption_key=exactly_32,
                           portal_jwt_secret=exactly_32,
                           session_signing_secrets=exactly_32)
        s.validate_secrets()


class TestSessionSigningSecrets:
    """Phase 2A V2: SESSION_SIGNING_SECRETS validation paths."""

    def test_empty_rejected(self):
        s = _prod_settings(session_signing_secrets="")
        with pytest.raises(RuntimeError, match="SESSION_SIGNING_SECRETS is empty"):
            s.validate_secrets()

    def test_whitespace_only_rejected(self):
        s = _prod_settings(session_signing_secrets="   ,  ,")
        with pytest.raises(RuntimeError, match="SESSION_SIGNING_SECRETS is empty"):
            s.validate_secrets()

    def test_known_default_rejected(self):
        s = _prod_settings(session_signing_secrets="change-me")
        with pytest.raises(
            RuntimeError, match=r"SESSION_SIGNING_SECRETS\[0\] is set to an insecure default"
        ):
            s.validate_secrets()

    def test_short_entry_rejected(self):
        s = _prod_settings(session_signing_secrets="short1,"  + _STRONG_SESSION)
        with pytest.raises(
            RuntimeError, match=r"SESSION_SIGNING_SECRETS\[0\] is too short"
        ):
            s.validate_secrets()

    def test_rotation_list_with_all_strong_entries_accepted(self):
        s = _prod_settings(
            session_signing_secrets=f"{_STRONG_SESSION},{_STRONG_SESSION + '-2'}"
        )
        s.validate_secrets()

    def test_second_entry_weak_also_rejected(self):
        # First entry strong, second entry weak — still a reject.
        s = _prod_settings(session_signing_secrets=f"{_STRONG_SESSION},weak")
        with pytest.raises(
            RuntimeError, match=r"SESSION_SIGNING_SECRETS\[1\] is too short"
        ):
            s.validate_secrets()

    def test_all_issues_reported_together(self):
        # JWT bad, master bad, session empty → all three in one message.
        s = _prod_settings(
            jwt_secret="change-me",
            master_encryption_key="change-me",
            session_signing_secrets="",
        )
        with pytest.raises(RuntimeError) as excinfo:
            s.validate_secrets()
        msg = str(excinfo.value)
        assert "JWT_SECRET" in msg
        assert "MASTER_ENCRYPTION_KEY" in msg
        assert "SESSION_SIGNING_SECRETS is empty" in msg


class TestAuthCookieProdValidation:
    """Phase 11 A2 — cross-origin auth-cookie startup guard.

    web-chat has no Bearer fallback: every authed fetch rides the
    ``portal_*`` HttpOnly cookies. A production deploy in cross-SITE
    cookie mode (SameSite=None) without a shared cookie Domain (or
    without Secure) silently 401s every request, so startup must fail
    LOUDLY instead.
    """

    @staticmethod
    def _settings(**overrides):
        """_prod_settings + strong values for the newer prod-required
        secrets (DLP_AUDIT_KEY / PROVIDER_KEY_MASTER_KEY) that the shared
        helper predates — so the happy paths here isolate the COOKIE
        checks instead of tripping unrelated gates."""
        defaults = dict(
            dlp_audit_key="a" * 64,
            provider_key_master_key="b" * 64,
        )
        defaults.update(overrides)
        return _prod_settings(**defaults)

    def test_default_lax_single_domain_passes(self):
        # The documented single-domain topology (nginx front door) needs
        # no cookie overrides at all.
        s = self._settings()
        s.validate_secrets()  # should not raise

    def test_lax_with_shared_parent_domain_passes(self):
        s = self._settings(auth_cookie_domain=".yourcompany.com")
        s.validate_secrets()  # should not raise

    def test_samesite_none_without_domain_blocks_startup(self):
        s = _prod_settings(auth_cookie_samesite="none")
        with pytest.raises(RuntimeError, match="AUTH_COOKIE_DOMAIN"):
            s.validate_secrets()

    def test_samesite_none_with_domain_and_secure_passes(self):
        # Secure auto-enables in production (auth_cookie_secure=None).
        s = self._settings(
            auth_cookie_samesite="none",
            auth_cookie_domain=".yourcompany.com",
        )
        s.validate_secrets()  # should not raise

    def test_samesite_none_with_secure_disabled_blocks_startup(self):
        s = _prod_settings(
            auth_cookie_samesite="none",
            auth_cookie_domain=".yourcompany.com",
            auth_cookie_secure=False,
        )
        with pytest.raises(RuntimeError, match="Secure"):
            s.validate_secrets()

    def test_invalid_samesite_value_blocks_startup(self):
        s = _prod_settings(auth_cookie_samesite="nnone")
        with pytest.raises(RuntimeError, match="AUTH_COOKIE_SAMESITE"):
            s.validate_secrets()

    def test_dev_env_is_not_gated(self):
        # Dev keeps its zero-ceremony behaviour (validate_secrets early-
        # returns) — the guard is a production-deploy tripwire only.
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="development", auth_cookie_samesite="none",
        )
        s.validate_secrets()  # should not raise
