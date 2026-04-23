"""Phase 3 Commit 17 — migration 029 (portal tenancy) verification.

Grounded in ``PORTAL_TENANCY_MIGRATION_PLAN.md`` §4 (Option C: rename
demo-tenant-000 → Default Team, backfill users, promote oldest to
owner) and Beat's Concern 3 (production-like fixture, happy + rollback
+ idempotency, explicit verification queries).

The fixture seeds a realistic pre-migration DB (multiple portal users
with distinct ``created_at`` values, projects, api_keys, request_logs)
then runs migrations 001-029 on it and asserts the post-migration
invariants.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# Migration loader — same pattern as test_migrations.py.
# ---------------------------------------------------------------------------


def _load_migration(name: str):
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SEQUENCE_THROUGH_028 = [
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
    # 013 skipped: ALTER COLUMN TYPE not supported on SQLite; only
    # widens admin_audit_log.actor_key_id which this test doesn't
    # depend on.
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
]


def _upgrade_through_028(ctx_conn) -> None:
    """Run every migration from 001 up to (and including) 028."""
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        for name in _SEQUENCE_THROUGH_028:
            _load_migration(name).upgrade()


def _apply_029(ctx_conn) -> None:
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        _load_migration("029_portal_tenancy.py").upgrade()


def _downgrade_029(ctx_conn) -> None:
    mctx = MigrationContext.configure(ctx_conn)
    with Operations.context(mctx):
        _load_migration("029_portal_tenancy.py").downgrade()


# ---------------------------------------------------------------------------
# Production-like fixture — Concern 3.
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_migration_engine():
    """Engine seeded with realistic pre-029 state.

    * 3 portal users with explicit ``created_at`` values so the
      "promote oldest" logic has a deterministic target.
    * 2 projects owned by different portal users, both landing in
      demo-tenant-000 (the hardcoded pre-fix behaviour).
    * 3 api_keys distributed across those projects.
    * 5 request_logs tied to demo-tenant-000.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        _upgrade_through_028(conn)

        # Portal users. Created_at values are lexicographically ordered
        # so SQLite's text comparison produces the right "oldest" pick.
        conn.execute(
            text(
                "INSERT INTO portal_users "
                "(id, email, password_hash, plan, status, email_verified, "
                " credit_balance, credit_used, created_at, updated_at) VALUES "
                "('u-oldest', 'alice@example.com', 'hash', 'free_trial', "
                " 'active', 1, 0, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00'),"
                "('u-middle', 'bob@example.com',   'hash', 'free_trial', "
                " 'active', 1, 0, 0, '2026-02-01 00:00:00', '2026-02-01 00:00:00'),"
                "('u-newest', 'carol@example.com', 'hash', 'free_trial', "
                " 'active', 0, 0, 0, '2026-03-01 00:00:00', '2026-03-01 00:00:00')"
            )
        )

        # Projects — hardcoded to demo-tenant-000 per pre-migration
        # portal_projects.py behaviour. Tenant row needs to exist (027
        # seeded it already as part of _SEQUENCE_THROUGH_028).
        conn.execute(
            text(
                "INSERT INTO projects "
                "(project_id, tenant_id, name, policy_profile_id, portal_user_id) VALUES "
                "('p-alice-1', 'demo-tenant-000', 'alice-project', 'default', 'u-oldest'),"
                "('p-bob-1',   'demo-tenant-000', 'bob-project',   'default', 'u-middle')"
            )
        )

        # API keys — inherit tenant from parent project.
        conn.execute(
            text(
                "INSERT INTO api_keys "
                "(key_id, key_hash, key_prefix, tenant_id, project_id, "
                " name, role, status, portal_user_id) VALUES "
                "('k-alice-1', 'h-ka1', 'gw_a1', 'demo-tenant-000', "
                " 'p-alice-1', 'alice-key', 'admin', 'active', 'u-oldest'),"
                "('k-bob-1',   'h-kb1', 'gw_b1', 'demo-tenant-000', "
                " 'p-bob-1',   'bob-key',   'admin', 'active', 'u-middle'),"
                "('k-bob-2',   'h-kb2', 'gw_b2', 'demo-tenant-000', "
                " 'p-bob-1',   'bob-key-2', 'admin', 'active', 'u-middle')"
            )
        )

        # Request logs — tenant_id has no FK pre-030, so we can seed
        # freely. All five rows already point at a valid tenant.
        conn.execute(
            text(
                "INSERT INTO request_logs "
                "(request_id, tenant_id, project_id, provider, model, "
                " status, policy_action, latency_ms) VALUES "
                "('r-1', 'demo-tenant-000', 'p-alice-1', 'openai', 'gpt-4o', "
                " 'success', 'allow', 100),"
                "('r-2', 'demo-tenant-000', 'p-alice-1', 'openai', 'gpt-4o', "
                " 'success', 'allow', 120),"
                "('r-3', 'demo-tenant-000', 'p-bob-1',   'openai', 'gpt-4o', "
                " 'success', 'allow', 150),"
                "('r-4', 'demo-tenant-000', 'p-bob-1',   'openai', 'gpt-4o', "
                " 'error',   'allow', 200),"
                "('r-5', 'demo-tenant-000', 'p-bob-1',   'openai', 'gpt-4o', "
                " 'success', 'allow', 180)"
            )
        )

    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Happy path — migration 029 upgrade.
