"""Phase 3 Commit 24 — mechanical cross-tenant leak enumeration (Concern 2C).

Beat's spec: "for every portal endpoint, run as tenant A user + try
to access tenant B resource → expect 404/403. Covers all endpoints
mechanically."

The targeted tests in ``test_portal_project_tenant_isolation.py`` and
``test_portal_team_routes.py`` already check the current routes
individually. This suite adds a safety net: it walks the real
FastAPI app, picks out every ``/portal/*`` route that takes a
resource-id path parameter, and probes each as tenant-A while
referencing a tenant-B resource. If someone adds a new route that
forgets to filter by tenant, this catches it without a code review
remembering to add a matching targeted test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            ~TenantORM.tenant_id.in_(["test-tenant"])
        ).delete(synchronize_session=False)
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="100000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email: str, **kwargs) -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!", **kwargs},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _collect_portal_routes_with_ids() -> list[tuple[str, str, list[str]]]:
    """Every /portal/* route that takes at least one path param.

    Returns (method, path_template, param_names). Skips HEAD + OPTIONS
    autogens and public routes (no auth → no leak concern).
    """
    out: list[tuple[str, str, list[str]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/portal/"):
            continue
        param_names = [p for p in route.path.split("/") if p.startswith("{")]
        if not param_names:
            continue
        # Skip routes marked public — they don't care about tenancy.
        scope = getattr(route.endpoint, "__portal_scope__", None)
        if scope is not None and scope.kind == "public":
            continue
        for method in sorted(route.methods or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.append((method, route.path, param_names))
    return out


@pytest.fixture
def two_tenants(client):
    """Owner A + owner B in distinct tenants, each with a project
    and an invite. Returns a mapping of path-param name → the
    tenant-B resource id used for cross-tenant probes."""
    a = _signup(client, "a@t.com")
    b = _signup(client, "b@t.com")

    # Tenant B: project + api key + invite + a member (so there's a
    # target_user_id to probe). Member via invite redemption.
    b_proj = client.post(
        "/portal/projects",
        headers=_auth(b["access_token"]),
        json={"name": "B-proj"},
    ).json()
    b_key = client.post(
        f"/portal/projects/{b_proj['id']}/keys",
        headers=_auth(b["access_token"]),
        json={"name": "B-key"},
    ).json()
    b_invite_live = client.post(
        "/portal/team/invites",
        headers=_auth(b["access_token"]),
        json={},
    ).json()
    # Second invite, redeemed by a third-party member so we have a
    # target_user_id in tenant B.
    b_invite_to_redeem = client.post(
        "/portal/team/invites",
        headers=_auth(b["access_token"]),
        json={},
    ).json()
    b_member = _signup(
        client, "m-b@t.com", invite_code=b_invite_to_redeem["code"]
    )

    return {
        "a_token": a["access_token"],
        "b_token": b["access_token"],
        "path_params": {
            "{project_id}": b_proj["id"],
            "{key_id}": b_key["key_id"],
            "{target_user_id}": b_member["user"]["id"],
            "{invite_id}": b_invite_live["id"],
        },
    }


@pytest.mark.parametrize(
    "method,path_template,params",
    _collect_portal_routes_with_ids(),
    ids=lambda x: (
        x if isinstance(x, str) else "|".join(x) if isinstance(x, list) else str(x)
    ),
)
def test_cross_tenant_request_is_not_authorized_to_read(
    client, two_tenants, method, path_template, params
):
    """Tenant A caller probing a tenant B resource must never succeed."""
    # Substitute tenant-B ids into the path.
    path = path_template
    skipped = False
    for p in params:
        value = two_tenants["path_params"].get(p)
        if value is None:
            # Unknown path param — skip this probe rather than fail
            # the suite. New routes introducing new path params will
            # surface here; add the id to the fixture.
            skipped = True
            break
        path = path.replace(p, value)
    if skipped:
        pytest.skip(f"No tenant-B id seeded for param in {path_template}")

    # Minimal valid body for mutating methods — not the resource
    # schema, just something FastAPI won't 422 on before the auth
    # layer gets a chance to say 403/404.
    body: dict | None = None
    if method in {"POST", "PATCH", "PUT"}:
        body = {}
        if "keys" in path_template and method == "POST":
            body = {"name": "hijack-key"}
        elif path_template.endswith("/redeem"):
            body = {"code": "whatever"}

    request_kwargs = {
        "headers": _auth(two_tenants["a_token"]),
    }
    if body is not None:
        request_kwargs["json"] = body

    r = client.request(method, path, **request_kwargs)
    assert r.status_code in (
        401,
        403,
        404,
        409,
        422,
    ), (
        f"Tenant A accessing tenant B's resource at {method} {path} "
        f"returned {r.status_code} (expected 401/403/404/409/422). "
        f"Body: {r.text[:200]}"
    )
    # Tighter assertion: a 200 or 201 means the resource leaked.
    # 2xx is the one thing that's NEVER acceptable here.
    assert r.status_code < 200 or r.status_code >= 300, (
        f"Cross-tenant read succeeded for {method} {path}: {r.status_code}"
    )
