"""FIX121 — the streaming path walks the Auto router's alternatives.

The router attaches its ranked alternatives to the request
(``metadata.auto_candidates``). The non-streaming walk merged them into the
fallback chain (FIX35); the two streaming selectors did not, so an Auto turn
on the path every web and desktop turn takes had no next candidate and ended
``all_models_failed`` after one attempt.

What stays exactly as it was is asserted next to what changed: an explicit
request (no list) on a model with no chain still has nothing to fall back to,
the kill switch still ends the walk, the boundary and the capability gate
still skip, and a pinned opt-in action still walks its own chain only.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import ProviderTimeoutError
from app.domain.entities import RequestContext, RouteDecision
from app.domain.providers import ProviderResponse
from app.services.gateway import fallback_orchestrator as fo
from app.services.gateway import fallback_policy as fp
from app.services.gateway.fallback_orchestrator import (
    FallbackOrchestrator,
    chain_with_auto_candidates,
)

# The live shape that failed: Auto picked the in-house model, which has no
# chain of its own, and the router ranked these behind it.
PRIMARY = ("sranai", "qwen3.8-27b-fp8")
ROUTER_LIST = ["gpt-5-mini", "deepseek-v4-pro", "Typhoon-S-ThaiLLM-8B-Instruct"]
PROVIDER_OF = {
    "qwen3.8-27b-fp8": "sranai",
    "Typhoon-S-ThaiLLM-8B-Instruct": "sranai",
    "gpt-5-mini": "openai",
    "deepseek-v4-pro": "deepseek",
}


class _Keys:
    def __init__(self, keys_for: set[str]):
        self.keys_for = keys_for

    def get_api_key(self, session, provider, model=None):
        return f"sk-{provider}" if provider in self.keys_for else None

    async def get_api_key_async(self, session, provider, model=None):
        return self.get_api_key(session, provider, model)


class _Dispatcher:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []
        self.adapters = {p: MagicMock(supports_streaming=True) for p in ("openai", "deepseek", "sranai", "google", "anthropic")}

    async def dispatch(self, *, route, payload, messages, api_key):
        self.calls.append((route.provider_name, route.model_name))
        if route.model_name in self.fail:
            raise ProviderTimeoutError("no first token", details={"provider": route.provider_name})
        return ProviderResponse(content="ok", model=route.model_name, finish_reason="stop")


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(
        "app.services.providers.model_registry.resolve_provider",
        lambda model: PROVIDER_OF.get(model),
    )


@pytest.fixture(autouse=True)
def _mock_provider_dispatch():
    yield


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    def boom():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)


async def _healthy(provider, model=None):
    return False


def _runner(keys=("openai", "deepseek", "sranai")):
    return FallbackOrchestrator(_Dispatcher(), _Keys(set(keys)))


def _select(runner, **kw):
    return runner.select_streaming_candidate(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], session=MagicMock(), **kw
    )


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------

def test_explicit_request_on_a_chainless_model_still_has_no_candidate(settings):
    skipped: list[dict] = []
    assert _select(_runner(), skipped=skipped) is None
    # nothing was even looked at: the caller reports this as ``no_chain``
    assert skipped == []


@pytest.mark.asyncio
async def test_explicit_request_async_selector_is_unchanged(settings, monkeypatch):
    monkeypatch.setattr(fo, "is_provider_unhealthy", _healthy)
    pick = await _runner().select_streaming_candidate_async(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], session=MagicMock(),
    )
    assert pick is None


def test_an_empty_or_missing_list_returns_the_chain_object_untouched():
    chain = [{"provider": "google", "model": "gemini-2.5-flash"}]
    for empty in (None, [], ()):
        out, merged = chain_with_auto_candidates(
            chain, empty, action="cooldown_then_next",
            original_provider="openai", original_model="gpt-4o",
        )
        assert out is chain and merged is False


def test_kill_switch_still_ends_the_walk_with_a_router_list(settings):
    settings["fallback_enabled"] = "false"
    fp.clear_cache()
    assert _select(_runner(), auto_candidates=ROUTER_LIST) is None


def test_a_pinned_opt_in_action_walks_its_own_chain_not_the_router_list(settings):
    # context_window / content_policy chains are opt-in and named by the
    # policy; the router's list must not widen them.
    assert _select(_runner(), auto_candidates=ROUTER_LIST, action="context_window_chain") is None


# ---------------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------------

def test_streaming_selector_walks_the_router_list(settings):
    pick = _select(_runner(), auto_candidates=ROUTER_LIST)
    assert pick == {"provider": "openai", "model": "gpt-5-mini", "api_key": "sk-openai"}


@pytest.mark.asyncio
async def test_pre_check_selector_walks_the_router_list(settings, monkeypatch):
    monkeypatch.setattr(fo, "is_provider_unhealthy", _healthy)
    pick = await _runner().select_streaming_candidate_async(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], session=MagicMock(),
        auto_candidates=ROUTER_LIST,
    )
    assert (pick["provider"], pick["model"]) == ("openai", "gpt-5-mini")


def test_each_failed_pair_moves_the_walk_to_the_next_candidate(settings):
    runner = _runner()
    pairs: set[tuple[str, str]] = set()
    order = []
    for _ in range(4):
        pick = _select(runner, auto_candidates=ROUTER_LIST, skip_pairs=pairs)
        if pick is None:
            break
        order.append(pick["model"])
        pairs.add((pick["provider"], pick["model"]))
    # other providers first (the primary's provider just failed), then the
    # primary's own provider, then nothing
    assert order == ["gpt-5-mini", "deepseek-v4-pro", "Typhoon-S-ThaiLLM-8B-Instruct"]


def test_a_provider_wide_failure_rules_out_the_router_candidates_on_that_provider(settings):
    pick = _select(
        _runner(), auto_candidates=["Typhoon-S-ThaiLLM-8B-Instruct", "deepseek-v4-pro"],
        skip_providers={"sranai"},
    )
    assert pick["model"] == "deepseek-v4-pro"


def test_the_boundary_still_skips_router_candidates_and_says_so(settings):
    skipped: list[dict] = []
    pick = _select(
        _runner(), auto_candidates=ROUTER_LIST, allowed_providers=["sranai"], skipped=skipped,
    )
    assert pick["model"] == "Typhoon-S-ThaiLLM-8B-Instruct"
    assert [(s["model"], s["reason"]) for s in skipped] == [
        ("gpt-5-mini", "outside_boundary"),
        ("deepseek-v4-pro", "outside_boundary"),
    ]


def test_the_capability_gate_still_skips_router_candidates(settings, monkeypatch):
    monkeypatch.setattr(
        "app.services.capability_detection.model_meets",
        lambda required, model: model != "gpt-5-mini",
    )
    skipped: list[dict] = []
    pick = _select(_runner(), auto_candidates=ROUTER_LIST, required=MagicMock(), skipped=skipped)
    assert pick["model"] == "deepseek-v4-pro"
    assert skipped == [{"provider": "openai", "model": "gpt-5-mini", "reason": "capability_mismatch"}]


def test_a_router_candidate_without_a_key_is_skipped_not_tried(settings):
    skipped: list[dict] = []
    pick = _select(_runner(keys=("deepseek",)), auto_candidates=ROUTER_LIST, skipped=skipped)
    assert pick["model"] == "deepseek-v4-pro"
    assert skipped[0] == {"provider": "openai", "model": "gpt-5-mini", "reason": "no_api_key"}


def test_the_primary_and_duplicates_never_come_back_as_candidates(settings):
    pick = _select(_runner(), auto_candidates=[PRIMARY[1], "gpt-5-mini", "gpt-5-mini"],
                   skip_pairs={("openai", "gpt-5-mini")})
    assert pick is None


# ---------------------------------------------------------------------------
# One merge, three callers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_and_non_streaming_walk_the_same_order(settings):
    """The order the non-streaming walk dispatches in is the order repeated
    streaming selections yield."""
    # A timeout on the primary rules out the primary's whole provider for
    # this request (FIX51 account scope) on both paths, so the third name on
    # the router's list — same provider as the primary — is never reached.
    disp = _Dispatcher(fail={"gpt-5-mini"})
    runner = FallbackOrchestrator(disp, _Keys({"openai", "deepseek", "sranai"}), max_candidates=5)
    payload = MagicMock()
    payload.model = PRIMARY[1]
    payload.metadata = {"auto_candidates": list(ROUTER_LIST)}
    payload.messages = []
    payload.tools = None
    await runner.run(
        ctx=RequestContext(tenant_id="t", project_id="p", project_name="P", request_id="req_x", turn_id="turn-x"),
        request=payload, messages=[], original_provider=PRIMARY[0], original_model=PRIMARY[1],
        original_error=ProviderTimeoutError("no first token"), session=MagicMock(),
    )
    non_stream_order = [m for _, m in disp.calls]

    pairs: set[tuple[str, str]] = set()
    stream_order = []
    while True:
        pick = _select(runner, auto_candidates=ROUTER_LIST, skip_pairs=pairs, skip_providers={PRIMARY[0]})
        if pick is None:
            break
        stream_order.append(pick["model"])
        pairs.add((pick["provider"], pick["model"]))
    assert non_stream_order == ["gpt-5-mini", "deepseek-v4-pro"]
    assert stream_order == non_stream_order


def test_every_streaming_selection_in_the_chat_route_passes_the_router_list():
    """This is the fourth time the streaming path dropped something the
    non-streaming path carried. Pin the call sites: any call to a streaming
    selector in the chat route names ``auto_candidates``."""
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "routes" / "public_chat.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("select_streaming_candidate", "select_streaming_candidate_async")
    ]
    assert len(calls) >= 2, "expected the pre-check and the in-generator selection"
    for call in calls:
        assert "auto_candidates" in {k.arg for k in call.keywords}, (
            f"public_chat.py:{call.lineno} selects a streaming fallback without the router's list"
        )