# ---------------------------------------------------------------------------


class TestMigration029Upgrade:
    def test_tenant_renamed_to_default_team(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            row = conn.execute(
                text(
                    "SELECT name, slug FROM tenants "
                    "WHERE tenant_id = 'demo-tenant-000'"
                )
            ).fetchone()
        assert row is not None
        assert row[0] == "Default Team"
        assert row[1] == "default-team"

    def test_portal_users_gain_tenant_columns(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
        cols = {
            c["name"]
            for c in inspect(pre_migration_engine).get_columns("portal_users")
        }
        assert {"tenant_id", "tenant_role", "token_version"}.issubset(cols)

    def test_tenant_invites_table_created(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
        inspector = inspect(pre_migration_engine)
        assert "tenant_invites" in inspector.get_table_names()
        cols = {c["name"] for c in inspector.get_columns("tenant_invites")}
        assert cols == {
            "id",
            "tenant_id",
            "code",
            "created_by",
            "created_at",
            "expires_at",
            "used_at",
            "used_by",
            "email_hint",
        }

    def test_backfill_assigns_tenant_id_to_every_existing_user(
        self, pre_migration_engine
    ):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            rows = conn.execute(
                text("SELECT id, tenant_id FROM portal_users ORDER BY id")
            ).fetchall()
        # All three users moved into demo-tenant-000, no NULLs left.
        assert len(rows) == 3
        for uid, tid in rows:
            assert tid == "demo-tenant-000", f"{uid} missing tenant_id backfill"

    def test_oldest_user_promoted_to_owner(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            rows = conn.execute(
                text(
                    "SELECT id, tenant_role FROM portal_users "
                    "ORDER BY created_at ASC"
                )
            ).fetchall()
        assert rows[0] == ("u-oldest", "owner")
        # Everyone else keeps the default 'member' role.
        assert rows[1] == ("u-middle", "member")
        assert rows[2] == ("u-newest", "member")

    def test_token_version_bumped_to_invalidate_pre_migration_jwts(
        self, pre_migration_engine
    ):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            versions = [
                row[0]
                for row in conn.execute(
                    text("SELECT token_version FROM portal_users")
                ).fetchall()
            ]
        # All bumped to 2 so tokens minted pre-migration (implicit v0 or
        # explicit v1) fail verification.
        assert all(v == 2 for v in versions)

    def test_projects_and_api_keys_untouched(self, pre_migration_engine):
        """Option C promise: no row rewrites on the big tables."""
        with pre_migration_engine.begin() as conn:
            pre_projects = conn.execute(
                text("SELECT project_id, tenant_id FROM projects ORDER BY project_id")
            ).fetchall()
            pre_keys = conn.execute(
                text("SELECT key_id, tenant_id FROM api_keys ORDER BY key_id")
            ).fetchall()
            pre_logs = conn.execute(
                text("SELECT request_id, tenant_id FROM request_logs ORDER BY request_id")
            ).fetchall()

            _apply_029(conn)

            post_projects = conn.execute(
                text("SELECT project_id, tenant_id FROM projects ORDER BY project_id")
            ).fetchall()
            post_keys = conn.execute(
                text("SELECT key_id, tenant_id FROM api_keys ORDER BY key_id")
            ).fetchall()
            post_logs = conn.execute(
                text("SELECT request_id, tenant_id FROM request_logs ORDER BY request_id")
            ).fetchall()

        assert pre_projects == post_projects
        assert pre_keys == post_keys
        assert pre_logs == post_logs


# ---------------------------------------------------------------------------
# Empty-DB case — fresh install where no portal users exist yet.
# ---------------------------------------------------------------------------


class TestMigration029FreshInstall:
    def test_migration_succeeds_on_empty_portal_users(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as conn:
                _upgrade_through_028(conn)
                _apply_029(conn)

                # No users, no owner promotion — but the tenant still
                # got renamed.
                row = conn.execute(
                    text(
                        "SELECT name FROM tenants WHERE tenant_id = 'demo-tenant-000'"
                    )
                ).fetchone()
                assert row[0] == "Default Team"
        finally:
            engine.dispose()

    def test_migration_succeeds_when_demo_tenant_row_missing(self):
        """Defensive path: if migration 027 didn't run, we still land."""
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as conn:
                # Run everything except 027.
                mctx = MigrationContext.configure(conn)
                with Operations.context(mctx):
                    for name in _SEQUENCE_THROUGH_028:
                        if name == "027_seed_demo_tenant.py":
                            continue
                        _load_migration(name).upgrade()
                    _load_migration("029_portal_tenancy.py").upgrade()

                row = conn.execute(
                    text(
                        "SELECT name, slug FROM tenants "
                        "WHERE tenant_id = 'demo-tenant-000'"
                    )
                ).fetchone()
            assert row is not None
            assert row[0] == "Default Team"
            assert row[1] == "default-team"
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Rollback — downgrade must reverse schema cleanly.
# ---------------------------------------------------------------------------


class TestMigration029Downgrade:
    def test_downgrade_drops_tenant_invites(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            tables_after_upgrade = set(
                conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                .scalars()
                .all()
            )
            assert "tenant_invites" in tables_after_upgrade
            _downgrade_029(conn)
            tables_after_downgrade = set(
                conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                .scalars()
                .all()
            )
        assert "tenant_invites" not in tables_after_downgrade

    def test_downgrade_removes_portal_user_tenant_columns(
        self, pre_migration_engine
    ):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            _downgrade_029(conn)
            cols = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA table_info(portal_users)")
                ).fetchall()
            }
        assert "tenant_id" not in cols
        assert "tenant_role" not in cols
        assert "token_version" not in cols

    def test_downgrade_preserves_project_and_key_rows(self, pre_migration_engine):
        """Downgrade must not cascade-delete user data."""
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            _downgrade_029(conn)
            projects = conn.execute(
                text("SELECT COUNT(*) FROM projects")
            ).scalar()
            keys = conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar()
            logs = conn.execute(
                text("SELECT COUNT(*) FROM request_logs")
            ).scalar()
        assert projects == 2
        assert keys == 3
        assert logs == 5

    def test_downgrade_restores_original_tenant_name(self, pre_migration_engine):
        with pre_migration_engine.begin() as conn:
            _apply_029(conn)
            _downgrade_029(conn)
            row = conn.execute(
                text(
                    "SELECT name, slug FROM tenants "
                    "WHERE tenant_id = 'demo-tenant-000'"
                )
            ).fetchone()
        assert row[0] == "Demo Tenant"
        assert row[1] == "demo"
