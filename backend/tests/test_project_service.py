"""P4-05: DB-backed ProjectService tests.

Verifies:
- resolve_project_context returns RequestContext from DB
- Missing project raises NotFoundError
- Policy profile field populated when set
- Policy profile field is None when not set
- request_id propagated through context
"""

import uuid

import pytest

from app.api.deps import ResolvedApiKey
from app.core.exceptions import NotFoundError
from app.domain.entities import RequestContext
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.project_service import ProjectService
import app.models  # noqa: F401


@pytest.fixture()
def project_service():
    return ProjectService()


def _seed_tenant(session, tenant_id=None):
    tid = tenant_id or str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    session.flush()
    return tid


def _seed_project(session, tenant_id, project_id=None, **kwargs):
    pid = project_id or str(uuid.uuid4())
    session.add(
        ProjectORM(
            project_id=pid,
            tenant_id=tenant_id,
            name=kwargs.get("name", "Test Project"),
            policy_profile_id=kwargs.get("policy_profile_id"),
        )
    )
    session.flush()
    return pid


def _make_auth(tenant_id, project_id):
    return ResolvedApiKey(
        key_id="k1",
        tenant_id=tenant_id,
        project_id=project_id,
        key_prefix="gw_test_",
    )


class TestResolveProjectContext:
    def test_returns_request_context(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid = _seed_project(
            db_session, tid, name="My Project", policy_profile_id="strict"
        )
        auth = _make_auth(tid, pid)

        ctx = project_service.resolve_project_context(db_session, auth)
        assert isinstance(ctx, RequestContext)
        assert ctx.tenant_id == tid
        assert ctx.project_id == pid
        assert ctx.project_name == "My Project"
        assert ctx.policy_profile_id == "strict"

    def test_missing_project_raises_not_found(self, db_session, project_service):
        auth = _make_auth("t1", "nonexistent-project")
        with pytest.raises(NotFoundError) as exc_info:
            project_service.resolve_project_context(db_session, auth)
        assert "nonexistent-project" in str(exc_info.value.message)

    def test_policy_profile_none_when_not_set(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid = _seed_project(db_session, tid, policy_profile_id=None)
        auth = _make_auth(tid, pid)

        ctx = project_service.resolve_project_context(db_session, auth)
        assert ctx.policy_profile_id is None

    def test_policy_profile_populated(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid = _seed_project(db_session, tid, policy_profile_id="internal-only")
        auth = _make_auth(tid, pid)

        ctx = project_service.resolve_project_context(db_session, auth)
        assert ctx.policy_profile_id == "internal-only"

    def test_returns_correct_tenant_id_from_db(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid = _seed_project(db_session, tid)
        auth = _make_auth("different-tenant", pid)

        ctx = project_service.resolve_project_context(db_session, auth)
        assert ctx.tenant_id == tid

    def test_request_id_propagated(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid = _seed_project(db_session, tid)
        auth = _make_auth(tid, pid)

        ctx = project_service.resolve_project_context(
            db_session, auth, request_id="req_abc123"
        )
        assert ctx.request_id == "req_abc123"

    def test_multiple_projects_resolve_correctly(self, db_session, project_service):
        tid = _seed_tenant(db_session)
        pid1 = _seed_project(
            db_session, tid, name="Project A", policy_profile_id="default"
        )
        pid2 = _seed_project(
            db_session, tid, name="Project B", policy_profile_id="strict"
        )

        ctx1 = project_service.resolve_project_context(
            db_session, _make_auth(tid, pid1)
        )
        ctx2 = project_service.resolve_project_context(
            db_session, _make_auth(tid, pid2)
        )

        assert ctx1.project_name == "Project A"
        assert ctx1.policy_profile_id == "default"
        assert ctx2.project_name == "Project B"
        assert ctx2.policy_profile_id == "strict"
