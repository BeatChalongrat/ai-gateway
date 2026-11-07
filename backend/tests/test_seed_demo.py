"""P4-08: Seed/demo data tests.

Verifies:
- seed() creates tenant, project, and API key
- seed() is idempotent (re-running skips existing)
- Seeded data has expected IDs and values
- Raw key is only returned on first creation
"""

import pytest

from app.models.api_key import ApiKeyORM
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from scripts.seed_demo_data import (
    DEMO_KEY_NAME,
    DEMO_PROJECT_ID,
    DEMO_TENANT_ID,
    DEMO_TENANT_SLUG,
    seed,
)
import app.models  # noqa: F401


class TestSeedDemo:
    def test_creates_tenant(self, db_session):
        result = seed(db_session)
        assert result["tenant_created"] is True
        tenant = db_session.get(TenantORM, DEMO_TENANT_ID)
        assert tenant is not None
        assert tenant.slug == DEMO_TENANT_SLUG

    def test_creates_project(self, db_session):
        result = seed(db_session)
        assert result["project_created"] is True
        project = db_session.get(ProjectORM, DEMO_PROJECT_ID)
        assert project is not None
        assert project.name == "Demo Project"
        assert project.policy_profile_id == "default"

    def test_creates_api_key(self, db_session):
        result = seed(db_session)
        assert result["key_created"] is True
        assert result["raw_key"] is not None
        assert result["raw_key"].startswith("gw_")

    def test_idempotent_tenant(self, db_session):
        seed(db_session)
        result2 = seed(db_session)
        assert result2["tenant_created"] is False

    def test_idempotent_project(self, db_session):
        seed(db_session)
        result2 = seed(db_session)
        assert result2["project_created"] is False

    def test_idempotent_key(self, db_session):
        seed(db_session)
        result2 = seed(db_session)
        assert result2["key_created"] is False
        assert result2["raw_key"] is None

    def test_raw_key_only_on_first_creation(self, db_session):
        r1 = seed(db_session)
        r2 = seed(db_session)
        assert r1["raw_key"] is not None
        assert r2["raw_key"] is None

    def test_seeded_tenant_is_active(self, db_session):
        seed(db_session)
        tenant = db_session.get(TenantORM, DEMO_TENANT_ID)
        assert tenant.status == "active"
