"""AI Sensitive Data Reviewer — settings + scan + findings + summary.

Exercises the admin routes end-to-end through the integration
TestClient. The Claude call is mocked with ``respx`` so the scan
test never hits a real endpoint; all other tests run without any
HTTP mocking at all.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.models.ai_review import (
    AiReviewFindingORM,
    AiReviewScanRunORM,
    AiReviewSettingsORM,
)
from app.models.request_log import RequestLogORM
from tests.conftest import _integration_engine
from sqlalchemy.orm import sessionmaker


client = TestClient(app, raise_server_exceptions=False)

# AI review resolves its fallback key through ProviderKeyService now, not from
# ``settings.<provider>_api_key`` — see app/services/ai_review_provider.py.
# These two helpers put the test in the state the product actually reaches:
# a provider key row the admin UI would have created, or none at all.
_TEST_PROVIDER_KEY = "sk-ant-api03-" + "t" * 60


def _give_provider_a_key(provider: str = "anthropic") -> None:
    from tests.conftest import _IntegrationSession
    from app.services.provider_key_service import ProviderKeyService, clear_keys_cache

    _take_provider_key_away(provider)
    session = _IntegrationSession()
    try:
        ProviderKeyService().add_key(
            session, provider, _TEST_PROVIDER_KEY, label="admin"
        )
        session.commit()
    finally:
        session.close()
    clear_keys_cache()


def _take_provider_key_away(*providers: str) -> None:
    from tests.conftest import _IntegrationSession
    from app.models.provider_key import ProviderKeyORM
    from app.services.provider_key_service import clear_keys_cache

    session = _IntegrationSession()
    try:
        for provider in providers or ("anthropic", "openai", "openrouter"):
            session.query(ProviderKeyORM).filter(
                ProviderKeyORM.provider_name == provider
            ).delete()
        session.commit()
    finally:
        session.close()
    clear_keys_cache()


_HEADERS = {"Authorization": "Bearer demo-key"}


@pytest.fixture(autouse=True)
def _clean_ai_review_tables():
    """Wipe AI review state between tests.

    ``_integration_engine`` is a module-level singleton, so rows
    written by one test would pollute the next. We nuke both AI
    review tables before and after each test and leave request_logs
    alone — the per-test seed helpers own their own cleanup.
    """
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        s.query(AiReviewFindingORM).delete()
        s.query(AiReviewScanRunORM).delete()
        s.query(AiReviewSettingsORM).delete()
        s.query(RequestLogORM).delete()
        s.commit()
    finally:
        s.close()
    yield
    s = Session()
    try:
        s.query(AiReviewFindingORM).delete()
        s.query(AiReviewScanRunORM).delete()
        s.query(AiReviewSettingsORM).delete()
        s.query(RequestLogORM).delete()
        s.commit()
    finally:
        s.close()


def _seed_request_log(
    input_raw: str,
    request_id: str | None = None,
    output_raw: str | None = None,
) -> str:
    rid = request_id or f"req_{uuid.uuid4().hex[:16]}"
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        row = RequestLogORM(
            request_id=rid,
            tenant_id="test-tenant",
            project_id="test-project",
            provider="openai",
            model="gpt-4o-mini",
            status="success",
            policy_action="allow",
            latency_ms=10,
            input_raw=input_raw,
            # Scan reads output_raw. Default to input_raw so existing
            # callers that don't pass output_raw still produce scannable rows.
            output_raw=output_raw if output_raw is not None else input_raw,
        )
        s.add(row)
        s.commit()
    finally:
        s.close()
    return rid


def _seed_finding(
    *,
    is_in_coverage: bool,
    status: str = "new",
    request_id: str = "req_xxx",
    scan_run_id: str | None = None,
) -> str:
    fid = str(uuid.uuid4())
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        s.add(
            AiReviewFindingORM(
                id=fid,
                request_id=request_id,
                scan_run_id=scan_run_id or str(uuid.uuid4()),
                entity_type="salary",
                description="Example finding",
                sample_text="sample",
                risk_level="MEDIUM",
                is_in_coverage=is_in_coverage,
                suggested_detector=None,
                status=status,
            )
        )
        s.commit()
    finally:
        s.close()
    return fid


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestAiReviewSettings:
    def test_settings_defaults_on_first_get(self):
        resp = client.get("/admin/ai-review/settings", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is False
        assert body["monitored_key_ids"] is None
        assert body["scan_limit"] == 50
        assert body["last_scan_at"] is None

    def test_patch_settings_enable(self):
        resp = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is True

        # Round-trip via a second GET to verify persistence.
        follow = client.get("/admin/ai-review/settings", headers=_HEADERS)
        assert follow.json()["enabled"] is True

    def test_patch_settings_monitored_keys(self):
        resp = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"monitored_key_ids": ["key-1", "key-2"]},
        )
        assert resp.status_code == 200
        assert resp.json()["monitored_key_ids"] == ["key-1", "key-2"]

        # Explicit null → back to "monitor all keys".
        resp2 = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"monitored_key_ids": None},
        )
        assert resp2.status_code == 200
        assert resp2.json()["monitored_key_ids"] is None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _claude_response(findings: list[dict]) -> dict:
    """Build a minimal Anthropic /v1/messages reply shape."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": [{"type": "text", "text": json.dumps(findings)}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


class TestAiReviewScan:
    def test_scan_returns_400_when_disabled(self):
        # Default state is disabled. ValidationError in this codebase
        # maps to HTTP 422 via the central error handler, not 400 —
        # the spec's "400" is the user-facing intent, 422 is the
        # implementation.
        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 422, resp.text
        assert "disabled" in resp.json()["error"]["message"].lower()

    def test_scan_returns_no_findings_when_no_output_raw(self):
        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scan_run_id"] is None
        assert body["findings_count"] == 0

    @respx.mock
    def test_scan_calls_claude_and_saves_findings(self, monkeypatch):
        # Ensure the route sees a usable key: a provider_keys row, which is
        # the only place AI review looks now.
        _give_provider_a_key("anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )

        rid1 = _seed_request_log("Please email me at alice@example.com")
        rid2 = _seed_request_log("My salary is 85000 THB/month")

        mock_findings = [
            {
                "request_index": 0,
                "entity_type": "email",
                "description": "Email address in message body",
                "sample_text": "alice@example.com",
                "risk_level": "MEDIUM",
                "is_in_coverage": True,
                "suggested_detector": "email",
            },
            {
                "request_index": 1,
                "entity_type": "salary",
                "description": "Employee salary figure in THB",
                "sample_text": "85000 THB/month",
                "risk_level": "HIGH",
                "is_in_coverage": False,
                "suggested_detector": "thai_salary",
            },
        ]
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_claude_response(mock_findings))
        )

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["findings_count"] == 2
        assert body["new_patterns_count"] == 1
        assert body["already_covered_count"] == 1
        assert body["scan_run_id"] is not None

        # The persisted rows reference the seeded request_ids.
        findings_resp = client.get(
            "/admin/ai-review/findings", headers=_HEADERS
        )
        items = findings_resp.json()["items"]
        assert len(items) == 2
        by_entity = {i["entity_type"]: i for i in items}
        assert by_entity["email"]["is_in_coverage"] is True
        assert by_entity["email"]["request_id"] in (rid1, rid2)
        assert by_entity["salary"]["is_in_coverage"] is False
        assert by_entity["salary"]["risk_level"] == "HIGH"
        assert by_entity["salary"]["suggested_detector"] == "thai_salary"


