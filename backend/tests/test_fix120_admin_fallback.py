"""FIX120 — the admin API over the fallback policy: read the chains and the
counts, flip the kill switch, store and remove overrides — all through the
same validation the runtime uses, all audited.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.models.admin_audit_log import AdminAuditLogORM
from app.models.system_setting import SystemSettingORM
from app.services.gateway import fallback_policy as fp
from tests.conftest import _IntegrationSession


@pytest.fixture
def client():
    fp.clear_cache()
    with TestClient(main_app) as c:
        yield c
    # leave no rows behind for the next test module
    with _IntegrationSession() as s:
        for key in ("fallback_enabled", "fallback_policy"):
            row = s.get(SystemSettingORM, key)
            if row is not None:
                s.delete(row)
        for row in s.query(SystemSettingORM).filter(SystemSettingORM.key.like("fallback_policy:tenant:%")).all():
            s.delete(row)
        s.commit()
    fp.clear_cache()


def _known_model_pair():
    from app.services.providers.model_registry import get_all_candidates

    cands = list(get_all_candidates())
    assert len(cands) >= 2
    return cands[0], cands[1]


def test_policy_read_shows_the_file_the_switch_and_no_overrides(client):
    r = client.get("/admin/fallback/policy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kill_switch"] == {"setting_key": "fallback_enabled", "enabled": True, "stored_value": None, "updated_at": None}
    assert len(body["contract_sha256"]) == 64
    assert body["actions"][-1] == "give_up"
    assert body["deployment_override"] is None and body["tenant_overrides"] == []
    assert body["effective"]["limits"]["stream"]["total_budget_s"] is None
    assert body["breaker"]["enabled"] is False and body["cost_cap"]["enabled"] is False
    assert "proposals" not in body


def test_chains_read_counts_models_with_and_without_a_chain(client):
    r = client.get("/admin/fallback/chains?include_inactive=true")
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["summary"]
    assert s["total"] == s["with_chain"] + s["without_chain"] == len(body["models"])
    assert body["kill_switch_enabled"] is True
    for row in body["models"]:
        assert set(row) == {"provider", "model", "has_chain", "chain", "source"}
        assert row["has_chain"] == bool(row["chain"])
        assert row["source"] in ("none", "curated", "tier", "override")
    expected = fp.coverage(chat_only=False)["summary"]
    assert s == expected


def test_kill_switch_flips_without_a_deploy_and_is_audited(client):
    r = client.put("/admin/fallback/kill-switch", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["stored_value"] == "false"
    assert client.get("/admin/fallback/policy").json()["kill_switch"]["enabled"] is False
    assert client.get("/admin/fallback/chains").json()["kill_switch_enabled"] is False
    r = client.put("/admin/fallback/kill-switch", json={"enabled": True})
    assert r.json()["enabled"] is True
    assert client.get("/admin/fallback/policy").json()["kill_switch"]["enabled"] is True
    with _IntegrationSession() as s:
        rows = s.query(AdminAuditLogORM).filter(AdminAuditLogORM.action == "fallback.kill_switch").all()
        assert len(rows) >= 2
        assert json.loads(rows[-1].details) == {"enabled": True}
        assert rows[-1].resource_id == "fallback_enabled"


def test_deployment_override_adds_a_chain_and_shows_as_override(client):
    a, b = _known_model_pair()
    body = {"override": {"chains": {"curated": {a.model: [{"provider": b.provider, "model": b.model}]}}}}
    r = client.put("/admin/fallback/overrides/deployment", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["setting_key"] == "fallback_policy"
    pol = client.get("/admin/fallback/policy").json()
    assert pol["deployment_override"]["override"] == body["override"] and pol["deployment_override"]["error"] is None
    chains = client.get("/admin/fallback/chains?include_inactive=true").json()
    row = next(m for m in chains["models"] if m["model"] == a.model)
    assert row["source"] == "override" and row["chain"] == [{"provider": b.provider, "model": b.model}]
    r = client.delete("/admin/fallback/overrides/deployment")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/admin/fallback/policy").json()["deployment_override"] is None


def test_tenant_override_sets_a_boundary_and_the_effective_view_shows_it(client):
    r = client.put("/admin/fallback/overrides/tenant/t-acme", json={"override": {"allowed_providers": ["sranai"], "opt_in": {"context_window_fallback": True}}})
    assert r.status_code == 200, r.text
    pol = client.get("/admin/fallback/policy?tenant_id=t-acme").json()
    assert pol["tenant_overrides"] == [{"tenant_id": "t-acme", "override": {"allowed_providers": ["sranai"], "opt_in": {"context_window_fallback": True}}, "error": None, "updated_at": pol["tenant_overrides"][0]["updated_at"]}]
    assert pol["effective"]["allowed_providers"] == ["sranai"]
    assert pol["effective"]["opt_in"]["context_window_fallback"] is True
    # another tenant is untouched
    other = client.get("/admin/fallback/policy?tenant_id=t-other").json()
    assert other["effective"]["allowed_providers"] is None
    assert other["effective"]["opt_in"]["context_window_fallback"] is False
    assert client.delete("/admin/fallback/overrides/tenant/t-acme").json()["deleted"] is True
    assert client.delete("/admin/fallback/overrides/tenant/t-acme").json()["deleted"] is False


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"boundary": {"rule": "allow"}}, "not overridable"),
        ({"decision_table": {"error_code": {"authentication_error": "cooldown_then_next"}}}, "hard-forbidden"),
        ({"allowed_providers": ["sranai"]}, "tenant-level"),
        ({"chains": {"curated": {"no-such-model-xyz": [{"provider": "openai", "model": "gpt-4o"}]}}}, "not a model in the catalogue"),
        ({"limits": {"stream": {"total_budget_s": "soon"}}}, "must be a number"),
    ],
)
def test_illegal_overrides_are_refused_with_the_reason(client, override, fragment):
    r = client.put("/admin/fallback/overrides/deployment", json={"override": override})
    assert r.status_code == 422, r.text
    assert fragment in r.text
    assert client.get("/admin/fallback/policy").json()["deployment_override"] is None


def test_a_hop_to_an_unknown_model_is_refused(client):
    a, _ = _known_model_pair()
    r = client.put("/admin/fallback/overrides/deployment", json={"override": {"chains": {"curated": {a.model: [{"provider": "openai", "model": "gpt-99-imaginary"}]}}}})
    assert r.status_code == 422 and "gpt-99-imaginary" in r.text
