import json
import re

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy import select

from app.core.dlp_audit_crypto import decrypt as decrypt_audit_blob
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


def _latest_dlp_block_log(prompt: str) -> RequestLogORM:
    """The newest ``dlp_violation`` row written for ``prompt``.

    The integration DB is shared and SQLite's ``created_at`` has one-second
    precision, so "the newest block row" alone can be another test's (an
    outbound block's row carries a different ``input_raw`` placeholder).
    The row is identified by its encrypted audit copy of the prompt.
    """
    session = _IntegrationSession()
    try:
        rows = session.execute(
            select(RequestLogORM)
            .where(RequestLogORM.error_code == "dlp_violation")
            .order_by(RequestLogORM.created_at.desc())
        ).scalars().all()
        row = next(
            r for r in rows
            if decrypt_audit_blob(r.input_raw_encrypted or "") == prompt
        )
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
    # The provider is sent a placeholder in the secret's place ...
    assert secret not in captured["content"]
    assert re.fullmatch(r"API Key: <r\d+>", captured["content"])
    # ... and a secret is never restored on the way back: the provider's
    # echo of the placeholder reaches the caller with the placeholder
    # stripped, not with the key put back.
    assert secret not in body_text
    assert resp.json()["choices"][0]["message"]["content"] == "API Key:"

    row = _request_log(resp.json()["id"])
    # All four stages + findings JSON must be free of the raw value
    # (``output_final`` holds the provider-safe masked text, never a
    # restored reply).
    serialized_row = "\n".join(
        str(v or "")
        for v in (
            row.input_raw,
            row.input_masked,
            row.output_raw,
            row.output_final,
            row.dlp_findings_json,
        )
    )
    assert secret not in serialized_row
    assert re.search(r"API Key: <r\d+>", row.input_masked)
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
        row = _latest_dlp_block_log(prompt)
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

    # Restored to the caller when the provider echoes their placeholder.
    restored_values = [
        "john.smith@example.com",
        "+66 81-234-5678",
        "1101700203451",
        "4111 1111 1111 1111",
        "99/88 Sukhumvit Road, Bangkok 10110",
        "diabetes",
        "85,000 THB/month",
    ]
    # Secrets are masked on the way in like the rest, and NEVER restored.
    secret_values = [
        "P@ssw0rd123!",
        "sk-test-1234567890abcdef",
    ]
    raw_values = restored_values + secret_values

    async def _echo_mask_tokens(self, route, payload, messages, api_key=None):
        provider_input = messages[-1].content
        captured["content"] = provider_input
        tokens = re.findall(r"<r\d+>", provider_input)
        captured["reply"] = "Summary: " + " ".join(tokens)
        return ProviderResponse(
            content=captured["reply"],
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
    for raw in restored_values:
        assert raw in body_text
    for raw in secret_values:
        assert raw not in body_text
    # No placeholder reaches the caller either: restored or stripped.
    assert not re.search(r"<r\d+>", body_text)

    # Every labelled value went to the provider as one ``<rN>`` token.
    labels = (
        "Email",
        "Phone",
        "Thai ID",
        "Credit Card",
        "Password",
        "API Key",
        "Address",
        "Medical",
        "Salary",
    )
    for label in labels:
        assert re.search(rf"^{label}: <r\d+>$", provider_input, re.M), label

    row = _request_log(resp.json()["id"])
    # No stage and no findings JSON may contain raw PII — the columns
    # are written as either fixed placeholders or token-only masked
    # text by construction.
    pii_safe_columns = "\n".join(
        str(v or "")
        for v in (
            row.input_raw,
            row.input_masked,
            row.output_raw,
            row.output_final,
            row.dlp_findings_json,
        )
    )
    for raw in raw_values:
        assert raw not in pii_safe_columns

    # Stage 1 (User → Service) stays as the placeholder; the raw
    # user prompt lives only in the AES-encrypted audit channel.
    assert row.input_raw == "[MASKED_BY_DLP]"

    # Stages 2 (Service → AI) and 3 (AI → Service) carry only mask
    # tokens (``<rN>``) by construction, so we persist the actual
    # masked text to make the Safe Trace UI useful.
    assert row.input_masked and row.input_masked != "[MASKED_BY_DLP]"
    for label in ("Email", "Phone", "Thai ID"):
        assert re.search(rf"^{label}: <r\d+>$", row.input_masked, re.M), label
    assert row.output_raw == captured["reply"]

    # Stage 4 (Service → User) persists the same provider-safe masked
    # text as stage 3 — never the post-restore reply the user received
    # (that was checked above, with the other columns).
    assert row.output_final == captured["reply"]

    assert '"matched_text"' not in (row.dlp_findings_json or "")
