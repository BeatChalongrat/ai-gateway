"""P4-03: API Key domain service tests.

Verifies:
- create_key: returns raw key + ORM, stores only hash
- verify_key: happy path, revoked, expired, unknown, updates last_used_at
- revoke_key: happy path, already revoked, unknown key_id
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_api_key
from app.models.api_key import ApiKeyORM
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.api_key_service import ApiKeyService
import app.models  # noqa: F401


@pytest.fixture()
def api_key_service():
    return ApiKeyService()


@pytest.fixture()
def tenant_and_project(db_session):
    """Insert a tenant and project, return (tenant_id, project_id)."""
    tid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    db_session.add(TenantORM(tenant_id=tid, name="Svc Tenant", slug=f"svc-{tid[:8]}"))
    db_session.flush()
    db_session.add(ProjectORM(project_id=pid, tenant_id=tid, name="Svc Project"))
    db_session.flush()
    return tid, pid


# ---------------------------------------------------------------------------
# create_key
# ---------------------------------------------------------------------------


class TestCreateKey:
    def test_returns_raw_key_and_orm(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Test"
        )
        assert isinstance(raw_key, str)
        assert isinstance(api_key, ApiKeyORM)
        assert len(raw_key) >= 48

    def test_raw_key_not_stored_in_db(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Test"
        )
        # The raw key should NOT appear in any column
        result = db_session.get(ApiKeyORM, api_key.key_id)
        assert raw_key not in (result.key_hash, result.key_prefix, result.name)

    def test_stored_hash_matches_raw_key(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Test"
        )
        assert api_key.key_hash == hash_api_key(raw_key)

    def test_prefix_is_first_8_chars(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Test"
        )
        assert api_key.key_prefix == raw_key[:8]

    def test_status_is_active(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        _, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Test"
        )
        db_session.expire(api_key)
        assert db_session.get(ApiKeyORM, api_key.key_id).status == "active"

    def test_expires_at_stored(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        exp = datetime(2099, 12, 31, tzinfo=timezone.utc)
        _, api_key = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Expiring", expires_at=exp
        )
        result = db_session.get(ApiKeyORM, api_key.key_id)
        assert result.expires_at is not None

    def test_unique_keys_per_call(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        keys = set()
        for i in range(5):
            raw, _ = api_key_service.create_key(
                db_session, tenant_id=tid, project_id=pid, name=f"Key {i}"
            )
            keys.add(raw)
        assert len(keys) == 5


# ---------------------------------------------------------------------------
# verify_key
# ---------------------------------------------------------------------------


class TestVerifyKey:
    def test_valid_key_returns_orm(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, created = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Valid"
        )
        result = api_key_service.verify_key(db_session, raw_key)
        assert result is not None
        assert result.key_id == created.key_id

    def test_unknown_key_returns_none(self, db_session, api_key_service):
        result = api_key_service.verify_key(db_session, "gw_nonexistent_key_value")
        assert result is None

    def test_revoked_key_returns_none(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, created = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Revoked"
        )
        api_key_service.revoke_key(db_session, created.key_id)
        result = api_key_service.verify_key(db_session, raw_key)
        assert result is None

    def test_expired_key_returns_none(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        raw_key, _ = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Expired", expires_at=past
        )
        result = api_key_service.verify_key(db_session, raw_key)
        assert result is None

    def test_future_expiry_key_is_valid(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        future = datetime.now(timezone.utc) + timedelta(days=30)
        raw_key, _ = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Future", expires_at=future
        )
        result = api_key_service.verify_key(db_session, raw_key)
        assert result is not None

    def test_updates_last_used_at(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        raw_key, created = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Track Usage"
        )
        assert created.last_used_at is None

        api_key_service.verify_key(db_session, raw_key)
        db_session.expire(created)
        result = db_session.get(ApiKeyORM, created.key_id)
        assert result.last_used_at is not None

    def test_empty_string_returns_none(self, db_session, api_key_service):
        result = api_key_service.verify_key(db_session, "")
        assert result is None


# ---------------------------------------------------------------------------
# revoke_key
# ---------------------------------------------------------------------------


class TestRevokeKey:
    def test_revoke_active_key(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        _, created = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="To Revoke"
        )
        result = api_key_service.revoke_key(db_session, created.key_id)
        assert result is True
        db_session.expire(created)
        assert db_session.get(ApiKeyORM, created.key_id).status == "revoked"

    def test_revoke_already_revoked(self, db_session, api_key_service, tenant_and_project):
        tid, pid = tenant_and_project
        _, created = api_key_service.create_key(
            db_session, tenant_id=tid, project_id=pid, name="Double Revoke"
        )
        api_key_service.revoke_key(db_session, created.key_id)
        result = api_key_service.revoke_key(db_session, created.key_id)
        assert result is True  # idempotent — sets revoked again

    def test_revoke_unknown_key_returns_false(self, db_session, api_key_service):
        result = api_key_service.revoke_key(db_session, str(uuid.uuid4()))
        assert result is False
