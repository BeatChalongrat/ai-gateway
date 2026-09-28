"""An explicit qwen3.8-27b-fp8 call on the public API got no rescue when the
self-hosted box stopped answering.

Live, 2026-09-28 06:52-07:02Z: every request to the box got no answer for
30 s. The web-chat turn on qwen was answered by another model after 32 s; the
two ``POST /v1/chat/completions`` calls (non-streaming, model named) waited
~91 s and got ``502 provider_error``. Two things did that, both on the
non-streaming path only:

* the primary had no ceiling of its own: the adapter's 30 s read timeout,
  tried three times (``provider_max_retries`` = 2) with backoff, ~91 s;
* the rescue walk's turn ceiling (``limits.non_stream.total_budget_s``,
  25 s) counts from the primary's dispatch, so a primary that waited 30 s
  (let alone 91 s) left no time for any candidate: the walk opened none and
  raised "Fallback exhausted with no candidates attempted." (502).

Pinned here: the policy file gives the self-hosted model a primary wait (30 s,
the read timeout it already had, tried once) and a turn ceiling that leaves
the walk its two rescue attempts; other models keep what they had; the route
cuts a primary that exceeds its wait, the router's closest model answers, the
response says who answered and at whose price, the answering model's price is
charged, and a turn that fails entirely is not charged.

And the other side of it: a caller whose data must never reach another model
(the Agent Market runner's in-organisation calls) sends ``X-Sran-Fallback:
off`` and gets the model it named or that model's own error — on both paths,
for that request only.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings as app_settings
from app.core.exceptions import ProviderError, ProviderTimeoutError
from app.domain.entities import RequestContext
from app.domain.providers import ProviderResponse
from app.services.auto_router import router
from app.services.gateway import fallback_orchestrator as fo
from app.services.gateway import fallback_policy as fp
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator

QWEN = ("sranai", "qwen3.8-27b-fp8")
PROVIDER_OF = {
    "qwen3.8-27b-fp8": "sranai",
    "gpt-5-mini": "openai",
    "deepseek-v4-pro": "deepseek",
    "gpt-4o": "openai",
}
# What the Auto router would offer for the same request.
ROUTER_LIST = ["gpt-5-mini", "deepseek-v4-pro"]


# ---------------------------------------------------------------------------
# Fakes (the FIX124 shapes)
# ---------------------------------------------------------------------------

class _Keys:
    def get_api_key(self, session, provider, model=None):
        return f"sk-{provider}"

    async def get_api_key_async(self, session, provider, model=None):
        return self.get_api_key(session, provider, model)


class _Dispatcher:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.adapters = {p: MagicMock(supports_streaming=True) for p in ("openai", "deepseek", "sranai")}

    async def dispatch(self, *, route, payload, messages, api_key):
        self.calls.append((route.provider_name, route.model_name))
        return ProviderResponse(content="ok", model=route.model_name, finish_reason="stop")


def _ctx() -> RequestContext:
    return RequestContext(tenant_id="tenant-x", project_id="proj-x", project_name="Test",
                          request_id="req_unit", turn_id="turn-api-qwen")


def _payload(model=QWEN[1]):
    p = MagicMock()
    p.model = model
    p.metadata = {}
    p.messages = []
    p.tools = None
    return p


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(
        "app.services.providers.model_registry.resolve_provider", lambda model: PROVIDER_OF.get(model)
    )


@pytest.fixture
def no_redis(monkeypatch):
    def boom():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)


# ---------------------------------------------------------------------------
# The policy file: the self-hosted model's own non-streaming ceilings
# ---------------------------------------------------------------------------

def test_the_self_hosted_model_has_its_own_non_stream_ceilings_in_the_policy_file():
    # one wait of the read timeout it already had, never three
    assert fp.primary_timeout_s(QWEN[1], "non_stream") == 30.0
    assert fp.primary_timeout_s(QWEN[1], "non_stream") == app_settings.provider_timeout_read
    lim = fp.limits("non_stream", model=QWEN[1])
    assert lim["total_budget_s"] == 50.0
    # after the primary's whole wait, the walk still has its two attempts
    assert lim["total_budget_s"] - 30.0 >= lim["max_candidates"] * lim["attempt_timeout_s"]
    # everything else on that turn is the file's non-streaming ceilings
    base = fp.limits("non_stream")
    assert {k: v for k, v in lim.items() if k != "total_budget_s"} == {
        k: v for k, v in base.items() if k != "total_budget_s"
    }


def test_other_models_and_the_streaming_path_keep_what_they_had():
    for model in ("gpt-4o", "deepseek-v4-pro", "deepseek-coder", "qwen3.6-35b-a3b", "auto"):
        assert fp.primary_timeout_s(model, "non_stream") is None
        assert fp.limits("non_stream", model=model) == fp.limits("non_stream")
    # web and desktop turns stream: their first-token wait and ceilings are untouched
    assert fp.primary_timeout_s(QWEN[1], "stream") is None
    assert fp.limits("stream", model=QWEN[1]) == fp.limits("stream")
    # the per-kind limits a caller reads without a model are the shipped ones
    assert fp.limits("non_stream") == {
        "max_candidates": 2, "attempt_timeout_s": 10.0, "total_budget_s": 25.0, "min_attempt_runway_s": 2.0,
    }


def test_model_limits_can_be_overridden_per_deployment_or_tenant_and_nothing_else(settings):
    ok = {"model_limits": {"non_stream": {"gpt-4o": {"primary_timeout_s": 20.0, "total_budget_s": 40.0}}}}
    assert fp.validate_override(ok, is_tenant=False) == ok
    assert "model_limits" in fp.base_policy()["overrides"]["overridable"]
    for bad in (
        {"model_limits": {"stream": {"gpt-4o": {"primary_timeout_s": 5.0}}}},
        {"model_limits": {"non_stream": {"gpt-4o": {"retries": 0}}}},
        {"model_limits": {"non_stream": {"gpt-4o": {"primary_timeout_s": "soon"}}}},
        {"model_limits": {"non_stream": {"gpt-4o": {"primary_timeout_s": 0}}}},
        {"model_limits": {"non_stream": {"gpt-4o": 30}}},
        {"model_limits": {"non_stream": []}},
        {"model_limits": []},
    ):
        with pytest.raises(ValueError):
            fp.validate_override(bad, is_tenant=True)

    settings["fallback_policy"] = json.dumps(ok)
    fp.clear_cache()
    eff = fp.effective_policy("tenant-x")
    assert fp.primary_timeout_s("gpt-4o", "non_stream", eff) == 20.0
    assert fp.limits("non_stream", eff, model="gpt-4o")["total_budget_s"] == 40.0
    # merged per model: the shipped entry is still there
    assert fp.primary_timeout_s(QWEN[1], "non_stream", eff) == 30.0

    # null switches a shipped entry off: the adapter's own wait and the file's ceiling
    settings["fallback_policy"] = json.dumps(
        {"model_limits": {"non_stream": {QWEN[1]: {"primary_timeout_s": None, "total_budget_s": None}}}}
    )
    fp.clear_cache()
    off = fp.effective_policy(None)
    assert fp.primary_timeout_s(QWEN[1], "non_stream", off) is None
    assert fp.limits("non_stream", off, model=QWEN[1]) == fp.limits("non_stream")


# ---------------------------------------------------------------------------
# The walk: a primary that used its whole wait still gets its rescue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_primary_that_used_its_whole_wait_is_still_rescued(settings, registry, no_redis):
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys())
    res = await runner.run(
        original_provider=QWEN[0], original_model=QWEN[1], request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("no answer in 30 s"),
        # the route measures the turn from the primary's dispatch: 30 s are gone
        started_at=time.perf_counter() - 30.0,
        rescue_candidates=ROUTER_LIST,
    )
    assert (res.provider_used, res.model_used) == ("openai", "gpt-5-mini")
    assert res.attempt_number == 2
    assert disp.calls == [("openai", "gpt-5-mini")]


@pytest.mark.asyncio
async def test_another_models_turn_ceiling_is_unchanged(settings, registry, no_redis):
    # gpt-4o has no entry: the file's 25 s turn ceiling applies exactly as before
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys())
    with pytest.raises(ProviderError):
        await runner.run(
            original_provider="openai", original_model="gpt-4o", request=_payload("gpt-4o"), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("no answer in 30 s"),
            started_at=time.perf_counter() - 30.0, rescue_candidates=ROUTER_LIST,
        )
    assert disp.calls == []


# ---------------------------------------------------------------------------
# The public route, end to end (non-streaming, the model named). The live
# model is not in the test catalogue, so a seeded model without a chain
# (deepseek-coder) stands in, with its own entry set by a deployment
# override; the Auto router is a stub that ranks three seeded models.
# ---------------------------------------------------------------------------

E2E_PRIMARY = ("deepseek", "deepseek-coder")
E2E_ROUTER = ["gpt-4o-mini", "gpt-4o", "claude-3-haiku-20240307"]
_HEADERS = {"Authorization": "Bearer demo-key"}
_PRIMARY_WAIT_S = 0.5
_HARD_S = 12.0  # the route's own hard timeout (120 s live), shortened so the old code fails in seconds


def _plan(cands) -> router.RoutePlan:
    return router.RoutePlan(
        primary=cands[0], fallbacks=list(cands[1:]), task="chat", task_confidence=1.0,
        tier="medium", reason="test", candidates_count=len(cands), filtered_count=len(cands),
    )


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.gateway import portal_credit_check
    from app.services.orchestrator import GatewayOrchestrator
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.providers.model_registry import get_candidate

    async def fake_route(self, ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        return _plan([get_candidate(m) for m in E2E_ROUTER])

    monkeypatch.setattr(GatewayOrchestrator, "_auto_route", fake_route)
    store: dict[str, str] = {"fallback_policy": json.dumps({
        "rescue_order": {"rule": "router_ranking"},
        "model_limits": {"non_stream": {E2E_PRIMARY[1]: {"primary_timeout_s": _PRIMARY_WAIT_S, "total_budget_s": 3.0}}},
    })}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    monkeypatch.setattr(app_settings, "request_hard_timeout_seconds", _HARD_S)

    calls: list[str] = []
    hung: set[str] = {E2E_PRIMARY[1]}

    async def _dispatch(self, route, payload, messages, api_key=None):
        calls.append(route.model_name)
        if route.model_name in hung:
            await asyncio.sleep(3600)  # the box took the request and never answered
        return ProviderResponse(content="answer from the model that works", model=route.model_name,
                                finish_reason="stop")

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _dispatch)

    charged: list[dict] = []

    async def _deduct(session, key_id, request_id, **kw):
        charged.append({"request_id": request_id, **kw})

    monkeypatch.setattr(portal_credit_check, "deduct_portal_credit_async", _deduct)
    client = TestClient(app, raise_server_exceptions=False)

    def post(metadata: dict | None = None, headers: dict | None = None, stream: bool = False):
        return client.post("/v1/chat/completions", headers={**_HEADERS, **(headers or {})}, json={
            "model": E2E_PRIMARY[1], "stream": stream, "metadata": metadata or {},
            "messages": [{"role": "user", "content": f"api rescue {uuid.uuid4().int % 10**6}"}],
        })

    post.calls, post.hung, post.charged = calls, hung, charged
    yield post
    fp.clear_cache()


def test_api_explicit_model_that_stops_answering_is_rescued_and_says_by_whom(api):
    t0 = time.perf_counter()
    resp = api()
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # the model that answered, in the body and the headers
    assert body["model"] == "gpt-4o-mini"
    assert resp.headers["X-Gateway-Model"] == "gpt-4o-mini"
    assert resp.headers["X-Gateway-Fallback"] == "true" and resp.headers["X-Gateway-Attempt"] == "2"
    assert body["choices"][0]["message"]["content"] == "answer from the model that works"
    assert api.calls == ["deepseek-coder", "gpt-4o-mini"]
    # the primary was cut at its own wait, not at the route's hard timeout
    assert elapsed < _HARD_S - 2.0

    # the same notice the web path shows: asked, answered, tried, charged at
    from app.services.providers.model_registry import get_candidate

    info = body["fallback_info"]
    assert info["requested_model"] == "deepseek-coder" and info["actual_model"] == "gpt-4o-mini"
    assert [(a["model"], a.get("error_code") or a.get("status")) for a in info["attempts"]] == [
        ("deepseek-coder", "provider_timeout"), ("gpt-4o-mini", "success"),
    ]
    assert info["attempts"][0]["latency_ms"] >= _PRIMARY_WAIT_S * 1000 * 0.9
    billed = get_candidate("gpt-4o-mini")
    assert info["billing"] == {
        "rule": "answering_model",
        "billed_model": "gpt-4o-mini",
        "requested_model": "deepseek-coder",
        "billed_usd_per_1m": {
            "input": round(billed.cost_per_input_token * 1_000_000, 4),
            "output": round(billed.cost_per_output_token * 1_000_000, 4),
        },
        "requested_usd_per_1m": fo.usd_per_1m(get_candidate("deepseek-coder")),
    }

    # the record and the charge are the answering model's
    from app.models.request_log import RequestLogORM
    from tests.conftest import _IntegrationSession

    with _IntegrationSession() as s:
        row = s.query(RequestLogORM).filter(RequestLogORM.request_id == body["id"]).one()
        assert row.fallback_used is True
        assert (row.original_provider, row.original_model) == E2E_PRIMARY
        assert (row.provider, row.model) == ("openai", "gpt-4o-mini")
        # the primary's failure is on the record, as on the web path
        assert [(a["model"], a["error_code"]) for a in row.fallback_attempts or []] == [
            ("deepseek-coder", "provider_timeout"),
        ]
        cost = row.estimated_cost_usd
    assert cost == body["usage"]["estimated_cost_usd"]
    assert [(c["request_id"], c["cost_usd"]) for c in api.charged] == [(body["id"], cost)]


def test_api_turn_where_every_model_is_down_is_an_error_and_is_not_charged(api):
    api.hung.update(E2E_ROUTER)
    t0 = time.perf_counter()
    resp = api()
    elapsed = time.perf_counter() - t0
    assert resp.status_code >= 500, resp.text
    assert "fallback_info" not in resp.text
    assert api.charged == []
    # the primary once, then the walk inside the turn's own ceiling — the
    # caller hears the walk's error, not the route's hard timeout
    assert api.calls[:2] == ["deepseek-coder", "gpt-4o-mini"]
    assert resp.json()["error"].get("type") != "upstream_timeout", resp.text
    assert elapsed < _HARD_S - 2.0


# ---------------------------------------------------------------------------
# The per-request opt-out: ``X-Sran-Fallback: off`` — the model named, or its
# own error; never another model (the Agent Market runner's in-organisation
# calls: company documents must not reach any other model).
# ---------------------------------------------------------------------------

def test_the_opt_out_header_values():
    assert fp.OPT_OUT_HEADER == "X-Sran-Fallback"
    for value in ("off", "OFF", " off ", "false", "0", "no", "none"):
        assert fp.header_opts_out(value), value
    for value in (None, "", "on", "true", "1", "auto", "sranai"):
        assert not fp.header_opts_out(value), value


def test_the_opt_out_is_the_kill_switch_for_one_request_only(settings):
    import contextvars

    assert fp.is_enabled() is True

    def _inside():
        fp.opt_out_request()
        return fp.is_enabled(), asyncio.run(fp.is_enabled_async()), fp.disabled_reason()

    # inside the request (its own context): off, and the logs say why
    assert contextvars.copy_context().run(_inside) == (False, False, "request_opt_out")
    # every other request: untouched, and the switch's cache was not written
    assert fp.is_enabled() is True and fp.request_opted_out() is False
    assert fp.disabled_reason() == "kill_switch"


@pytest.mark.asyncio
async def test_explicit_qwen_with_the_opt_out_gets_no_other_model_and_without_it_is_rescued(
    settings, registry, no_redis,
):
    import contextvars

    async def _walk():
        disp = _Dispatcher()
        runner = FallbackOrchestrator(disp, _Keys())
        original = ProviderTimeoutError("no answer in 30 s")
        try:
            res = await runner.run(
                original_provider=QWEN[0], original_model=QWEN[1], request=_payload(), messages=[],
                session=MagicMock(), ctx=_ctx(), original_error=original,
                started_at=time.perf_counter() - 30.0, rescue_candidates=ROUTER_LIST,
            )
            return res.model_used, disp.calls
        except ProviderTimeoutError as exc:
            assert exc is original  # its own error, untouched
            return None, disp.calls

    async def _opted_out():
        fp.opt_out_request()
        return await _walk()

    # with the header: no fallback attempt of any kind
    assert await asyncio.get_running_loop().create_task(
        _opted_out(), context=contextvars.copy_context()
    ) == (None, [])
    # without it: rescued by the closest model the router offers
    assert await _walk() == ("gpt-5-mini", [("openai", "gpt-5-mini")])


def test_api_opt_out_header_gets_the_named_models_own_error_and_no_other_model(api):
    t0 = time.perf_counter()
    resp = api(headers={"X-Sran-Fallback": "off"})
    elapsed = time.perf_counter() - t0
    # the named model only; its own wait still applies, then its own error
    assert api.calls == ["deepseek-coder"]
    assert resp.status_code >= 500, resp.text
    assert resp.json()["error"]["code"] == "provider_timeout", resp.text
    assert "fallback_info" not in resp.text and "X-Gateway-Fallback" not in resp.headers
    assert api.charged == []
    assert elapsed < _HARD_S - 2.0

    # the next request from the same client, without the header, is rescued
    api.calls.clear()
    again = api()
    assert again.status_code == 200, again.text
    assert again.json()["model"] == "gpt-4o-mini" and api.calls == ["deepseek-coder", "gpt-4o-mini"]


def _stream_frames(body: bytes) -> list[dict]:
    return [
        json.loads(line[6:]) for line in body.decode().splitlines()
        if line.startswith("data: ") and line[6:].strip() != "[DONE]"
    ]


def _stream_fake(fail: bool):
    from app.domain.providers import ProviderStreamChunk

    async def _stream(self, payload, messages, *, resolved_model=None, api_key=None):
        _stream.calls.append(resolved_model)
        if fail:
            raise ProviderError("upstream 503", code="provider_5xx", details={"provider": "deepseek", "status": 503})
        yield ProviderStreamChunk(delta="answer from the model that works", finish_reason="stop", model=resolved_model)

    _stream.calls = []
    return _stream


def test_stream_opt_out_header_never_swaps_the_model_at_the_precheck_or_in_the_stream(api, monkeypatch):
    from app.services.providers.deepseek_adapter import DeepSeekAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    dead, alive = _stream_fake(fail=True), _stream_fake(fail=False)
    monkeypatch.setattr(DeepSeekAdapter, "stream_chat", dead)
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", alive)

    # the in-stream swap: the named model fails before its first byte
    frames = _stream_frames(api(headers={"X-Sran-Fallback": "off"}, stream=True).content)
    assert [f["error"]["code"] for f in frames if "error" in f] == ["provider_5xx"]
    assert not [f for f in frames if f.get("type") == "fallback_info"]
    assert dead.calls == ["deepseek-coder"] and alive.calls == []

    # the pre-check swap: the named model is marked as failing a moment ago
    async def _marked(provider, model=None):
        return provider == E2E_PRIMARY[0]

    monkeypatch.setattr(fo, "is_provider_unhealthy", _marked)
    frames = _stream_frames(api(headers={"X-Sran-Fallback": "off"}, stream=True).content)
    assert dead.calls == ["deepseek-coder", "deepseek-coder"] and alive.calls == []
    assert not [f for f in frames if f.get("type") == "fallback_info"]

    # without the header the same turn is rescued (the web path, unchanged)
    frames = _stream_frames(api(stream=True).content)
    info = [f["fallback_info"] for f in frames if f.get("type") == "fallback_info"]
    assert info and info[0]["actual_model"] == "gpt-4o-mini" and alive.calls == ["gpt-4o-mini"]


def test_api_caller_that_keeps_its_data_inside_is_never_rescued_outside(api):
    # A caller whose data must stay with its own providers (the agent runner's
    # self-hosted-only calls) names them in metadata.allowed_providers; the
    # walk never crosses that line — the turn fails at the primary's wait
    # instead of going to another provider, and is not charged.
    t0 = time.perf_counter()
    resp = api(metadata={"allowed_providers": [E2E_PRIMARY[0]]})
    elapsed = time.perf_counter() - t0
    assert resp.status_code >= 500, resp.text
    assert api.calls == ["deepseek-coder"]
    assert api.charged == []
    assert elapsed < _HARD_S - 2.0
