"""P4-02: API Key ORM model, secure hashing, and migration tests.

Verifies:
- ApiKeyORM schema: columns, types, constraints, defaults
- FKs to tenants.tenant_id and projects.project_id
- hash_api_key is deterministic SHA-256
- generate_api_key produces correct format
- Migration 003 upgrade/downgrade
- ORM CRUD operations
"""

import hashlib
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.core.db import Base
from app.core.security import generate_api_key, hash_api_key
from app.models.api_key import ApiKeyORM
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
import app.models  # noqa: F401


# ---------------------------------------------------------------------------
# Hashing utility tests
# ---------------------------------------------------------------------------


class TestHashApiKey:
    def test_returns_64_char_hex_string(self):
        result = hash_api_key("gw_abc123")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        key = "gw_test_determinism"
        assert hash_api_key(key) == hash_api_key(key)

    def test_matches_stdlib_sha256(self):
        key = "gw_verify_sha256"
        expected = hashlib.sha256(key.encode("utf-8")).hexdigest()
        assert hash_api_key(key) == expected

    def test_different_keys_produce_different_hashes(self):
        assert hash_api_key("gw_key_one") != hash_api_key("gw_key_two")

    def test_empty_string(self):
        result = hash_api_key("")
        assert len(result) == 64


class TestGenerateApiKey:
    def test_returns_three_tuple(self):
        result = generate_api_key()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_raw_key_starts_with_prefix(self):
        raw_key, _, _ = generate_api_key("gw")
        assert raw_key.startswith("gw_")

    def test_custom_prefix(self):
        raw_key, _, _ = generate_api_key("test")
        assert raw_key.startswith("test_")

    def test_key_prefix_is_first_8_chars(self):
        raw_key, key_prefix, _ = generate_api_key()
        assert key_prefix == raw_key[:8]
        assert len(key_prefix) == 8

    def test_key_hash_matches_raw_key(self):
        raw_key, _, key_hash = generate_api_key()
        assert key_hash == hash_api_key(raw_key)

    def test_each_call_produces_unique_key(self):
        keys = {generate_api_key()[0] for _ in range(10)}
        assert len(keys) == 10

    def test_raw_key_has_sufficient_entropy(self):
        raw_key, _, _ = generate_api_key()
        # "gw_" + 48 hex chars = 51 chars minimum
        assert len(raw_key) >= 48


# ---------------------------------------------------------------------------
# Schema verification
# ---------------------------------------------------------------------------


class TestApiKeySchema:
    def test_api_keys_table_exists(self, db_inspector):
        assert "api_keys" in db_inspector.get_table_names()

    def test_api_keys_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("api_keys")}
        expected = {
            "key_id",
            "key_prefix",
            "key_hash",
            "tenant_id",
            "project_id",
            "name",
            "role",
            "status",
            "expires_at",
            "created_at",
            "last_used_at",
            # Added by migration 025 (portal users): link a key to its
            # owning portal user so the gateway credit-check path can
            # find the user for suspension / deduction checks.
            "portal_user_id",
        }
        assert expected == cols

    def test_key_id_is_primary_key(self, db_inspector):
        pk = db_inspector.get_pk_constraint("api_keys")
        assert pk["constrained_columns"] == ["key_id"]

    def test_key_hash_has_unique_constraint(self, db_inspector):
        indexes = db_inspector.get_indexes("api_keys")
        unique_cols = {
            col
            for idx in indexes
            if idx["unique"]
            for col in idx["column_names"]
        }
        uqs = db_inspector.get_unique_constraints("api_keys")
        for uq in uqs:
            for col in uq["column_names"]:
                unique_cols.add(col)
        assert "key_hash" in unique_cols

    def test_fk_to_tenants_in_metadata(self):
        table = Base.metadata.tables["api_keys"]
        fk_targets = {str(fk.target_fullname) for fk in table.foreign_keys}
        assert "tenants.tenant_id" in fk_targets

    def test_fk_to_projects_in_metadata(self):
        table = Base.metadata.tables["api_keys"]
        fk_targets = {str(fk.target_fullname) for fk in table.foreign_keys}
        assert "projects.project_id" in fk_targets


# ---------------------------------------------------------------------------
# ORM CRUD
# ---------------------------------------------------------------------------


def _make_tenant_and_project(db_session):
    """Helper: insert a tenant and project, return (tenant_id, project_id)."""
    tid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    db_session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    db_session.flush()
    db_session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    db_session.flush()
    return tid, pid


