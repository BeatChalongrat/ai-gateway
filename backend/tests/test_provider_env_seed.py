"""Env seeds the first boot; the admin UI owns provider config after that.

Every test here pins one clause of the rule in
``app/services/provider_env_seed.py``::

    a provider that has never had a key  ->  env is read, and seeds the DB
    the moment a key exists              ->  the database owns it; env is
                                             never read for that provider again

The two rules that matter most are the ones a naive "seed when there is no
key" implementation gets wrong in opposite directions, so both have a test
named after the failure they prevent:

* ``test_deleted_key_does_not_come_back`` — too eager. An operator who
  deletes a key must not find it restored from the environment on the next
  restart.
* ``test_dead_inactive_rows_do_not_block_seeding`` — too shy. The old
  importer used "no rows at all", and the leftovers it created (anthropic 1
  inactive, openrouter 3 inactive on the dev box) made those providers
  permanently unseedable with nothing on screen explaining why.
"""

from __future__ import annotations

import pytest

from app.models.system_setting import SystemSettingORM
from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_env_seed import (
    KIND_BASE_URL,
    KIND_KEY,
    marker_key,
    seed_provider_base_urls_from_env,
    seed_provider_keys_from_env,
)
from app.services.provider_key_service import (
    ProviderKeyService,
    _encode,
    clear_keys_cache,
)

REAL_KEY = "sk-proj-" + "a" * 60
ROTATED_KEY = "sk-proj-" + "b" * 60
ENV_KEY = "sk-proj-" + "c" * 60
PLACEHOLDER_KEY = "sk-ant-api03-your-key-here-xxxx"

PROVIDER = "openai"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_keys_cache()
    yield
    clear_keys_cache()


