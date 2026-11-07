"""Admin custom-DLP-detector API tests (Sprint 6, F).

The conftest globally overrides ``require_admin_session`` with an admin
identity, so these drive the happy paths directly; the 403 case re-overrides
it to deny.
"""

import uuid

import pytest
from starlette.testclient import TestClient

from app.api.deps import require_admin_session
from app.core.exceptions import AuthorizationError
from app.main import app as main_app

BASE = "/admin/dlp/custom-detectors"


def _client() -> TestClient:
    return TestClient(main_app)


def _create_body(**over) -> dict:
    body = {
        "name": f"det-{uuid.uuid4().hex[:8]}",
        "category": "pii",
        "detection_type": "regex",
        "pattern": {"regex": r"ACCT-\d{6}"},
        "severity": "high",
        "default_action": "mask",
        "test_cases": [
            {"input": "ACCT-123456", "expected_match": True},
            {"input": "nope", "expected_match": False},
        ],
    }
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_non_admin_403(async_db):
    def _deny():
        raise AuthorizationError("not admin")

    main_app.dependency_overrides[require_admin_session] = _deny
    try:
        r = _client().get(BASE)
        assert r.status_code == 403
    finally:
        from tests.conftest import MOCK_AUTH

        main_app.dependency_overrides[require_admin_session] = lambda: MOCK_AUTH


@pytest.mark.asyncio
async def test_create_valid_regex(async_db):
    r = _client().post(BASE, json=_create_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "draft"
    assert data["test_passed"] is True


@pytest.mark.asyncio
async def test_create_invalid_regex_redos(async_db):
    r = _client().post(BASE, json=_create_body(pattern={"regex": r"(a+)+$"}))
    assert r.status_code == 400
    assert "ReDoS" in r.text  # wrapped as error.message by the app handler


@pytest.mark.asyncio
async def test_create_failing_tests_draft(async_db):
    r = _client().post(
        BASE,
        json=_create_body(
            test_cases=[{"input": "nope", "expected_match": True}]
        ),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "draft"
    assert data["test_passed"] is False


@pytest.mark.asyncio
async def test_activate_without_test_passed_400(async_db):
    cid = _client().post(
        BASE, json=_create_body(test_cases=[{"input": "x", "expected_match": True}])
    ).json()["id"]
    r = _client().post(f"{BASE}/{cid}/activate")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_activate_success(async_db):
    cid = _client().post(BASE, json=_create_body()).json()["id"]
    r = _client().post(f"{BASE}/{cid}/activate")
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_update_bumps_version_and_audits(async_db):
    cid = _client().post(BASE, json=_create_body()).json()["id"]
    r = _client().put(
        f"{BASE}/{cid}",
        json={"pattern": {"regex": r"ACCT-\d{6,8}"}, "reason": "widen"},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2
    audit = _client().get(f"{BASE}/{cid}/audit-log").json()
    actions = {a["action"] for a in audit}
    assert {"create", "update"} <= actions


@pytest.mark.asyncio
async def test_disable_without_reason_400(async_db):
    cid = _client().post(BASE, json=_create_body()).json()["id"]
    r = _client().post(f"{BASE}/{cid}/disable", json={"reason": ""})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_audit_log_returns_history(async_db):
    cid = _client().post(BASE, json=_create_body()).json()["id"]
    _client().post(f"{BASE}/{cid}/activate")
    audit = _client().get(f"{BASE}/{cid}/audit-log").json()
    assert [a["action"] for a in audit][:2] == ["activate", "create"]  # newest first


@pytest.mark.asyncio
async def test_test_endpoint_runs_detector(async_db):
    cid = _client().post(BASE, json=_create_body()).json()["id"]
    r = _client().post(
        f"{BASE}/{cid}/test",
        json={"inputs": ["ACCT-123456", "harmless"]},
    )
    assert r.status_code == 200
    results = {x["input"]: x["matched"] for x in r.json()["results"]}
    assert results["ACCT-123456"] is True
    assert results["harmless"] is False