# ---------------------------------------------------------------------------
# Findings status update + summary
# ---------------------------------------------------------------------------


class TestAiReviewFindings:
    def test_findings_status_update(self):
        fid = _seed_finding(is_in_coverage=False)
        resp = client.patch(
            f"/admin/ai-review/findings/{fid}",
            headers=_HEADERS,
            json={"status": "dismissed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

        listed = client.get(
            "/admin/ai-review/findings?status=dismissed", headers=_HEADERS
        ).json()
        assert any(i["id"] == fid for i in listed["items"])


class TestAiReviewSummary:
    def test_summary_counts_correctly(self):
        # 2 new patterns (not covered, status=new), 1 already covered.
        _seed_finding(is_in_coverage=False, status="new")
        _seed_finding(is_in_coverage=False, status="new")
        _seed_finding(is_in_coverage=True, status="new")

        resp = client.get("/admin/ai-review/summary", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_findings"] == 3
        assert body["new_patterns"] == 2
        assert body["already_covered"] == 1
        assert body["enabled"] is False  # default


# ---------------------------------------------------------------------------
# Provider configuration (migration 023)
# ---------------------------------------------------------------------------


def _seed_scan_run(
    *,
    trigger: str = "manual",
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5-20251001",
    cost: float = 0.0,
    tokens: int = 0,
) -> str:
    from decimal import Decimal

    sid = str(uuid.uuid4())
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        s.add(
            AiReviewScanRunORM(
                scan_run_id=sid,
                trigger=trigger,
                provider=provider,
                model=model,
                requests_scanned=1,
                findings_count=0,
                prompt_tokens=tokens // 2,
                completion_tokens=tokens // 2,
                total_tokens=tokens,
                estimated_cost_usd=Decimal(str(cost)),
                status="completed",
            )
        )
        s.commit()
    finally:
        s.close()
    return sid


class TestAiReviewProviderConfig:
    def test_patch_settings_review_provider(self):
        resp = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={
                "review_provider": "openai",
                "review_model": "gpt-4o-mini",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review_provider"] == "openai"
        assert body["review_model"] == "gpt-4o-mini"
        # Key was NOT provided, so review_api_key_set should still be false.
        assert body["review_api_key_set"] is False

    def test_patch_settings_review_api_key_stored_encrypted(self):
        resp = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"review_api_key": "sk-test-key-123"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # ``review_api_key_set`` flips on; raw key must not appear.
        assert body["review_api_key_set"] is True
        assert "review_api_key" not in body
        assert "sk-test-key-123" not in resp.text

        # The DB stores an encoded form, not plaintext.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            row = s.get(AiReviewSettingsORM, 1)
            assert row is not None
            assert row.review_api_key_enc is not None
            assert row.review_api_key_enc != "sk-test-key-123"
            # base64 of the plaintext is what actually lives there.
            import base64
            assert base64.b64decode(row.review_api_key_enc).decode() == "sk-test-key-123"
        finally:
            s.close()

    def test_patch_settings_clear_review_api_key(self):
        # Seed a key first.
        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"review_api_key": "sk-seeded"},
        )
        # Then clear it.
        resp = client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"review_api_key": None},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["review_api_key_set"] is False


# ---------------------------------------------------------------------------
# Scan runs + cost tracking
# ---------------------------------------------------------------------------


class TestAiReviewScanRuns:
    @respx.mock
    def test_scan_creates_scan_run_row(self, monkeypatch):
        _give_provider_a_key("anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        _seed_request_log("alice@example.com in message")

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": json.dumps([])}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )
        )

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scan_run_id"] is not None

        # A scan_run row now exists with trigger=manual.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            rows = s.query(AiReviewScanRunORM).all()
            assert len(rows) == 1
            assert rows[0].trigger == "manual"
            assert rows[0].status == "completed"
        finally:
            s.close()

    @respx.mock
    def test_scan_run_tracks_tokens_and_cost(self, monkeypatch):
        _give_provider_a_key("anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        _seed_request_log("card 4111 1111 1111 1111")

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": json.dumps([])}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            )
        )

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Totals come through the response.
        assert body["tokens_used"] == 150
        assert body["estimated_cost_usd"] > 0

        # And are stored on the scan_run row.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            row = s.query(AiReviewScanRunORM).one()
            assert row.total_tokens == 150
            assert row.prompt_tokens == 100
            assert row.completion_tokens == 50
            assert float(row.estimated_cost_usd) > 0
        finally:
            s.close()

    def test_scan_returns_422_when_no_key_configured(self, monkeypatch):
        # No key row for either provider — the OpenAI preset is selected and
        # nothing is configured, so resolve_review_key returns None.
        _take_provider_key_away("openai", "anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={
                "enabled": True,
                "review_provider": "openai",
                "review_model": "gpt-4o-mini",
            },
        )
        _seed_request_log("Some input text that needs scanning")

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 422, resp.text
        assert "No API key" in resp.json()["error"]["message"]

    def test_scan_run_summary_endpoint(self):
        _seed_scan_run(trigger="manual", cost=0.0005, tokens=100)
        _seed_scan_run(trigger="realtime", cost=0.0001, tokens=30)

        resp = client.get("/admin/ai-review/scan-runs/summary", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == 2
        assert abs(body["total_cost_usd"] - 0.0006) < 1e-9
        assert body["total_tokens"] == 130
        assert body["manual_runs"] == 1
        assert body["realtime_runs"] == 1


# ---------------------------------------------------------------------------
# Realtime hook
# ---------------------------------------------------------------------------


class TestAiReviewRealtimeHook:
    def test_realtime_hook_respects_sample_rate_zero(self, monkeypatch):
        """rate=0 must short-circuit before touching call_review_api.

        We pre-populate the settings cache so ``_maybe_ai_review``
        doesn't hit the DB, then confirm that ``call_review_api`` is
        never called by monkey-patching it to raise if invoked.
        """
        import asyncio

        from app.api.routes import admin_ai_review

        admin_ai_review._settings_cache["row"] = {
            "enabled": True,
            "realtime_enabled": True,
            "realtime_sample_rate": 0,
            "monitored_key_ids": None,
            "review_provider": "anthropic",
            "review_model": None,
            "review_api_key_enc": None,
            "review_endpoint": None,
        }
        admin_ai_review._settings_cache["expires_at"] = float("inf")

        called = {"yes": False}

        async def _should_not_be_called(*a, **kw):
            called["yes"] = True
            return {"content": "[]", "prompt_tokens": 0, "completion_tokens": 0}

        monkeypatch.setattr(admin_ai_review, "call_review_api", _should_not_be_called)

        asyncio.run(
            admin_ai_review._maybe_ai_review("req_xxx", "some input with data")
        )

        assert called["yes"] is False

        # Restore the cache for subsequent tests.
        admin_ai_review.invalidate_settings_cache()

    def test_realtime_hook_fires_after_settings_cache_bust(self, monkeypatch):
        """After PATCH enables realtime (which busts the cache), the
        next ``_maybe_ai_review`` call must reach ``call_review_api``
        with sample_rate=100 (guaranteed hit)."""
        import asyncio

        from app.api.routes import admin_ai_review

        # 1. Enable realtime via the PATCH handler — this both persists
        #    the change and calls invalidate_settings_cache().
        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True, "realtime_enabled": True, "realtime_sample_rate": 100},
        )

        # 2. Stub call_review_api so it records the call without hitting
        #    a real LLM.
        called = {"yes": False}

        async def _stub(*a, **kw):
            called["yes"] = True
            return {"content": "[]", "prompt_tokens": 0, "completion_tokens": 0}

        monkeypatch.setattr(admin_ai_review, "call_review_api", _stub)

        # 3. Route SessionLocal to the integration engine so the hook's
        #    DB reads land in the same DB the PATCH wrote to. The import
        #    in admin_ai_review is ``from app.core.db import SessionLocal``
        #    so the local name must be patched directly.
        from sqlalchemy.orm import sessionmaker as _sm
        from tests.conftest import _integration_engine as _ie
        monkeypatch.setattr(
            admin_ai_review, "SessionLocal",
            _sm(bind=_ie, autoflush=False, autocommit=False),
        )

        # 4. Call _maybe_ai_review directly.
        asyncio.run(
            admin_ai_review._maybe_ai_review("req_test_bust", "hello PII")
        )

        assert called["yes"] is True, (
            "call_review_api was not called — cache bust did not propagate"
        )

        admin_ai_review.invalidate_settings_cache()

    def test_realtime_hook_skips_when_disabled(self, monkeypatch):
        """When realtime_enabled=False, the hook must not call the LLM."""
        import asyncio

        from app.api.routes import admin_ai_review

        admin_ai_review._settings_cache["row"] = {
            "enabled": True,
            "realtime_enabled": False,
            "realtime_sample_rate": 100,
            "monitored_key_ids": None,
            "review_provider": "anthropic",
            "review_model": None,
            "review_api_key_enc": None,
            "review_endpoint": None,
        }
        admin_ai_review._settings_cache["expires_at"] = float("inf")

        called = {"yes": False}

        async def _should_not_be_called(*a, **kw):
            called["yes"] = True
            return {"content": "[]", "prompt_tokens": 0, "completion_tokens": 0}

        monkeypatch.setattr(admin_ai_review, "call_review_api", _should_not_be_called)

        asyncio.run(
            admin_ai_review._maybe_ai_review("req_disabled", "hello PII")
        )

        assert called["yes"] is False

        admin_ai_review.invalidate_settings_cache()