@pytest.fixture()
def env_key(monkeypatch):
    """Put a real-looking key in the environment for ``openai``."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", ENV_KEY, raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    return ENV_KEY


def _boot(session) -> dict[str, str]:
    """One startup pass of the key seeder."""
    outcomes = seed_provider_keys_from_env(session)
    session.flush()
    clear_keys_cache()
    return outcomes


def _status_and_dispatch(session, provider: str = PROVIDER):
    svc = ProviderKeyService()
    return (
        svc.get_key_status(session, provider),
        svc._resolve_key_candidates(session, provider),
    )


# ---------------------------------------------------------------------------
# First boot
# ---------------------------------------------------------------------------


class TestFirstBoot:
    def test_fresh_database_is_seeded_and_dispatch_uses_the_key(
        self, db_session, env_key
    ):
        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "seeded"
        status, candidates = _status_and_dispatch(db_session)
        # The badge and dispatch are derived from the same state, so this
        # asserts both halves at once.
        assert status["configured"] is True
        assert status["total_keys"] == 1
        assert status["source"] == "provider_keys"
        assert candidates == [env_key]
        assert ProviderKeyService().get_active_key(db_session, PROVIDER) == env_key

    def test_the_seeded_row_says_where_it_came_from(self, db_session, env_key):
        _boot(db_session)

        keys = ProviderKeyService().list_keys(db_session, PROVIDER)
        assert [k["label"] for k in keys] == ["Seeded from environment"]

    def test_the_seeded_row_is_an_ordinary_ui_managed_key(self, db_session, env_key):
        """Not a special read-only class: editable, rotatable, deletable.

        This is the Open WebUI wart we are explicitly not reproducing — a UI
        that looks editable while the value cannot actually change.
        """
        _boot(db_session)
        svc = ProviderKeyService()
        seeded_id = svc.list_keys(db_session, PROVIDER)[0]["id"]

        # The admin UI rotates by adding the replacement and deleting the old
        # row, so that is what is exercised here.
        svc.add_key(db_session, PROVIDER, ROTATED_KEY, label="rotated")
        svc.delete_key(db_session, seeded_id)
        db_session.flush()
        clear_keys_cache()
        assert svc._resolve_key_candidates(db_session, PROVIDER) == [ROTATED_KEY]

        rotated_id = [
            k["id"] for k in svc.list_keys(db_session, PROVIDER) if k["is_active"]
        ][0]
        svc.delete_key(db_session, rotated_id)
        db_session.flush()
        clear_keys_cache()
        assert svc._resolve_key_candidates(db_session, PROVIDER) == []

    def test_a_marker_row_records_the_decision(self, db_session, env_key):
        _boot(db_session)

        marker = db_session.get(SystemSettingORM, marker_key(KIND_KEY, PROVIDER))
        assert marker is not None
        assert marker.value == "env-seed"
        # The description is what an operator reads months later.
        assert "admin UI" in marker.description
        # And it must never carry key material.
        assert ENV_KEY not in marker.description
        assert ENV_KEY not in marker.value


# ---------------------------------------------------------------------------
# Every boot after the first
# ---------------------------------------------------------------------------


class TestSubsequentBoots:
    def test_second_boot_does_not_reseed_or_duplicate(self, db_session, env_key):
        _boot(db_session)
        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "owned-by-database"
        assert len(ProviderKeyService().list_keys(db_session, PROVIDER)) == 1

    def test_edited_key_survives_a_restart(self, db_session, env_key):
        """The admin UI is the owner: a restart must not undo an edit."""
        _boot(db_session)
        svc = ProviderKeyService()
        seeded_id = svc.list_keys(db_session, PROVIDER)[0]["id"]
        svc.add_key(db_session, PROVIDER, ROTATED_KEY, label="rotated")
        svc.delete_key(db_session, seeded_id)
        db_session.flush()
        clear_keys_cache()

        _boot(db_session)  # restart

        assert svc._resolve_key_candidates(db_session, PROVIDER) == [ROTATED_KEY]

    def test_deleted_key_does_not_come_back(self, db_session, env_key):
        """TOO EAGER. Deleting a key is an operator decision; a restart must
        not silently undo it, even though env still holds a real value."""
        _boot(db_session)
        svc = ProviderKeyService()
        row_id = svc.list_keys(db_session, PROVIDER)[0]["id"]
        svc.delete_key(db_session, row_id)
        db_session.flush()
        clear_keys_cache()

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "owned-by-database"
        status, candidates = _status_and_dispatch(db_session)
        assert candidates == []
        assert status["configured"] is False

    def test_a_ui_added_key_closes_the_env_door_without_a_boot(
        self, db_session, env_key
    ):
        """Adding a key in the UI marks the provider owned immediately, so
        the door cannot re-open through a delete that never saw a boot."""
        svc = ProviderKeyService()
        svc.add_key(db_session, PROVIDER, REAL_KEY, label="admin")
        db_session.flush()
        row_id = svc.list_keys(db_session, PROVIDER)[0]["id"]
        svc.delete_key(db_session, row_id)
        db_session.flush()
        clear_keys_cache()

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "owned-by-database"
        assert svc._resolve_key_candidates(db_session, PROVIDER) == []

    def test_an_existing_key_is_never_overwritten(self, db_session, env_key):
        svc = ProviderKeyService()
        svc.add_key(db_session, PROVIDER, REAL_KEY, label="admin")
        db_session.flush()
        clear_keys_cache()

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] in ("already-configured", "owned-by-database")
        assert svc._resolve_key_candidates(db_session, PROVIDER) == [REAL_KEY]


# ---------------------------------------------------------------------------
# What must never be seeded
# ---------------------------------------------------------------------------


class TestPlaceholdersAreNeverSeeded:
    @pytest.mark.parametrize(
        "value",
        [
            "sk-ant-api03-...",
            "your-openai-key-here-xxxxxxxxxxxx",
            "changeme-changeme-changeme",
            "placeholder-placeholder-value",
            "example-key-example-key-example",
            "short",
            "",
        ],
    )
    def test_placeholder_env_seeds_nothing_and_badge_says_none(
        self, db_session, monkeypatch, value
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "openai_api_key", value, raising=False)
        monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
        monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] in ("env-placeholder", "env-unset")
        status, candidates = _status_and_dispatch(db_session)
        assert status["configured"] is False
        assert status["total_keys"] == 0
        assert candidates == []

    def test_a_placeholder_boot_decides_nothing_so_a_real_key_can_still_seed(
        self, db_session, monkeypatch
    ):
        """The ordinary flow: copy .env.example, boot, then fill in the real
        keys. A first look at a placeholder must not burn the one chance."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "openai_api_key", PLACEHOLDER_KEY, raising=False)
        monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
        monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
        _boot(db_session)
        assert db_session.get(SystemSettingORM, marker_key(KIND_KEY, PROVIDER)) is None

        monkeypatch.setattr(settings, "openai_api_key", ENV_KEY, raising=False)
        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "seeded"
        assert ProviderKeyService()._resolve_key_candidates(db_session, PROVIDER) == [
            ENV_KEY
        ]


class TestAutoflushIndependence:
    """The seeder must behave the same under the session settings PRODUCTION
    uses, not just the ones the test fixtures happen to have.

    ``SessionLocal`` is built with ``autoflush=False`` (app/core/db.py:96).
    The seed path marks the provider owned and then calls ``add_key``, which
    marks it again; with no flush in between, the second call cannot see the
    pending row and inserts a duplicate primary key. This test pins the fix
    by running the seeder on a session with autoflush switched off.
    """

    def test_endpoint_seed_writes_exactly_one_marker_without_autoflush(
        self, db_session, monkeypatch
    ):
        """The ENDPOINT path is the vulnerable one.

        ``seed_provider_base_urls_from_env`` marks the provider owned and
        then calls ``set_stored_base_url``, which marks it again — with no
        flush in between unless ``mark_provider_owned`` does it. The KEY path
        happens to be safe because ``add_key`` → ``repo.create`` →
        ``BaseRepository.add`` flushes to populate the id, which is exactly
        the kind of accidental protection that disappears in a refactor.
        """
        from app.core.config import settings
        from app.models.system_setting import SystemSettingORM

        monkeypatch.setattr(
            settings,
            "provider_base_url_overrides",
            '{"sranai": "http://mock-provider:9090/v1"}',
            raising=False,
        )
        db_session.autoflush = False

        outcomes = seed_provider_base_urls_from_env(db_session)
        db_session.flush()

        assert outcomes["sranai"] == "seeded"
        markers = (
            db_session.query(SystemSettingORM)
            .filter(SystemSettingORM.key == marker_key(KIND_BASE_URL, "sranai"))
            .all()
        )
        assert len(markers) == 1
        assert markers[0].value == "env-seed"

    def test_key_seed_writes_exactly_one_marker_without_autoflush(
        self, db_session, env_key
    ):
        from app.models.system_setting import SystemSettingORM

        db_session.autoflush = False
        outcomes = seed_provider_keys_from_env(db_session)
        db_session.flush()

        assert outcomes[PROVIDER] == "seeded"
        markers = (
            db_session.query(SystemSettingORM)
            .filter(SystemSettingORM.key == marker_key(KIND_KEY, PROVIDER))
            .all()
        )
        assert len(markers) == 1
        assert markers[0].value == "env-seed"


