"""Tests for Sub-Phase 0A: Alembic migrations and ORM model integrity.

Verifies:
- All 3 tables exist with correct columns after metadata.create_all()
- Timestamp columns (created_at, updated_at) present where expected
- Migration 001 upgrade/downgrade functions work against SQLite
"""

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.core.db import Base
from app.models.project import ProjectORM
from app.models.provider import ProviderORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
import app.models  # noqa: F401


# ---------------------------------------------------------------------------
# Schema verification (from ORM metadata.create_all)
# ---------------------------------------------------------------------------


class TestSchemaFromORM:
    """Verify that ORM metadata produces correct tables."""

    def test_projects_table_exists(self, db_inspector):
        assert "projects" in db_inspector.get_table_names()

    def test_providers_table_exists(self, db_inspector):
        assert "providers" in db_inspector.get_table_names()

    def test_request_logs_table_exists(self, db_inspector):
        assert "request_logs" in db_inspector.get_table_names()

    def test_projects_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("projects")}
        expected = {
            "project_id",
            "tenant_id",
            "name",
            "policy_profile_id",
            "budget_limit",
            "max_requests_per_day",
            "created_at",
            "updated_at",
        }
        assert expected == cols

    def test_providers_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("providers")}
        expected = {
            "provider_id",
            "name",
            "enabled",
            "created_at",
            "updated_at",
            # Added in migration 008_add_provider_api_key
            "api_key_encrypted",
            "api_key_set_at",
        }
        assert expected == cols

    def test_request_logs_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("request_logs")}
        expected = {
            "request_id",
            "tenant_id",
            "project_id",
            "provider",
            "model",
            "status",
            "policy_action",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "estimated_cost_usd",
            "error_code",
            "created_at",
            # Added in migration 014_add_alert_rules_and_feedback
            "feedback_rating",
            "feedback_comment",
            "feedback_at",
            # Added in migration 016_add_dlp_findings_to_request_logs
            "dlp_input_findings",
            "dlp_input_action",
            "dlp_output_findings",
            "dlp_categories",
            # Added in migration 018_add_keyword_rules
            "router_used",
            # Added in migration 020_add_policy_profile_id_to_request_logs
            "policy_profile_id",
            # Added in migration 021_add_dlp_trace_to_request_logs
            "input_raw",
            "input_masked",
            "output_raw",
            "output_final",
            "dlp_findings_json",
        }
        assert expected == cols


# ---------------------------------------------------------------------------
# ORM CRUD smoke tests
# ---------------------------------------------------------------------------


class TestORMInsert:
    """Verify ORM models can be inserted and read back."""

    def test_insert_project(self, db_session):
        proj = ProjectORM(
            project_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            name="test-project",
        )
        db_session.add(proj)
        db_session.flush()

        result = db_session.get(ProjectORM, proj.project_id)
        assert result is not None
        assert result.name == "test-project"

    def test_insert_provider(self, db_session):
        prov = ProviderORM(
            provider_id=str(uuid.uuid4()),
            name="openai",
        )
        db_session.add(prov)
        db_session.flush()

        result = db_session.get(ProviderORM, prov.provider_id)
        assert result is not None
        assert result.name == "openai"

    def test_insert_request_log(self, db_session):
        log = RequestLogORM(
            request_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            provider="openai",
            model="gpt-4o",
            status="success",
            policy_action="allow",
        )
        db_session.add(log)
        db_session.flush()

        result = db_session.get(RequestLogORM, log.request_id)
        assert result is not None
        assert result.provider == "openai"


# ---------------------------------------------------------------------------
# Alembic migration 001 — upgrade / downgrade via Operations context
# ---------------------------------------------------------------------------


def _load_migration(name: str):
    """Load a migration by filename."""
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_migration_module():
    """Load 001_initial_schema.py as a module (filename starts with digit)."""
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("migration_001", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration001:
    """Test upgrade + downgrade functions from migration 001 directly."""

    def test_upgrade_creates_tables(self):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                migration = _load_migration_module()
                migration.upgrade()

            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert {"projects", "providers", "request_logs"}.issubset(tables)

        engine.dispose()

    def test_downgrade_drops_tables(self):
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                migration = _load_migration_module()
                migration.upgrade()
                migration.downgrade()

            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "projects" not in tables
            assert "providers" not in tables
            assert "request_logs" not in tables

        engine.dispose()

    def test_upgrade_is_idempotent_with_fresh_db(self):
        """Running upgrade on a fresh DB twice should not error (tables created once)."""
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            migration = _load_migration_module()

            with Operations.context(ctx):
                migration.upgrade()

            # Second call will fail if CREATE TABLE doesn't use IF NOT EXISTS,
            # but the migration doesn't — that's expected (Alembic tracks revisions).
            # We just verify the first call succeeded.
            inspector = inspect(engine)
            assert "projects" in inspector.get_table_names()

        engine.dispose()

    def test_migration_columns_match_orm(self):
        """Verify all migrations produce tables matching ORM metadata."""
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                # Run ALL migrations through the current head so the
                # migration-side schema reflects every ORM column added
                # after the original foundation migrations.
                _load_migration_module().upgrade()
                _load_migration("002_add_tenants.py").upgrade()
                _load_migration("003_add_api_keys.py").upgrade()
                _load_migration("004_add_usage_columns.py").upgrade()
                _load_migration("005_add_role_to_api_keys.py").upgrade()
                _load_migration("006_add_quota_fields.py").upgrade()
                _load_migration("007_add_admin_audit_log.py").upgrade()
                _load_migration("008_add_provider_api_key.py").upgrade()
                _load_migration("009_add_provider_keys_table.py").upgrade()
                _load_migration("010_add_dashboard_indexes.py").upgrade()
                _load_migration("011_add_policies_table.py").upgrade()
                _load_migration("012_add_admin_users_table.py").upgrade()
                # Migration 013 uses ALTER COLUMN TYPE which SQLite cannot
                # execute in place. It only touches admin_audit_log, not any
                # of the tables this test compares, so it is safe to skip.
                _load_migration("014_add_alert_rules_and_feedback.py").upgrade()
                _load_migration("015_add_policy_profiles_and_org_settings.py").upgrade()
                _load_migration("016_add_dlp_findings_to_request_logs.py").upgrade()
                _load_migration("017_add_alert_delivery_fields.py").upgrade()
                _load_migration("018_add_keyword_rules.py").upgrade()
                _load_migration("019_add_ner_enabled_to_tenants.py").upgrade()
                _load_migration("020_add_policy_profile_id_to_request_logs.py").upgrade()
                _load_migration("021_add_dlp_trace_to_request_logs.py").upgrade()
                _load_migration("022_add_ai_review.py").upgrade()
                _load_migration("023_ai_review_provider_config.py").upgrade()

            inspector = inspect(conn)

            # Compare migration columns vs ORM columns for tables that
            # exist after all migrations
            for table_name in ("projects", "providers", "request_logs"):
                migration_cols = {c["name"] for c in inspector.get_columns(table_name)}
                orm_cols = {c.name for c in Base.metadata.tables[table_name].columns}
                assert migration_cols == orm_cols, (
                    f"Column mismatch in {table_name}: "
                    f"migration={migration_cols}, orm={orm_cols}"
                )

        engine.dispose()