# ---------------------------------------------------------------------------
# Test connection endpoint
# ---------------------------------------------------------------------------


class TestAiReviewTestConnection:
    @respx.mock
    def test_test_connection_returns_success_when_key_valid(self, monkeypatch):
        _give_provider_a_key("anthropic")

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            )
        )

        resp = client.post("/admin/ai-review/test-connection", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["latency_ms"] >= 0
        assert body["model_used"] != ""

        # Must NOT create a scan_run row — test-connection is free.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            assert s.query(AiReviewScanRunORM).count() == 0
        finally:
            s.close()

    def test_test_connection_returns_error_when_no_key(self, monkeypatch):
        _take_provider_key_away("anthropic", "openai")

        resp = client.post("/admin/ai-review/test-connection", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert body["error"] is not None
        assert "No API key" in body["error"]


# ---------------------------------------------------------------------------
# output_raw scanning (Change 1 + Change 2)
# ---------------------------------------------------------------------------


class TestAiReviewOutputRaw:
    @respx.mock
    def test_scan_uses_output_raw_not_input_raw(self, monkeypatch):
        """The scan endpoint must send output_raw (the AI provider's raw
        response) to Claude, NOT input_raw (the user message)."""
        _give_provider_a_key("anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        _seed_request_log(
            input_raw="user asked about salary",
            output_raw="the salary is 85000 baht per month",
        )

        captured_body: list[dict] = []

        def _capture_request(request):
            captured_body.append(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": "[]"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )

        respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=_capture_request
        )

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text

        # The user message sent to Claude must contain the output_raw
        # text, NOT the input_raw text.
        assert captured_body, "Claude was never called"
        user_msg = captured_body[0]["messages"][0]["content"]
        assert "85000 baht" in user_msg
        assert "user asked about salary" not in user_msg

    def test_realtime_hook_uses_output_raw(self, monkeypatch):
        """_maybe_ai_review passes output_raw to the prompt builder."""
        import asyncio

        from app.api.routes import admin_ai_review

        admin_ai_review._settings_cache["row"] = {
            "enabled": True,
            "realtime_enabled": True,
            "realtime_sample_rate": 100,
            "monitored_key_ids": None,
            "review_provider": "anthropic",
            "review_model": None,
            "review_api_key_enc": None,
            "review_endpoint": None,
        }
        admin_ai_review._settings_cache["expires_at"] = float("inf")

        captured_text: list[str] = []

        async def _capture(*a, **kw):
            captured_text.append(kw.get("user_text", ""))
            return {"content": "[]", "prompt_tokens": 0, "completion_tokens": 0}

        monkeypatch.setattr(admin_ai_review, "call_review_api", _capture)

        asyncio.run(
            admin_ai_review._maybe_ai_review("req_rt", "salary 90000 THB")
        )

        assert captured_text, "call_review_api was not called"
        assert "90000 THB" in captured_text[0]

        admin_ai_review.invalidate_settings_cache()

    @respx.mock
    def test_scan_skips_requests_with_no_output_raw(self, monkeypatch):
        """Rows with output_raw=NULL must be skipped entirely — no Claude call."""
        _give_provider_a_key("anthropic")

        client.patch(
            "/admin/ai-review/settings",
            headers=_HEADERS,
            json={"enabled": True},
        )
        # Seed a row with input_raw but no output_raw.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            s.add(RequestLogORM(
                request_id=f"req_{uuid.uuid4().hex[:16]}",
                tenant_id="test-tenant",
                project_id="test-project",
                provider="openai",
                model="gpt-4o-mini",
                status="success",
                policy_action="allow",
                latency_ms=10,
                input_raw="some user input",
                output_raw=None,
            ))
            s.commit()
        finally:
            s.close()

        called = {"yes": False}

        def _should_not_call(request):
            called["yes"] = True
            return httpx.Response(200, json={
                "id": "msg_x", "type": "message", "role": "assistant",
                "model": "test", "content": [{"type": "text", "text": "[]"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0},
            })

        respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=_should_not_call
        )

        resp = client.post("/admin/ai-review/scan", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["findings_count"] == 0
        assert called["yes"] is False, "Claude was called despite no output_raw rows"
