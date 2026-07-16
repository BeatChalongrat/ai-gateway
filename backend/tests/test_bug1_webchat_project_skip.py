"""FIX 6 / Bug 1 — the web-chat service-JWT path resolves a tenant-scoped
context and does NOT require a project row (billing charges the user).

Before: the service-JWT resolver set project_id="__webchat__", which
project_service looked up as a real ProjectORM -> NotFoundError -> every
web-chat completion 502'd. Now internal_trusted callers skip the project
lookup entirely and build the context from the tenant + default DLP profile.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.project_service import ProjectService


class _FakeAsyncSession:
    """session.get(Model, pk): tenants resolve, projects raise if touched."""

    def __init__(self, tenant):
        self._tenant = tenant
        self.project_lookups = 0

    async def get(self, model, pk):
        name = model.__name__
        if name == "TenantORM":
            return self._tenant
        if name == "OrgSettingsORM":
            return SimpleNamespace(ner_enabled=False)
        if name == "ProjectORM":
            self.project_lookups += 1
            return None  # a project lookup would produce the old 404
        return None


def _auth(**kw):
    base = dict(
        key_id="svc:u1", tenant_id="acme-corp-001", project_id="__webchat__",
        internal_trusted=True, portal_user_id="u1", log_user_id="u1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_service_jwt_path_skips_project_lookup():
    tenant = SimpleNamespace(tenant_id="acme-corp-001", region="apac", ner_enabled=True)
    session = _FakeAsyncSession(tenant)

    ctx = await ProjectService().resolve_project_context_async(
        session, _auth(), request_id="r1",
    )

    assert session.project_lookups == 0  # NEVER looks up the phantom project
    assert ctx.tenant_id == "acme-corp-001"
    assert ctx.region == "apac"
    assert ctx.ner_enabled is True            # tenant flag honored
    assert ctx.policy_profile_id == "default"  # default DLP profile (masks)
    assert ctx.user_id == "u1"


@pytest.mark.asyncio
async def test_service_jwt_path_org_ner_fallback():
    # tenant NER off but org default on -> ner_enabled True.
    tenant = SimpleNamespace(tenant_id="t", region="global", ner_enabled=False)

    class _Sess(_FakeAsyncSession):
        async def get(self, model, pk):
            if model.__name__ == "OrgSettingsORM":
                return SimpleNamespace(ner_enabled=True)
            return await super().get(model, pk)

    ctx = await ProjectService().resolve_project_context_async(
        _Sess(tenant), _auth(), request_id="r2",
    )
    assert ctx.ner_enabled is True


@pytest.mark.asyncio
async def test_non_internal_caller_still_requires_project():
    # A normal gw_-key caller (internal_trusted absent/False) must still hit
    # the project lookup -> NotFoundError on a missing project (unchanged).
    from app.core.exceptions import NotFoundError

    session = _FakeAsyncSession(SimpleNamespace(tenant_id="t", region="g", ner_enabled=False))
    with pytest.raises(NotFoundError):
        await ProjectService().resolve_project_context_async(
            session, _auth(internal_trusted=False, project_id="proj-does-not-exist"),
            request_id="r3",
        )
    assert session.project_lookups == 1
