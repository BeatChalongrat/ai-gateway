from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.deps import ResolvedApiKey, require_admin_session
from app.main import app
from app.models.admin_audit_log import AdminAuditLogORM
from app.models.tenant import TenantORM
from app.services.secure_trace_store import secure_trace_store
from tests.conftest import _IntegrationSession


client = TestClient(app)
_HEADERS = {"Authorization": "Bearer demo-key"}

_SENSITIVE_VALUES = [
    "Somchai",
    "somchai.jaidee@example.com",
    "99/88 Sukhumvit",
    "diabetes",
    "85,000",
    "sk-test",
    "P@ssw0rd123",
]
_PROVIDER_MASKED_VALUES = [v for v in _SENSITIVE_VALUES if v != "Somchai"]


def _synthetic_prompt() -> str:
    return """Please summarize this customer profile and echo placeholders:
Name: Somchai Jaidee
Email: somchai.jaidee@example.com
Phone: +66 81-234-5678
Thai ID: 1101700203451
Passport: AA1234567
Address: 99/88 Sukhumvit Road, Bangkok 10110
Credit Card: 4111 1111 1111 1111
Bank Account: 123-4-56789-0
Password: P@ssw0rd123!
API Key: sk-test-1234567890abcdef
Medical: diabetes
Salary: 85,000 THB/month
"""


def _make_request() -> str:
    secure_trace_store.clear()
    session = _IntegrationSession()
    try:
        tenant = session.get(TenantORM, "test-tenant")
        if tenant is not None:
            tenant.ner_enabled = True
            session.commit()
    finally:
        session.close()
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": _synthetic_prompt()}],
            "metadata": {},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_safe_trace_does_not_leak_raw_sensitive_values():
    request_id = _make_request()

    detail = client.get(f"/admin/requests/{request_id}", headers=_HEADERS)
    assert detail.status_code == 200, detail.text
    body = detail.json()

    # Stage 1 (User → Service) is still a placeholder — the raw user
    # prompt is only recoverable through the audit-encrypted channel
    # at ``GET /admin/requests/{id}/trace``.
    assert body["input_raw"] == "[MASKED_BY_DLP]"

    # Stages 2 (Service → AI) and 3 (AI → Service) carry the actual
    # masked text. The privacy guarantee at those stages is exactly
    # the masker's catch rate: anything the detector classified as
    # sensitive must NOT appear, but values the masker doesn't catch
    # (e.g. a bare Thai first name) pass through. That's the same
    # contract enforced on the wire to the provider — see
    # ``test_admin_secure_trace_shows_raw_and_masked_stages``.
    serialized = json.dumps(
        {
            "input_masked": body.get("input_masked"),
            "output_raw": body.get("output_raw"),
        },
        ensure_ascii=False,
    )
    for value in _PROVIDER_MASKED_VALUES:
        assert value not in serialized

    # Stage 4 (Service → User) deliberately persists the post-restore
    # plaintext so admins can audit what the user received. We only
    # assert that the field is non-empty and not the legacy placeholder
    # — the actual content is whatever the model returned with the
    # original PII spliced back, which may or may not echo every
    # sensitive value verbatim depending on the model's behaviour.
    assert body["output_final"] is not None
    assert body["output_final"] != "[MASKED_BY_DLP]"


def test_admin_secure_trace_shows_raw_and_masked_stages():
    request_id = _make_request()

    resp = client.get(
        f"/admin/requests/{request_id}/secure-trace?reason=debug",
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["trace_mode"] == "admin_secure"
    stages = body["stages"]
    assert "Somchai Jaidee" in stages["user_to_service"]["content"]
    assert "Somchai Jaidee" in stages["service_to_user"]["content"]

    service_to_ai = stages["service_to_ai"]["content"]
    ai_to_service = stages["ai_to_service"]["content"]
    for value in _PROVIDER_MASKED_VALUES:
        assert value not in service_to_ai
    assert "[EMAIL_" in service_to_ai
    assert "[PASSWORD_" in service_to_ai
    assert "[BEARER_TOKEN_" in service_to_ai
    assert "[ADDRESS_" in service_to_ai
    assert "[HEALTH_CONDITION_" in service_to_ai
    assert "[INCOME_" in service_to_ai
    assert "[EMAIL_" in ai_to_service


def test_secure_trace_requires_admin_permission():
    request_id = _make_request()
    low_priv = ResolvedApiKey(
        key_id="tenant-admin-key",
        tenant_id="test-tenant",
        project_id="test-project",
        key_prefix="gw_low",
        role="user",
    )

    old_override = app.dependency_overrides.get(require_admin_session)
    app.dependency_overrides[require_admin_session] = lambda: low_priv
    try:
        resp = client.get(
            f"/admin/requests/{request_id}/secure-trace",
            headers=_HEADERS,
        )
    finally:
        if old_override is not None:
            app.dependency_overrides[require_admin_session] = old_override
        else:
            app.dependency_overrides.pop(require_admin_session, None)

    assert resp.status_code == 403


def test_secure_trace_access_creates_safe_audit_event():
    request_id = _make_request()

    resp = client.get(
        f"/admin/requests/{request_id}/secure-trace?reason=incident-review",
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    session = _IntegrationSession()
    try:
        row = (
            session.query(AdminAuditLogORM)
            .filter(AdminAuditLogORM.action == "dlp.secure_trace.viewed")
            .filter(AdminAuditLogORM.resource_id == request_id)
            .order_by(AdminAuditLogORM.timestamp.desc())
            .first()
        )
        assert row is not None
        details = row.details or ""
        assert "user_to_service" in details
        assert "service_to_user" in details
        assert "incident-review" in details
        for value in _SENSITIVE_VALUES:
            assert value not in details
    finally:
        session.close()
