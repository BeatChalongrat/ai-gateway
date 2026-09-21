"""FIX125 M1 — a streaming turn that produced no answer is charged 0 credits.

Owner policy: "a turn that fails — the user gets no answer — is not charged
at all; log the real cost for the admin". Before FIX125 ``_finalize_stream``
priced the provider-reported usage first and only skipped the charge when no
usage had arrived, so a reasoning-only turn (usage present, empty answer), an
error frame after the usage chunk, and an exhausted fallback walk were all
billed. And the usage of a FAILED attempt survived the in-generator fallback
swap, so a rescued turn whose fallback reported no usage was billed on the
dead model's tokens.

Contract pinned here:

* no answer text and no tool calls  → no ledger row, the request_log row
  keeps the real cost, one ``cost_absorbed`` log event with the reason;
* an answer (text)                  → charged once (unchanged);
* a rescued turn                    → billed on the answering attempt only.

Stop with partial text and a mid-stream error after text stay charged —
pinned by ``tests/test_stream_abort_billing.py`` (unchanged).
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ProviderError
from app.domain.providers import ProviderStreamChunk, ProviderUsage
from app.main import app
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.test_stream_abort_billing import (  # noqa: F401 — fixture import
    _STREAM_PAYLOAD,
    _log_for,
    _user_state,
    billable_user,
)

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}

_BIG = ProviderUsage(
    prompt_tokens=90_000, completion_tokens=4_000, total_tokens=94_000,
    reasoning_tokens=4_000,
)


class _CapturingLogger:
    def __init__(self):
        self.records: list[dict] = []

    def bind(self, **kw):
        return self

    def _rec(self, level, event, **kw):
        self.records.append({"log_level": level, "event": event, **kw})

    def debug(self, event, **kw):
        self._rec("debug", event, **kw)

    def info(self, event, **kw):
        self._rec("info", event, **kw)

    def warning(self, event, **kw):
        self._rec("warning", event, **kw)

    def error(self, event, **kw):
        self._rec("error", event, **kw)

    def exception(self, event, **kw):
        self._rec("error", event, **kw)

    def events(self, name: str) -> list[dict]:
        return [r for r in self.records if r["event"] == name]


@pytest.fixture(autouse=True)
def _no_inherited_health_markers():
    from app.services._redis_pool import run_sync
    from app.services.gateway.fallback_orchestrator import clear_provider_health

    def _clear():
        for provider in ("openai", "anthropic"):
            try:
                run_sync(clear_provider_health(provider, "gpt-4o-mini"))
                run_sync(clear_provider_health(provider, "claude-sonnet-4"))
                run_sync(clear_provider_health(provider))
            except Exception:  # noqa: BLE001
                pass

    _clear()
    yield
    _clear()


@pytest.fixture()
def cap(monkeypatch):
    c = _CapturingLogger()
    monkeypatch.setattr("app.api.routes.public_chat.logger", c)
    return c


@pytest.fixture()
def no_candidate(monkeypatch):
    monkeypatch.setattr(
        FallbackOrchestrator, "select_streaming_candidate", lambda self, **kw: None
    )


def _post():
    return client.post("/v1/chat/completions", headers=_HEADERS, json=_STREAM_PAYLOAD)


def _only_absorbed(cap: _CapturingLogger) -> dict:
    rows = cap.events("cost_absorbed")
    assert len(rows) == 1, f"expected one cost_absorbed event, got {rows}"
    return rows[0]


def _drop_log(request_id: str) -> None:
    from app.models.request_log import RequestLogORM
    from tests.test_stream_abort_billing import _Session

    db = _Session()
    try:
        db.query(RequestLogORM).filter(RequestLogORM.request_id == request_id).delete()
        db.commit()
    finally:
        db.close()


def _request_id_from(resp) -> str:
    rid = resp.headers.get("x-request-id")
    assert rid
    return rid


def test_reasoning_only_turn_is_not_charged(monkeypatch, billable_user, cap, no_candidate):
    async def _think_only(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(reasoning_delta="thinking hard " * 50, model="gpt-4o-mini")
        yield ProviderStreamChunk(finish_reason="length", usage=_BIG, model="gpt-4o-mini")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _think_only)
    resp = _post()
    assert resp.status_code == 200

    balance, used, txs = _user_state(billable_user)
    assert txs == [], "a turn with no answer must not be charged"
    assert balance == 100_000 and used == 0

    ev = _only_absorbed(cap)
    assert ev["reason"] == "empty_completion"
    assert ev["prompt_tokens"] == 90_000
    assert ev["completion_tokens"] == 4_000
    assert ev["reasoning_tokens"] == 4_000
    assert ev["estimated_cost_usd"] > 0
    assert ev["model"] == "gpt-4o-mini"

    assert ev["request_id"] in _request_id_from(resp)
    row = _log_for(ev["request_id"])
    try:
        assert row is not None, "the attempt is still recorded for the admin"
        assert float(row.estimated_cost_usd) > 0, "the real cost stays on the request_log row"
        assert row.status == "provider_error"
    finally:
        _drop_log(ev["request_id"])


def test_error_frame_after_usage_without_text_is_not_charged(
    monkeypatch, billable_user, cap, no_candidate,
):
    async def _usage_then_die(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(usage=_BIG, model="gpt-4o-mini")
        raise RuntimeError("provider connection lost")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _usage_then_die)
    resp = _post()
    assert resp.status_code == 200
    assert '"error"' in resp.text

    _, _, txs = _user_state(billable_user)
    assert txs == []
    ev = _only_absorbed(cap)
    _drop_log(ev["request_id"])
    assert ev["reason"] == "error"
    assert ev["estimated_cost_usd"] > 0


def test_all_models_failed_is_not_charged(monkeypatch, billable_user, cap, no_candidate):
    async def _usage_then_401(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(usage=_BIG, model="gpt-4o-mini")
        raise ProviderError(
            "rejected credentials (401)", details={"provider": "openai", "status": 401},
        )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _usage_then_401)
    resp = _post()
    assert "all_models_failed" in resp.text

    _, _, txs = _user_state(billable_user)
    assert txs == []
    ev = _only_absorbed(cap)
    _drop_log(ev["request_id"])
    assert ev["reason"] == "all_models_failed"


def test_answered_turn_is_charged_once(monkeypatch, billable_user, cap, no_candidate):
    small = ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    async def _answer(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(delta="a real answer " * 20, model="gpt-4o-mini")
        yield ProviderStreamChunk(finish_reason="stop", usage=small, model="gpt-4o-mini")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _answer)
    resp = _post()
    assert resp.status_code == 200

    _, _, txs = _user_state(billable_user)
    assert len(txs) == 1 and txs[0].amount < 0
    assert cap.events("cost_absorbed") == []
    row = _log_for(txs[0].request_id)
    assert row.prompt_tokens == 100


def test_rescued_turn_is_billed_on_the_answering_attempt_only(
    monkeypatch, billable_user, cap,
):
    """The primary reports a large usage then fails before any text; the
    fallback answers without reporting usage. The bill must be the
    fallback's estimate — never the dead attempt's 90,000 prompt tokens."""
    from app.services.providers.anthropic_adapter import AnthropicAdapter

    async def _primary_usage_then_401(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(usage=_BIG, model="gpt-4o-mini")
        raise ProviderError(
            "rejected credentials (401)", details={"provider": "openai", "status": 401},
        )

    async def _fallback_answers(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(
            delta="answer from the fallback model. " * 6,
            finish_reason="stop",
            model=resolved_model or "claude-sonnet-4",
        )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _primary_usage_then_401)
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", _fallback_answers)
    monkeypatch.setattr(
        FallbackOrchestrator,
        "select_streaming_candidate",
        lambda self, **kw: {
            "provider": "anthropic", "model": "claude-sonnet-4", "api_key": "test-stub-key",
        },
    )

    resp = _post()
    assert "answer from the fallback model" in resp.text

    _, _, txs = _user_state(billable_user)
    assert len(txs) == 1 and txs[0].amount < 0, "a rescued turn is charged"
    row = _log_for(txs[0].request_id)
    assert row.model == "claude-sonnet-4"
    assert row.prompt_tokens < 90_000, "the failed attempt's usage leaked into the bill"
    assert row.completion_tokens < 4_000
    assert cap.events("cost_absorbed") == []
