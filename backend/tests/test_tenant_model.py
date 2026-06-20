"""P4-01: Tenant ORM model, migration, and domain entity tests.

Verifies:
- TenantORM schema: columns, types, constraints, defaults
- FK from projects.tenant_id → tenants.tenant_id
- Migration 002 upgrade/downgrade
- Domain Tenant dataclass alignment with ORM
"""

import importlib.util
import uuid
from dataclasses import fields as dc_fields
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.core.db import Base
from app.domain.entities import Tenant
from app.models.tenant import TenantORM
from app.models.project import ProjectORM
import app.models  # noqa: F401


# ---------------------------------------------------------------------------
# Schema verification
# ---------------------------------------------------------------------------


class TestTenantSchema:
    """Verify TenantORM produces the expected table structure."""

    def test_tenants_table_exists(self, db_inspector):
        assert "tenants" in db_inspector.get_table_names()

    def test_tenants_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("tenants")}
        expected = {
            "tenant_id",
            "name",
            "slug",
            "status",
            "region",
            "ner_enabled",  # added by migration 019
            "dlp_engine",  # added by migration 043 (Phase-3c Rust DLP sidecar)
            "credit_markup",  # added by migration 054 (cost-based credit billing)
            "created_at",
            "updated_at",
        }
        assert expected == cols

    def test_tenant_id_is_primary_key(self, db_inspector):
        pk = db_inspector.get_pk_constraint("tenants")
        assert pk["constrained_columns"] == ["tenant_id"]

    def test_slug_has_unique_constraint(self, db_inspector):
        indexes = db_inspector.get_indexes("tenants")
        unique_cols = {
            col
            for idx in indexes
            if idx["unique"]
            for col in idx["column_names"]
        }
        # Also check unique constraints directly
        uqs = db_inspector.get_unique_constraints("tenants")
        for uq in uqs:
            for col in uq["column_names"]:
                unique_cols.add(col)
        assert "slug" in unique_cols


# ---------------------------------------------------------------------------
# ORM defaults and CRUD
# ---------------------------------------------------------------------------


class TestTenantORM:
    def test_insert_and_read_back(self, db_session):
        tenant = TenantORM(
            tenant_id=str(uuid.uuid4()),
            name="Acme Corp",
            slug="acme-corp",
        )
        db_session.add(tenant)
        db_session.flush()

        result = db_session.get(TenantORM, tenant.tenant_id)
        assert result is not None
        assert result.name == "Acme Corp"
        assert result.slug == "acme-corp"

    def test_status_defaults_to_active(self, db_session):
        tenant = TenantORM(
            tenant_id=str(uuid.uuid4()),
            name="Test",
            slug="test-default-status",
        )
        db_session.add(tenant)
        db_session.flush()
        db_session.expire(tenant)

        result = db_session.get(TenantORM, tenant.tenant_id)
        assert result.status == "active"

    def test_region_defaults_to_global(self, db_session):
        tenant = TenantORM(
            tenant_id=str(uuid.uuid4()),
            name="Test",
            slug="test-default-region",
        )
        db_session.add(tenant)
        db_session.flush()
        db_session.expire(tenant)

        result = db_session.get(TenantORM, tenant.tenant_id)
        assert result.region == "global"

    def test_slug_unique_enforcement(self, db_session):
        slug = "unique-slug"
        t1 = TenantORM(
            tenant_id=str(uuid.uuid4()), name="First", slug=slug
        )
        t2 = TenantORM(
            tenant_id=str(uuid.uuid4()), name="Second", slug=slug
        )
        db_session.add(t1)
        db_session.flush()
        db_session.add(t2)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# Foreign key: projects.tenant_id → tenants.tenant_id
# ---------------------------------------------------------------------------


