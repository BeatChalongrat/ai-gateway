"""PR-03: Startup secret validation tests."""

import pytest

from app.core.config import Settings


class TestSecretValidation:
    def test_dev_env_allows_insecure(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="development", jwt_secret="change-me",
        )
        s.validate_secrets()  # should not raise

    def test_production_rejects_insecure_jwt(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="production", jwt_secret="change-me",
            master_encryption_key="real-secret-value-here",
        )
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            s.validate_secrets()

    def test_production_rejects_insecure_encryption_key(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="production", jwt_secret="real-jwt-secret",
            master_encryption_key="change-me",
        )
        with pytest.raises(RuntimeError, match="MASTER_ENCRYPTION_KEY"):
            s.validate_secrets()

    def test_production_accepts_real_secrets(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="production",
            jwt_secret="a-real-strong-secret-123",
            master_encryption_key="another-strong-key-456",
        )
        s.validate_secrets()  # should not raise

    def test_test_env_allows_insecure(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="test", jwt_secret="change-me",
        )
        s.validate_secrets()  # should not raise

    def test_error_message_is_clear(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="staging", jwt_secret="change-me",
            master_encryption_key="change-me",
        )
        with pytest.raises(RuntimeError, match="STARTUP BLOCKED"):
            s.validate_secrets()
