"""Repository-layer tests for ``ProviderKeyRepository``.

Exercises the extraction from ``ProviderKeyService`` directly against
the repository. The seventh test verifies the service still delegates
to the repository after the refactor — a spy on
``ProviderKeyRepository.list_by_provider`` must fire when
``ProviderKeyService.list_keys`` is called.

The repository takes a ``Session`` in ``__init__`` and reuses it for
every call, so tests use the standard ``db_session`` fixture (from
``conftest.py``) which gives a fresh per-test session bound to an
in-memory SQLite engine with all ORM tables created.
"""

from __future__ import annotations

import pytest

from app.models.provider_key import ProviderKeyORM
from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_key_service import ProviderKeyService


def _make_repo(db_session) -> ProviderKeyRepository:
    return ProviderKeyRepository(db_session)


class TestCreateAndGetById:
    def test_create_and_get_by_id(self, db_session):
        repo = _make_repo(db_session)
        created = repo.create(
            provider_name="openai",
            label="primary",
            api_key_encrypted="enc_key_abc",
        )
        assert created.id
        fetched = repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.label == "primary"
        assert fetched.provider_name == "openai"
        assert fetched.api_key_encrypted == "enc_key_abc"
        # Default test status is untested.
        assert fetched.last_test_status == "untested"


class TestListByProviderReturnsCorrectRows:
    def test_list_by_provider_returns_correct_rows(self, db_session):
        repo = _make_repo(db_session)
        repo.create("openai", "a", "e1")
        repo.create("openai", "b", "e2")
        repo.create("anthropic", "a", "e3")

        openai_rows = repo.list_by_provider("openai")
        anthropic_rows = repo.list_by_provider("anthropic")

        assert len(openai_rows) == 2
        assert {r.label for r in openai_rows} == {"a", "b"}
        assert len(anthropic_rows) == 1
        assert anthropic_rows[0].label == "a"


class TestGetActiveForProviderReturnsActiveKey:
    def test_get_active_for_provider_returns_active_key(self, db_session):
        repo = _make_repo(db_session)
        inactive = repo.create("openai", "old", "enc_old")
        repo.update(inactive, is_active=False)
        active = repo.create("openai", "new", "enc_new")

        found = repo.get_active_for_provider("openai")
        assert found is not None
        assert found.id == active.id
        assert found.is_active is True


class TestSetActiveDeactivatesOthers:
    def test_set_active_deactivates_others(self, db_session):
        repo = _make_repo(db_session)
        k1 = repo.create("openai", "one", "e1")
        k2 = repo.create("openai", "two", "e2")
        k3 = repo.create("openai", "three", "e3")
        # All 3 created active by default — set_active on k3 must
        # turn k1 and k2 off.
        result = repo.set_active(k3.id, "openai")

        assert result is not None
        assert result.id == k3.id
        assert result.is_active is True

        # Re-fetch all three from the session to see the updated state.
        k1_fresh = repo.get_by_id(k1.id)
        k2_fresh = repo.get_by_id(k2.id)
        k3_fresh = repo.get_by_id(k3.id)
        assert k1_fresh.is_active is False
        assert k2_fresh.is_active is False
        assert k3_fresh.is_active is True


class TestRecordTestResultUpdatesFields:
    def test_record_test_result_updates_fields(self, db_session):
        repo = _make_repo(db_session)
        key = repo.create("openai", "primary", "enc_key_abc")

        updated = repo.record_test_result(key.id, "valid", 142)

        assert updated is not None
        assert updated.last_test_status == "valid"
        assert updated.last_test_latency_ms == 142
        assert updated.last_tested_at is not None


class TestDeleteRemovesRow:
    def test_delete_removes_row(self, db_session):
        repo = _make_repo(db_session)
        key = repo.create("openai", "primary", "enc_key_abc")

        # Hard delete (the repo primitive) — NOT the service's
        # soft-delete public API.
        deleted = repo.delete(key.id)
        assert deleted is True
        assert repo.get_by_id(key.id) is None

        # Deleting again is a no-op that returns False.
        assert repo.delete(key.id) is False


class TestProviderKeyServiceUsesRepository:
    def test_provider_key_service_uses_repository(self, db_session, monkeypatch):
        """Regression: the refactor must route service reads through the repo.

        Spies on ``ProviderKeyRepository.list_by_provider``. After the
        refactor, ``ProviderKeyService.list_keys`` must call it.
        """
        # Seed one row so list_keys has something to decode + mask.
        repo = _make_repo(db_session)
        repo.create("openai", "primary", "ZW5jX2tleV9hYmM=")  # base64("enc_key_abc")

        captured: dict = {"calls": 0, "arg": None}
        original = ProviderKeyRepository.list_by_provider

        def _spy(self, provider_name):
            captured["calls"] += 1
            captured["arg"] = provider_name
            return original(self, provider_name)

        monkeypatch.setattr(
            ProviderKeyRepository, "list_by_provider", _spy
        )

        svc = ProviderKeyService()
        rows = svc.list_keys(db_session, "openai")

        assert captured["calls"] == 1
        assert captured["arg"] == "openai"
        assert len(rows) == 1
        assert rows[0]["label"] == "primary"
