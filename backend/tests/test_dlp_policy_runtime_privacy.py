import json
import re

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy import select

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.domain.observability import build_dlp_findings_json
from app.domain.providers import ProviderResponse
from app.main import app
from app.models.policy_profile import PolicyProfileORM
from app.models.request_log import RequestLogORM
from app.services.provider_dispatcher import ProviderDispatcher
from tests.conftest import _IntegrationSession


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _reset_policy_override(profile_id: str = "default") -> None:
    session = _IntegrationSession()
    try:
        session.execute(
            delete(PolicyProfileORM).where(PolicyProfileORM.profile_id == profile_id)
        )
        session.commit()
    finally:
        session.close()


def _upsert_policy_override(profile_id: str, actions: dict[str, str]) -> None:
    session = _IntegrationSession()
    try:
        row = session.get(PolicyProfileORM, profile_id)
        if row is None:
            row = PolicyProfileORM(
                profile_id=profile_id,
                name=profile_id.title(),
                category_actions_json=json.dumps(actions),
            )
            session.add(row)
        else:
            row.category_actions_json = json.dumps(actions)
        session.commit()
    finally:
        session.close()


def _request_log(request_id: str) -> RequestLogORM:
    session = _IntegrationSession()
    try:
        row = session.get(RequestLogORM, request_id)
        assert row is not None
        session.expunge(row)
        return row
    finally:
        session.close()


def _latest_dlp_block_log() -> RequestLogORM:
    session = _IntegrationSession()
    try:
        row = session.execute(
            select(RequestLogORM)
            .where(RequestLogORM.error_code == "dlp_violation")
            .order_by(RequestLogORM.created_at.desc())
            .limit(1)
        ).scalar_one()
        session.expunge(row)
        return row
    finally:
        session.close()


def test_build_dlp_findings_json_does_not_persist_matched_text():
    match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=19,
        matched_text="alice@example.com",
        severity=SensitivityLevel.HIGH,
    )
    raw = build_dlp_findings_json([
        MatchAction(match, EnforcementAction.MASK, "pii_mask", "masked")
    ])

    payload = json.loads(raw)
    assert "matched_text" not in payload[0]
    assert payload[0]["matched_text_len"] == len("alice@example.com")


def test_runtime_uses_db_policy_override_for_default_profile():
    _reset_policy_override()
    _upsert_policy_override("default", {"pii": "block"})
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "email alice@example.com"}],
            },
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        assert body["error"]["action"] == "block"
        assert "PII" in body["error"]["categories"]
    finally:
        _reset_policy_override()


def test_secret_mask_policy_masks_provider_input_and_request_log(monkeypatch):
    _reset_policy_override()
    captured: dict[str, str] = {}

    async def _capture_dispatch(self, route, payload, messages, api_key=None):
        captured["content"] = messages[-1].content
        return ProviderResponse(content=messages[-1].content, model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _capture_dispatch)

    secret = "sk-test-1234567890abcdef"
    prompt = f"API Key: {secret}"
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={"model": "auto", "messages": [{"role": "user", "content": prompt}]},
    )

    assert resp.status_code == 200, resp.text
    body_text = resp.text
    assert secret not in captured["content"]
    assert secret in body_text
    assert "BEARER_TOKEN" in captured["content"]

    row = _request_log(resp.json()["id"])
    # ``output_final`` deliberately stores the post-restore plaintext
    # the user received, which echoes the original secret — exclude
    # it from the no-leak check. Stage 1 / 2 / 3 + findings JSON must
    # still be free of the raw value.
    serialized_row = "\n".join(
        str(v or "")
        for v in (
            row.input_raw,
            row.input_masked,
            row.output_raw,
            row.dlp_findings_json,
        )
    )
    assert secret not in serialized_row
    assert '"matched_text"' not in (row.dlp_findings_json or "")