class TestApiKeyORM:
    def test_insert_and_read_back(self, db_session):
        tid, pid = _make_tenant_and_project(db_session)
        raw_key, prefix, khash = generate_api_key()

        ak = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=prefix,
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="Test Key",
        )
        db_session.add(ak)
        db_session.flush()

        result = db_session.get(ApiKeyORM, ak.key_id)
        assert result is not None
        assert result.key_prefix == prefix
        assert result.key_hash == khash
        assert result.name == "Test Key"

    def test_status_defaults_to_active(self, db_session):
        tid, pid = _make_tenant_and_project(db_session)
        _, prefix, khash = generate_api_key()

        ak = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=prefix,
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="Default Status",
        )
        db_session.add(ak)
        db_session.flush()
        db_session.expire(ak)

        result = db_session.get(ApiKeyORM, ak.key_id)
        assert result.status == "active"

    def test_duplicate_key_hash_rejected(self, db_session):
        tid, pid = _make_tenant_and_project(db_session)
        khash = hash_api_key("gw_duplicate_test")

        ak1 = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix="gw_dupli",
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="Key 1",
        )
        ak2 = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix="gw_dupli",
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="Key 2",
        )
        db_session.add(ak1)
        db_session.flush()
        db_session.add(ak2)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_expires_at_nullable(self, db_session):
        tid, pid = _make_tenant_and_project(db_session)
        _, prefix, khash = generate_api_key()

        ak = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=prefix,
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="No Expiry",
        )
        db_session.add(ak)
        db_session.flush()

        result = db_session.get(ApiKeyORM, ak.key_id)
        assert result.expires_at is None

    def test_last_used_at_nullable(self, db_session):
        tid, pid = _make_tenant_and_project(db_session)
        _, prefix, khash = generate_api_key()

        ak = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=prefix,
            key_hash=khash,
            tenant_id=tid,
            project_id=pid,
            name="Never Used",
        )
        db_session.add(ak)
        db_session.flush()

        result = db_session.get(ApiKeyORM, ak.key_id)
        assert result.last_used_at is None


# ---------------------------------------------------------------------------
# Migration 003: upgrade / downgrade
# ---------------------------------------------------------------------------


def _load_migration(name: str):
    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / name
    )
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration003:
    def _run_all_upgrades(self, conn):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            _load_migration("001_initial_schema.py").upgrade()
            _load_migration("002_add_tenants.py").upgrade()
            _load_migration("003_add_api_keys.py").upgrade()
            _load_migration("004_add_usage_columns.py").upgrade()
            _load_migration("005_add_role_to_api_keys.py").upgrade()
            # Migration 025 adds portal_user_id to api_keys — to keep
            # test_migration_columns_match_orm honest the runner must
            # reach at least that revision. 024 is a prereq.
            _load_migration("024_system_settings.py").upgrade()
            _load_migration("025_portal_users.py").upgrade()

    def test_upgrade_creates_api_keys_table(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            self._run_all_upgrades(conn)
            inspector = inspect(conn)
            assert "api_keys" in inspector.get_table_names()
        engine.dispose()

    def test_upgrade_columns_complete(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            self._run_all_upgrades(conn)
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("api_keys")}
            expected = {
                "key_id",
                "key_prefix",
                "key_hash",
                "tenant_id",
                "project_id",
                "name",
                "role",
                "status",
                "expires_at",
                "created_at",
                "last_used_at",
                "portal_user_id",  # migration 025
            }
            assert expected == cols
        engine.dispose()

    def test_downgrade_drops_api_keys_table(self):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                _load_migration("001_initial_schema.py").upgrade()
                _load_migration("002_add_tenants.py").upgrade()
                m003 = _load_migration("003_add_api_keys.py")
                m003.upgrade()
                m003.downgrade()

            inspector = inspect(conn)
            assert "api_keys" not in inspector.get_table_names()
            assert "tenants" in inspector.get_table_names()
            assert "projects" in inspector.get_table_names()

        engine.dispose()

    def test_migration_columns_match_orm(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            self._run_all_upgrades(conn)
            inspector = inspect(conn)
            migration_cols = {
                c["name"] for c in inspector.get_columns("api_keys")
            }
            orm_cols = {
                c.name for c in Base.metadata.tables["api_keys"].columns
            }
            assert migration_cols == orm_cols
        engine.dispose()