class TestDeadRowsDoNotBlock:
    def test_dead_inactive_rows_do_not_block_seeding(self, db_session, env_key):
        """TOO SHY. The dev box carries inactive rows the old importer left
        behind. Under a "no rows at all" rule those providers could never be
        seeded again; the marker gate ignores them."""
        repo = ProviderKeyRepository(db_session)
        for label in ("Imported from environment", "Key 3"):
            row = repo.create(
                provider_name=PROVIDER,
                label=label,
                api_key_encrypted=_encode(REAL_KEY),
            )
            repo.update(row, is_active=False)
        db_session.flush()
        clear_keys_cache()

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "seeded"
        assert ProviderKeyService()._resolve_key_candidates(db_session, PROVIDER) == [
            ENV_KEY
        ]

    def test_an_invalid_only_provider_is_still_seedable(self, db_session, env_key):
        """A row marked invalid by a key test is not a usable key, so it must
        not count as "already configured" either."""
        repo = ProviderKeyRepository(db_session)
        row = repo.create(
            provider_name=PROVIDER,
            label="bad",
            api_key_encrypted=_encode(REAL_KEY),
        )
        repo.update(row, last_test_status="invalid")
        db_session.flush()
        clear_keys_cache()

        outcomes = _boot(db_session)

        assert outcomes[PROVIDER] == "seeded"
        assert ENV_KEY in ProviderKeyService()._resolve_key_candidates(
            db_session, PROVIDER
        )


# ---------------------------------------------------------------------------
# Endpoints follow the same rule
# ---------------------------------------------------------------------------


class TestBaseUrlSeed:
    @pytest.fixture()
    def env_map(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(
            settings,
            "provider_base_url_overrides",
            '{"sranai": "http://mock-provider:9090/v1"}',
            raising=False,
        )

    def test_first_boot_seeds_the_endpoint(self, db_session, env_map):
        from app.services.providers.base_urls import get_stored_base_url

        outcomes = seed_provider_base_urls_from_env(db_session)
        db_session.flush()

        assert outcomes["sranai"] == "seeded"
        assert get_stored_base_url(db_session, "sranai") == (
            "http://mock-provider:9090/v1"
        )
        marker = db_session.get(
            SystemSettingORM, marker_key(KIND_BASE_URL, "sranai")
        )
        assert marker is not None and marker.value == "env-seed"

    def test_second_boot_does_not_overwrite_an_admin_edit(self, db_session, env_map):
        from app.services.providers.base_urls import (
            clear_base_url_cache,
            get_stored_base_url,
            set_stored_base_url,
        )

        seed_provider_base_urls_from_env(db_session)
        db_session.flush()
        set_stored_base_url(db_session, "sranai", "http://real-box:8080/v1")
        db_session.flush()
        clear_base_url_cache()

        outcomes = seed_provider_base_urls_from_env(db_session)
        db_session.flush()

        assert outcomes["sranai"] == "owned-by-database"
        assert get_stored_base_url(db_session, "sranai") == "http://real-box:8080/v1"

    def test_malformed_env_map_is_ignored_not_fatal(self, db_session, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(
            settings, "provider_base_url_overrides", "{not json", raising=False
        )
        assert seed_provider_base_urls_from_env(db_session) == {"*": "env-malformed"}

    def test_an_endpoint_the_admin_form_would_reject_is_not_seeded(
        self, db_session, monkeypatch
    ):
        """A seed is not a reason to accept a URL the UI would refuse."""
        from app.core.config import settings
        from app.services.providers.base_urls import get_stored_base_url

        monkeypatch.setattr(
            settings,
            "provider_base_url_overrides",
            '{"sranai": "file:///etc/passwd"}',
            raising=False,
        )
        outcomes = seed_provider_base_urls_from_env(db_session)

        assert outcomes["sranai"] == "env-invalid"
        assert get_stored_base_url(db_session, "sranai") is None