def test_dlp_blocked_request_uses_placeholder_input_raw():
    _reset_policy_override()
    _upsert_policy_override("default", {"secret": "block"})
    secret = "sk-test-1234567890abcdef"
    prompt = f"API Key: {secret}"
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": prompt}]},
        )
        assert resp.status_code == 403
        row = _latest_dlp_block_log()
        assert row.input_raw == "[BLOCKED_BY_DLP]"
        assert row.input_raw != prompt
        assert secret not in (row.dlp_findings_json or "")
    finally:
        _reset_policy_override()


def test_outbound_restore_returns_all_original_token_values_without_persisting(
    monkeypatch,
):
    _reset_policy_override()
    captured: dict[str, str] = {}

    raw_values = [
        "john.smith@example.com",
        "+66 81-234-5678",
        "1101700203451",
        "4111 1111 1111 1111",
        "P@ssw0rd123!",
        "sk-test-1234567890abcdef",
        "99/88 Sukhumvit Road, Bangkok 10110",
        "diabetes",
        "85,000 THB/month",
    ]

    async def _echo_mask_tokens(self, route, payload, messages, api_key=None):
        provider_input = messages[-1].content
        captured["content"] = provider_input
        tokens = re.findall(r"\[[A-Z][A-Z0-9_]*_[A-Za-z0-9_\-]+\]", provider_input)
        return ProviderResponse(
            content="Summary: " + " ".join(tokens),
            model=route.model_name,
        )

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _echo_mask_tokens)

    prompt = (
        "Please rewrite this customer note:\n"
        "Email: john.smith@example.com\n"
        "Phone: +66 81-234-5678\n"
        "Thai ID: 1101700203451\n"
        "Credit Card: 4111 1111 1111 1111\n"
        "Password: P@ssw0rd123!\n"
        "API Key: sk-test-1234567890abcdef\n"
        "Address: 99/88 Sukhumvit Road, Bangkok 10110\n"
        "Medical: diabetes\n"
        "Salary: 85,000 THB/month\n"
    )
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": prompt}],
        },
    )

    assert resp.status_code == 200, resp.text
    provider_input = captured["content"]
    body_text = resp.json()["choices"][0]["message"]["content"]

    for raw in raw_values:
        assert raw not in provider_input
        assert raw in body_text

    for token_name in (
        "EMAIL",
        "PHONE_NUMBER",
        "THAI_ID",
        "CREDIT_CARD",
        "PASSWORD",
        "BEARER_TOKEN",
        "ADDRESS",
        "HEALTH_CONDITION",
        "INCOME",
    ):
        assert f"[{token_name}_" in provider_input

    row = _request_log(resp.json()["id"])
    # Stages 1-3 + findings JSON must not contain raw PII — those
    # columns are written as either fixed placeholders or token-only
    # masked text by construction.
    pii_safe_columns = "\n".join(
        str(v or "")
        for v in (
            row.input_raw,
            row.input_masked,
            row.output_raw,
            row.dlp_findings_json,
        )
    )
    for raw in raw_values:
        assert raw not in pii_safe_columns

    # Stage 1 (User → Service) stays as the placeholder; the raw
    # user prompt lives only in the AES-encrypted audit channel.
    assert row.input_raw == "[MASKED_BY_DLP]"

    # Stages 2 (Service → AI) and 3 (AI → Service) carry only mask
    # tokens (e.g. [EMAIL_0], [THAI_ID_3]) by construction, so we
    # persist the actual masked text to make the Safe Trace UI useful.
    assert row.input_masked and row.input_masked != "[MASKED_BY_DLP]"
    assert "[EMAIL_" in row.input_masked
    assert "[PHONE_NUMBER_" in row.input_masked
    assert "[THAI_ID_" in row.input_masked
    assert row.output_raw and row.output_raw != "[MASKED_BY_DLP]"

    # Stage 4 (Service → User) deliberately persists the post-restore
    # plaintext the user received, gated behind Role.ADMIN at the API
    # layer and NULLed by the retention job after the configured
    # window. We only assert it is no longer the legacy placeholder.
    assert row.output_final is not None
    assert row.output_final != "[MASKED_BY_DLP]"

    assert '"matched_text"' not in (row.dlp_findings_json or "")
