"""Phase 3 Commit 18 — migration 030 (finalize portal tenancy).

Verifies:
- ``portal_users.tenant_id`` becomes NOT NULL after backfill.
- ``request_logs.tenant_id`` gains an FK to ``tenants.tenant_id``.
- Orphan ``request_logs.tenant_id`` values are resolved via
  placeholder tenant rows so the FK can land without data loss
  (design doc §12 R3).
- Migration refuses to run if ``portal_users`` still has NULL
  tenant_id — belt to go with 029's braces.
- Downgrade reverses FK and NOT NULL without destroying data.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


def _load_migration(name: str):
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SEQUENCE_THROUGH_029 = [
    "001_initial_schema.py",
    "002_add_tenants.py",
    "003_add_api_keys.py",
    "004_add_usage_columns.py",
    "005_add_role_to_api_keys.py",
    "006_add_quota_fields.py",
    "007_add_admin_audit_log.py",
    "008_add_provider_api_key.py",
    "009_add_provider_keys_table.py",
    "010_add_dashboard_indexes.py",
    "011_add_policies_table.py",
    "012_add_admin_users_table.py",
    "014_add_alert_rules_and_feedback.py",
    "015_add_policy_profiles_and_org_settings.py",
    "016_add_dlp_findings_to_request_logs.py",
    "017_add_alert_delivery_fields.py",
    "018_add_keyword_rules.py",
    "019_add_ner_enabled_to_tenants.py",
    "020_add_policy_profile_id_to_request_logs.py",
    "021_add_dlp_trace_to_request_logs.py",
    "022_add_ai_review.py",
    "023_ai_review_provider_config.py",
    "024_system_settings.py",
    "025_portal_users.py",
    "026_portal_users_oauth.py",
    "027_seed_demo_tenant.py",
    "028_admin_audit_log_extensions.py",
    "029_portal_tenancy.py",
]


def _upgrade_through_029(ctx_conn) -> None:
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        for name in _SEQUENCE_THROUGH_029:
            _load_migration(name).upgrade()


def _apply_030(ctx_conn) -> None:
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        _load_migration("030_portal_tenancy_finalize.py").upgrade()


def _downgrade_030(ctx_conn) -> None:
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        _load_migration("030_portal_tenancy_finalize.py").downgrade()


@pytest.fixture()
def post_029_engine():
    """Engine already at migration 029 head with realistic seed data."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        _upgrade_through_029(conn)
        # One portal user so the NOT NULL flip has something to act on.
        conn.execute(
            text(
                "INSERT INTO portal_users "
                "(id, email, password_hash, plan, status, email_verified, "
                " credit_balance, credit_used, tenant_id, tenant_role, token_version, "
                " created_at, updated_at) VALUES "
                "('u-1', 'a@example.com', 'hash', 'free_trial', 'active', 1, "
                " 0, 0, 'demo-tenant-000', 'owner', 2, "
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        # Seed a request_log pointing at a VALID tenant (no orphan).
        conn.execute(
            text(
                "INSERT INTO request_logs "
                "(request_id, tenant_id, project_id, provider, model, "
                " status, policy_action, latency_ms) VALUES "
                "('r-ok', 'demo-tenant-000', 'p-x', 'openai', 'gpt-4o', "
                " 'success', 'allow', 100)"
            )
        )
    yield engine
    engine.dispose()


class TestMigration030Upgrade:
    def test_portal_users_tenant_id_is_not_null_after_upgrade(
        self, post_029_engine
    ):
        with post_029_engine.begin() as conn:
            _apply_030(conn)
            cols = conn.execute(
                text("PRAGMA table_info(portal_users)")
            ).fetchall()
        tenant_id_col = next(c for c in cols if c[1] == "tenant_id")
        # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk).
        # notnull == 1 means the column is NOT NULL.
        assert tenant_id_col[3] == 1, "tenant_id should be NOT NULL after 030"

    def test_request_logs_tenant_id_has_foreign_key(self, post_029_engine):
        with post_029_engine.begin() as conn:
            _apply_030(conn)
            fks = conn.execute(
                text("PRAGMA foreign_key_list(request_logs)")
            ).fetchall()
        # Expect exactly one FK pointing at tenants(tenant_id).
        matching = [
            fk for fk in fks if fk[2] == "tenants" and fk[3] == "tenant_id"
        ]
        assert len(matching) == 1

    def test_migration_refuses_to_run_when_portal_users_have_null_tenant(self):
        """Belt-and-braces: the backfill in 029 should have caught this,
        but if someone hand-edited between migrations, 030 must fail loud."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            with engine.begin() as conn:
                _upgrade_through_029(conn)
                # Manually NULL out a tenant_id to simulate a drifted row.
                conn.execute(
                    text(
                        "INSERT INTO portal_users "
                        "(id, email, password_hash, plan, status, "
                        " email_verified, credit_balance, credit_used, "
                        " tenant_id, tenant_role, token_version, "
                        " created_at, updated_at) VALUES "
                        "('u-null', 'n@example.com', 'hash', 'free_trial', "
                        " 'active', 1, 0, 0, NULL, 'member', 1, "
                        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                    )
                )
            with engine.begin() as conn:
                with pytest.raises(RuntimeError, match="NULL tenant_id"):
                    _apply_030(conn)
        finally:
            engine.dispose()


class TestMigration030OrphanPrescan:
    def test_orphan_tenant_id_gets_placeholder_and_fk_lands(self):
        """A request_log whose tenant_id is missing from tenants should
        trigger a placeholder-tenant insert, then the FK can be created
        without violating the constraint."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            with engine.begin() as conn:
                _upgrade_through_029(conn)
                # Seed a portal user so NOT NULL flip passes.
                conn.execute(
                    text(
                        "INSERT INTO portal_users "
                        "(id, email, password_hash, plan, status, "
                        " email_verified, credit_balance, credit_used, "
                        " tenant_id, tenant_role, token_version, "
                        " created_at, updated_at) VALUES "
                        "('u-1', 'a@example.com', 'hash', 'free_trial', "
                        " 'active', 1, 0, 0, 'demo-tenant-000', 'owner', 2, "
                        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                    )
                )
                # Orphan log — no matching tenant row.
                conn.execute(
                    text(
                        "INSERT INTO request_logs "
                        "(request_id, tenant_id, project_id, provider, model, "
                        " status, policy_action, latency_ms) VALUES "
                        "('r-orphan', 'ghost-tenant-123', 'p-x', 'openai', "
                        " 'gpt-4o', 'success', 'allow', 100)"
                    )
                )
                _apply_030(conn)
                # Placeholder tenant inserted.
                placeholder = conn.execute(
                    text(
                        "SELECT name, status FROM tenants "
                        "WHERE tenant_id = 'ghost-tenant-123'"
                    )
                ).fetchone()
                assert placeholder is not None
                assert "orphan-log-tenant" in placeholder[0]
                assert placeholder[1] == "inactive"
                # FK now landed.
                fks = conn.execute(
                    text("PRAGMA foreign_key_list(request_logs)")
                ).fetchall()
                assert any(
                    fk[2] == "tenants" and fk[3] == "tenant_id" for fk in fks
                )
        finally:
            engine.dispose()


class TestMigration030Downgrade:
    def test_downgrade_reverts_not_null(self, post_029_engine):
        with post_029_engine.begin() as conn:
            _apply_030(conn)
            _downgrade_030(conn)
            cols = conn.execute(
                text("PRAGMA table_info(portal_users)")
            ).fetchall()
        tenant_id_col = next(c for c in cols if c[1] == "tenant_id")
        assert tenant_id_col[3] == 0, "tenant_id should be nullable again"

    def test_downgrade_drops_request_logs_fk(self, post_029_engine):
        with post_029_engine.begin() as conn:
            _apply_030(conn)
            _downgrade_030(conn)
            fks = conn.execute(
                text("PRAGMA foreign_key_list(request_logs)")
            ).fetchall()
        matching = [
            fk for fk in fks if fk[2] == "tenants" and fk[3] == "tenant_id"
        ]
        assert matching == []

    def test_downgrade_preserves_data(self, post_029_engine):
        with post_029_engine.begin() as conn:
            _apply_030(conn)
            _downgrade_030(conn)
            users = conn.execute(
                text("SELECT COUNT(*) FROM portal_users")
            ).scalar()
            logs = conn.execute(
                text("SELECT COUNT(*) FROM request_logs")
            ).scalar()
        assert users == 1
        assert logs == 1
