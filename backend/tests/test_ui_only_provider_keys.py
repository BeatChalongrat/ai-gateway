"""Provider keys and endpoints are configured in the admin UI only.

These tests pin the contract that the "UI only" change established:

* There is no environment tier. ``_ENV_KEYS`` is gone, both resolver
  variants stop after the legacy ``providers`` table, and no amount of
  environment configuration can make an unkeyed provider dispatchable.
* ``get_key_status`` — which drives the admin Providers card — reports
  exactly what dispatch would do. Before this, a provider whose only key
  was marked ``invalid`` reported ``configured: true`` while every request
  to it failed with "no API key configured", and a provider with only a
  placeholder env var showed a ``via env`` badge while dispatch had
  nothing at all.

The badge/dispatch agreement is asserted by deriving BOTH from the same
fixtures in each test, so the two can never drift apart silently again.
"""

from __future__ import annotations

import pytest

from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_key_service import (
    ProviderKeyService,
    _encode,
    clear_keys_cache,
)

REAL_KEY = "sk-proj-" + "a" * 60
OTHER_REAL_KEY = "sk-proj-" + "b" * 60
PLACEHOLDER_KEY = "sk-ant-api03-your-key-here-xxxx"

PROVIDER = "openai"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_keys_cache()
    yield
    clear_keys_cache()


def _status_and_dispatch(session, provider: str = PROVIDER):
    """Return ``(status_dict, resolved_candidates)`` for the same state."""
    svc = ProviderKeyService()
    return (
        svc.get_key_status(session, provider),
        svc._resolve_key_candidates(session, provider),
    )


# ---------------------------------------------------------------------------
# The "none" state
# ---------------------------------------------------------------------------


