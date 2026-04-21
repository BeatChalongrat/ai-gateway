import warnings
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {"change-me", "changeme", "secret", "password", "", "dev-stable-key-change-in-prod"}
_DEV_ENVS = {"development", "test", "testing"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_name: str = "secure-ai-gateway"
    app_env: str = "development"
    app_port: int = 8080
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ai_gateway"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Security — jwt_secret MUST be stable across restarts (not random)
    jwt_secret: str = "change-me"
    master_encryption_key: str = "change-me"
    access_token_expire_minutes: int = 1440  # 24 hours

    # Portal JWT — separate secret so leaking the admin secret does not
    # forge portal tokens (and vice versa). Payload carries type="portal".
    portal_jwt_secret: str = "change-me-portal-secret"
    portal_access_token_expire_minutes: int = 60
    portal_refresh_token_expire_days: int = 7

    # Provider API keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    minimax_api_key: str = ""
    qwen_api_key: str = ""
    kimi_api_key: str = ""
    zai_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    xai_api_key: str = ""
    mistral_api_key: str = ""
    cohere_api_key: str = ""
    perplexity_api_key: str = ""

    # Provider timeouts and retry
    provider_timeout_connect: float = 5.0
    provider_timeout_read: float = 30.0
    provider_max_retries: int = 2

    # DLP
    dlp_enabled: bool = True
    dlp_default_profile: str = "default"
    dlp_disabled_detectors: str = ""
    dlp_scan_responses: bool = True
    # Days to retain raw PII content (input_raw / output_raw / matched_text
    # in dlp_findings_json) on request_logs rows. After this window,
    # scripts/dlp_retention_cleanup.py NULL-ifies the raw columns and
    # strips matched_text from each finding — leaving the masked
    # snapshots, summary counts, and per-finding metadata intact for
    # analytics and post-hoc audits.
    dlp_raw_retention_days: int = 90

    # Semantic router (intent classification via sentence-transformers).
    # Off by default — flipping this to true requires the optional
    # sentence-transformers + torch stack (see requirements-semantic.txt).
    semantic_router_enabled: bool = False
    semantic_router_model: str = "all-MiniLM-L6-v2"
    semantic_router_threshold: float = 0.65

    # Optional full DATABASE_URL override (used in Docker / production).
    # When set, takes precedence over individual postgres_* fields.
    database_url_override: str = ""

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dlp_disabled_detectors_list(self) -> list[str]:
        return [d.strip() for d in self.dlp_disabled_detectors.split(",") if d.strip()]

    def validate_secrets(self) -> None:
        """Reject insecure default secrets outside dev/test environments.

        Raises RuntimeError if critical secrets are left at placeholder values.
        """
        if self.app_env.lower() in _DEV_ENVS:
            return  # Allow insecure defaults in dev/test

        issues: list[str] = []
        if self.jwt_secret.lower() in _INSECURE_DEFAULTS:
            issues.append("JWT_SECRET is set to an insecure default")
        if self.master_encryption_key.lower() in _INSECURE_DEFAULTS:
            issues.append("MASTER_ENCRYPTION_KEY is set to an insecure default")

        if issues:
            raise RuntimeError(
                "STARTUP BLOCKED — insecure secrets detected:\n"
                + "\n".join(f"  - {i}" for i in issues)
                + "\n\nSet strong secret values or set APP_ENV=development for local use."
            )


settings = Settings()
