"""Group A / A1 — startup env-key import placeholder guard.

``app.main._import_env_provider_keys`` must never import a dev placeholder
into ``provider_keys`` (a placeholder in the pool 401s silently on dispatch),
and must retroactively deactivate any placeholder row a pre-guard boot left
behind.

The function opens its own ``SessionLocal()`` and reads the env keys off
``settings``. The test builds a private in-memory engine, points
``app.core.db.SessionLocal`` at it, and pins the three env keys via
monkeypatch — fully isolated from the shared integration DB.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import _import_env_provider_keys
from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_key_service import _encode


def _fresh_sessionmaker():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _pin_env(monkeypatch, *, openai=None, anthropic=None, openrouter=None):
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", openai, raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", anthropic, raising=False)
    monkeypatch.setattr(
        settings, "openrouter_api_key", openrouter, raising=False
    )


class TestPlaceholderNotImported:
    def test_placeholder_env_key_is_not_inserted(self, monkeypatch):
        Session = _fresh_sessionmaker()
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        # Obvious dev placeholder (contains the "your-" marker).
        _pin_env(monkeypatch, anthropic="sk-ant-api03-your-key-here-please")

        _import_env_provider_keys()

        with Session() as s:
            rows = ProviderKeyRepository(s).list_by_provider("anthropic")
        assert rows == [], "placeholder key must not be imported"

    def test_valid_env_key_is_inserted(self, monkeypatch):
        Session = _fresh_sessionmaker()
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        real = "sk-proj-" + "a" * 60  # long, no placeholder marker
        _pin_env(monkeypatch, openai=real)

        _import_env_provider_keys()

        with Session() as s:
            rows = ProviderKeyRepository(s).list_active_by_provider("openai")
        assert len(rows) == 1
        assert rows[0].label == "Imported from environment"


class TestPlaceholderRowSelfHeals:
    def test_prior_imported_placeholder_is_deactivated(self, monkeypatch):
        Session = _fresh_sessionmaker()
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        placeholder = "sk-ant-api03-changeme-xxxxxxxxxxxx"

        # Simulate a pre-guard boot that imported the placeholder.
        with Session() as s:
            repo = ProviderKeyRepository(s)
            repo.create(
                provider_name="anthropic",
                label="Imported from environment",
                api_key_encrypted=_encode(placeholder),
            )
            s.commit()

        _pin_env(monkeypatch, anthropic=placeholder)
        _import_env_provider_keys()

        with Session() as s:
            repo = ProviderKeyRepository(s)
            active = repo.list_active_by_provider("anthropic")
            allrows = repo.list_by_provider("anthropic")
        assert active == [], "placeholder row must be deactivated"
        assert len(allrows) == 1, "row is soft-deactivated, not duplicated"
        assert allrows[0].is_active is False