class TestProjectTenantFK:
    def test_project_with_valid_tenant(self, db_session):
        tid = str(uuid.uuid4())
        tenant = TenantORM(tenant_id=tid, name="FK Tenant", slug="fk-tenant")
        db_session.add(tenant)
        db_session.flush()

        project = ProjectORM(
            project_id=str(uuid.uuid4()),
            tenant_id=tid,
            name="FK Project",
        )
        db_session.add(project)
        db_session.flush()

        result = db_session.get(ProjectORM, project.project_id)
        assert result.tenant_id == tid

    def test_fk_constraint_exists_in_orm_metadata(self):
        """Verify the FK is declared in ORM metadata."""
        table = Base.metadata.tables["projects"]
        fk_targets = {
            str(fk.target_fullname) for fk in table.foreign_keys
        }
        assert "tenants.tenant_id" in fk_targets


# ---------------------------------------------------------------------------
# Migration 002: upgrade / downgrade
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


class TestMigration002:
    def _run_migrations(self, engine, up_to="002"):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                m001 = _load_migration("001_initial_schema.py")
                m001.upgrade()
                if up_to == "002":
                    m002 = _load_migration("002_add_tenants.py")
                    m002.upgrade()

    def test_upgrade_creates_tenants_table(self):
        engine = create_engine("sqlite:///:memory:")
        self._run_migrations(engine)

        inspector = inspect(engine)
        assert "tenants" in inspector.get_table_names()

        cols = {c["name"] for c in inspector.get_columns("tenants")}
        assert "tenant_id" in cols
        assert "slug" in cols
        assert "status" in cols

        engine.dispose()

    def test_upgrade_tenants_columns_complete(self):
        engine = create_engine("sqlite:///:memory:")
        self._run_migrations(engine)

        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("tenants")}
        expected = {
            "tenant_id",
            "name",
            "slug",
            "status",
            "region",
            "created_at",
            "updated_at",
        }
        assert expected == cols
        engine.dispose()

    def test_downgrade_drops_tenants_table(self):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        m001 = _load_migration("001_initial_schema.py")
        m002 = _load_migration("002_add_tenants.py")

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                m001.upgrade()
                m002.upgrade()
                m002.downgrade()

            inspector = inspect(engine)
            assert "tenants" not in inspector.get_table_names()
            # projects table should still exist
            assert "projects" in inspector.get_table_names()

        engine.dispose()

    def test_migration_columns_match_orm(self):
        engine = create_engine("sqlite:///:memory:")
        self._run_migrations(engine)

        inspector = inspect(engine)
        migration_cols = {
            c["name"] for c in inspector.get_columns("tenants")
        }
        orm_cols = {
            c.name for c in Base.metadata.tables["tenants"].columns
        }
        # Columns added by migrations AFTER 002 are expected to be
        # present on the ORM but absent from the 002-only migration
        # snapshot. Exclude them from the equality check.
        post_002 = {
            "ner_enabled",  # added in migration 019
            "dlp_engine",   # added in migration 043 (Phase-3c)
            "credit_markup",  # added in migration 054 (cost-based billing)
        }
        assert migration_cols == (orm_cols - post_002)
        engine.dispose()


# ---------------------------------------------------------------------------
# Domain entity alignment
# ---------------------------------------------------------------------------


class TestDomainEntityAlignment:
    """Verify domain Tenant dataclass fields align with ORM columns."""

    def test_domain_tenant_has_slug_field(self):
        field_names = {f.name for f in dc_fields(Tenant)}
        assert "slug" in field_names

    def test_domain_tenant_has_status_field(self):
        field_names = {f.name for f in dc_fields(Tenant)}
        assert "status" in field_names

    def test_domain_fields_subset_of_orm_columns(self):
        domain_fields = {f.name for f in dc_fields(Tenant)}
        orm_columns = {c.name for c in Base.metadata.tables["tenants"].columns}
        # Domain fields should be a subset of ORM columns
        # (ORM may have extra like created_at, updated_at)
        assert domain_fields.issubset(orm_columns), (
            f"Domain fields not in ORM: {domain_fields - orm_columns}"
        )

    def test_domain_tenant_defaults(self):
        t = Tenant(tenant_id="t1", name="Test")
        assert t.slug == ""
        assert t.region == "global"
        assert t.status == "active"
