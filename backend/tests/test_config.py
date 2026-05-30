"""Tests for P0-01: Settings class expansion and database URL unification."""

from app.core.config import Settings


class TestSettingsDefaults:
    """Verify Settings instantiates with sensible defaults (no .env file)."""

    def _fresh_settings(self, **overrides) -> Settings:
        """Build a Settings instance using only explicit overrides (no env file)."""
        return Settings(_env_file=None, **overrides)

    def test_app_defaults(self):
        s = self._fresh_settings()
        assert s.app_name == "secure-ai-gateway"
        assert s.app_env == "development"
        assert s.app_port == 8080
        assert s.log_level == "INFO"

    def test_database_defaults(self):
        s = self._fresh_settings()
        assert s.postgres_host == "localhost"
        assert s.postgres_port == 5432
        assert s.postgres_db == "ai_gateway"
        assert s.postgres_user == "postgres"
        assert s.postgres_password == "postgres"

    def test_redis_defaults(self):
        s = self._fresh_settings()
        assert s.redis_host == "localhost"
        assert s.redis_port == 6379

    def test_provider_keys_accept_empty_string(self):
        # Note: can't test true defaults when OS env vars are set (e.g. OPENAI_API_KEY).
        # This verifies the field accepts "" — the class default is "" in config.py.
        s = self._fresh_settings(
            openai_api_key="",
            anthropic_api_key="",
            openrouter_api_key="",
        )
        assert s.openai_api_key == ""
        assert s.anthropic_api_key == ""
        assert s.openrouter_api_key == ""

    def test_security_defaults(self):
        s = self._fresh_settings()
        assert s.jwt_secret == "change-me"
        assert s.master_encryption_key == "change-me"


class TestDatabaseURL:
    """Verify the computed database_url property."""

    def test_default_database_url(self):
        s = Settings(_env_file=None)
        assert s.database_url == (
            "postgresql+psycopg://postgres:postgres@localhost:5432/ai_gateway"
        )

    def test_custom_database_url(self):
        s = Settings(
            _env_file=None,
            postgres_user="admin",
            postgres_password="s3cret",
            postgres_host="db.example.com",
            postgres_port=5433,
            postgres_db="prod_gw",
        )
        assert s.database_url == (
            "postgresql+psycopg://admin:s3cret@db.example.com:5433/prod_gw"
        )


    def test_database_url_encodes_special_chars(self):
        s = Settings(
            _env_file=None,
            postgres_user="admin",
            postgres_password="p@ss:word/test#1",
        )
        # SQLAlchemy must parse the URL and recover the original credentials
        from sqlalchemy import make_url
        parsed = make_url(s.database_url)
        assert parsed.username == "admin"
        assert parsed.password == "p@ss:word/test#1"
        assert parsed.host == "localhost"
        assert parsed.port == 5432


class TestCORSOrigins:
    """Verify cors_origins_list parsing."""

    def test_default_cors_origins(self):
        s = Settings(_env_file=None)
        assert s.cors_origins_list == [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:3002",
            "http://localhost:5173",
        ]

    def test_single_origin(self):
        s = Settings(_env_file=None, cors_origins="https://admin.example.com")
        assert s.cors_origins_list == ["https://admin.example.com"]

    def test_empty_string_produces_empty_list(self):
        s = Settings(_env_file=None, cors_origins="")
        assert s.cors_origins_list == []

    def test_whitespace_trimmed(self):
        s = Settings(
            _env_file=None,
            cors_origins="  http://a.com , http://b.com  ",
        )
        assert s.cors_origins_list == ["http://a.com", "http://b.com"]


class TestDBModuleUsesSettingsURL:
    """Verify db.py engine is wired to settings.database_url."""

    def test_engine_url_matches_settings(self):
        from app.core.db import engine
        from app.core.config import settings

        assert engine.url.render_as_string(hide_password=False) == settings.database_url