class TestNoKeyConfigured:
    def test_reports_none_and_dispatch_has_nothing(self, db_session):
        status, candidates = _status_and_dispatch(db_session)

        assert status["source"] == "none"
        assert status["configured"] is False
        assert status["total_keys"] == 0
        assert status["valid_keys"] == 0
        assert candidates == []

    def test_get_active_key_returns_none(self, db_session):
        assert ProviderKeyService().get_active_key(db_session, PROVIDER) is None

    def test_environment_cannot_configure_a_provider(self, db_session, monkeypatch):
        """The whole point of the change: setting the env var that used to
        back this provider must NOT make it configured or dispatchable."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "openai_api_key", REAL_KEY, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", REAL_KEY)
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert status["source"] == "none"
        assert status["configured"] is False
        assert candidates == []

    def test_env_keys_map_is_gone(self):
        import app.services.provider_key_service as pks

        assert not hasattr(pks, "_ENV_KEYS")

    def test_status_never_reports_environment_as_a_source(self, db_session):
        """``source`` is a closed set of three values now."""
        status, _ = _status_and_dispatch(db_session)
        assert status["source"] in {"provider_keys", "database", "none"}


# ---------------------------------------------------------------------------
# Invalid-only: the divergence the audit found
# ---------------------------------------------------------------------------


class TestInvalidOnlyKeyIsNotConfigured:
    def test_invalid_only_reports_not_configured(self, db_session):
        repo = ProviderKeyRepository(db_session)
        row = repo.create(PROVIDER, "k1", _encode(REAL_KEY))
        repo.update(row, last_test_status="invalid")
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        # Dispatch skips it...
        assert candidates == []
        # ...so the badge must not claim it.
        assert status["configured"] is False
        assert status["source"] == "none"
        assert status["total_keys"] == 0
        # But the operator is told the row exists and is unusable, so a
        # "no key" badge on a provider that visibly has a key row is
        # explainable rather than looking like a bug.
        assert status["unusable_keys"] == 1

    def test_row_is_still_listed_for_the_operator(self, db_session):
        """Filtering the SUMMARY must not hide the row itself — the key
        panel still shows it with its invalid status."""
        repo = ProviderKeyRepository(db_session)
        row = repo.create(PROVIDER, "k1", _encode(REAL_KEY))
        repo.update(row, last_test_status="invalid")
        db_session.flush()

        listed = ProviderKeyService().list_keys(db_session, PROVIDER)

        assert len(listed) == 1
        assert listed[0]["last_test_status"] == "invalid"

    def test_one_good_key_alongside_an_invalid_one_is_configured(self, db_session):
        repo = ProviderKeyRepository(db_session)
        bad = repo.create(PROVIDER, "bad", _encode(REAL_KEY))
        repo.update(bad, last_test_status="invalid")
        repo.create(PROVIDER, "good", _encode(OTHER_REAL_KEY))
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert candidates == [OTHER_REAL_KEY]
        assert status["configured"] is True
        assert status["source"] == "provider_keys"
        assert status["total_keys"] == 1
        assert status["unusable_keys"] == 1


# ---------------------------------------------------------------------------
# Placeholder filter
# ---------------------------------------------------------------------------


class TestPlaceholderKeyIsNotConfigured:
    def test_placeholder_only_reports_not_configured(self, db_session):
        repo = ProviderKeyRepository(db_session)
        repo.create(PROVIDER, "ph", _encode(PLACEHOLDER_KEY))
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert candidates == []
        assert status["configured"] is False
        assert status["source"] == "none"
        assert status["total_keys"] == 0
        assert status["unusable_keys"] == 1

    def test_placeholder_is_dropped_but_real_key_survives(self, db_session):
        repo = ProviderKeyRepository(db_session)
        repo.create(PROVIDER, "ph", _encode(PLACEHOLDER_KEY))
        repo.create(PROVIDER, "real", _encode(REAL_KEY))
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert candidates == [REAL_KEY]
        assert status["total_keys"] == 1
        assert status["configured"] is True

    @pytest.mark.parametrize(
        "value",
        [
            "sk-ant-...",
            "sk-...",
            "changeme",
            "your-api-key-goes-here-abcdefgh",
            "sk-proj-placeholder-value-abcdefgh",
            "short",
        ],
    )
    def test_known_placeholder_shapes_are_all_rejected(self, db_session, value):
        repo = ProviderKeyRepository(db_session)
        repo.create(PROVIDER, "ph", _encode(value))
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert candidates == []
        assert status["configured"] is False


# ---------------------------------------------------------------------------
# A key added through the UI is the key dispatch uses
# ---------------------------------------------------------------------------


class TestUiAddedKeyIsUsedByDispatch:
    def test_add_key_makes_the_provider_dispatchable_immediately(self, db_session):
        svc = ProviderKeyService()

        before_status, before_candidates = _status_and_dispatch(db_session)
        assert before_status["configured"] is False
        assert before_candidates == []

        svc.add_key(db_session, PROVIDER, REAL_KEY, label="added via UI")
        db_session.flush()

        status, candidates = _status_and_dispatch(db_session)

        # add_key busts the resolved-key cache, so this needs no restart
        # and no TTL wait in the worker that served the request.
        assert candidates == [REAL_KEY]
        assert svc.get_active_key(db_session, PROVIDER) == REAL_KEY
        assert status["configured"] is True
        assert status["source"] == "provider_keys"
        assert status["total_keys"] == 1

    def test_badge_count_equals_dispatch_pool_size(self, db_session):
        svc = ProviderKeyService()
        svc.add_key(db_session, PROVIDER, REAL_KEY)
        svc.add_key(db_session, PROVIDER, OTHER_REAL_KEY)
        db_session.flush()
        clear_keys_cache()

        status, candidates = _status_and_dispatch(db_session)

        assert status["total_keys"] == len(candidates) == 2

    def test_deleting_the_last_key_returns_to_none(self, db_session):
        svc = ProviderKeyService()
        row = svc.add_key(db_session, PROVIDER, REAL_KEY)
        db_session.flush()
        svc.delete_key(db_session, row.id)
        db_session.flush()

        status, candidates = _status_and_dispatch(db_session)

        assert candidates == []
        assert status["source"] == "none"
        assert status["configured"] is False
