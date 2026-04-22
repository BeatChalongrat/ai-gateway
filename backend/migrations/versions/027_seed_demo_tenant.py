"""TEMPORARY WORKAROUND for P0-12 (onboarding crash on fresh DB).

Root cause is P0-4: ``POST /portal/projects`` hard-codes
``tenant_id="demo-tenant-000"``. On a fresh install that tenant does not
exist, so the first portal-user project creation fails with an
``IntegrityError`` that bubbles up as HTTP 500. Portal onboarding is
100% broken out-of-the-box until someone runs ``scripts.seed_demo_data``.

This migration idempotently inserts ``demo-tenant-000`` so signup →
create-first-project works on any freshly migrated DB.

REMOVE THIS MIGRATION after ``PORTAL_TENANCY_MIGRATION_PLAN.md`` is
executed and portal signup provisions a real per-user or per-org
tenant. Tracking: Phase 2-0 (2026-04-22), see FUNCTIONAL_AUDIT_REPORT
P0-4 / P0-12 and PHASE1.4_LIVE_TEST_REPORT.

Revision ID: 027
Revises: 026
Create Date: 2026-04-22
"""

from alembic import op


revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ON CONFLICT DO NOTHING keeps this idempotent across repeated runs
    # and coexists with any prior hand-seeded demo data.
    op.execute(
        """
        INSERT INTO tenants (tenant_id, name, slug, status, region, ner_enabled)
        VALUES ('demo-tenant-000', 'Demo Tenant', 'demo', 'active', 'global', false)
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Projects FK-referencing this tenant will prevent removal; that's
    # intentional — downgrading past this revision should be rare and
    # the operator should clean up demo data first.
    op.execute("DELETE FROM tenants WHERE tenant_id = 'demo-tenant-000'")
