"""FIX120 — the fallback policy is data, and the shipped data is f042040.

Every assertion here is either a pin of the file's shape against the
other contracts (the error catalogue, terminal_cause.json, the adapter's
retry table) or a literal copy of the pre-FIX120 code sets — so a change
to the file that would change behaviour fails here first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import error_catalog
from app.services.gateway import fallback_policy as fp
from app.services.gateway.fallback_orchestrator import (
    FALLBACK_ELIGIBLE_ERRORS,
    FALLBACK_ATTEMPT_TIMEOUT_S,
    FALLBACK_TOTAL_BUDGET_S,
    MAX_FALLBACK_CANDIDATES,
    _ACCOUNT_SCOPED_CODES,
    _HEALTH_TTL_SECONDS,
    _MIN_ATTEMPT_RUNWAY_S,
    _PROVIDER_WIDE_CODES,
    _REQUEST_SCOPED_CODES,
    classify_failure_scope,
)
from app.services.providers import model_tiers
from app.services.providers.error_handler import _RETRYABLE_STATUS

REPO = Path(__file__).resolve().parents[2]
DOCS_COPY = REPO / "docs" / "contracts" / "fallback_policy.json"
TERMINAL = REPO / "docs" / "contracts" / "terminal_cause.json"


# ---------------------------------------------------------------------------
# The pre-FIX120 code, verbatim — what the file must reproduce.
# ---------------------------------------------------------------------------

OLD_FALLBACK_ELIGIBLE = {
    "provider_timeout", "provider_5xx", "provider_error", "provider_rate_limited",
    "provider_at_capacity", "rate_limit_exceeded", "provider_model_not_found",
    "provider_unavailable", "provider_no_credit", "provider_auth_error",
}
OLD_REQUEST_SCOPED = {
    "context_length_exceeded", "validation_error", "upstream_bad_response",
    "content_policy_blocked", "moderation_flagged", "guardrail_blocked",
    "dlp_violation", "policy_violation",
}
OLD_ACCOUNT_SCOPED = {
    "provider_auth_error", "provider_no_credit", "provider_unavailable",
    "provider_timeout", "provider_at_capacity", "provider_rate_limited",
    "rate_limit_exceeded", "provider_5xx",
}
OLD_PROVIDER_WIDE = {"provider_unavailable", "provider_timeout", "provider_no_credit"}
OLD_CURATED = {
    "claude-sonnet-4": [("openrouter", "anthropic/claude-sonnet-4.5"), ("deepseek", "deepseek-v4-pro")],
    "claude-sonnet-4.5": [("openrouter", "anthropic/claude-sonnet-4"), ("deepseek", "deepseek-v4-pro")],
    "gpt-4o": [("openrouter", "anthropic/claude-sonnet-4"), ("deepseek", "deepseek-v4-pro")],
    "gemini-2.5-pro": [("openrouter", "anthropic/claude-sonnet-4"), ("deepseek", "deepseek-v4-pro")],
    "deepseek-r1": [("openrouter", "openai/gpt-4o"), ("deepseek", "deepseek-v4-pro")],
    "claude-opus-4.1": [("openrouter", "anthropic/claude-sonnet-4.5"), ("deepseek", "deepseek-v4-pro")],
    "claude-opus-4": [("openrouter", "anthropic/claude-sonnet-4.5"), ("deepseek", "deepseek-v4-pro")],
    "o3-mini": [("openrouter", "deepseek/deepseek-r1"), ("deepseek", "deepseek-v4-pro")],
    "o4-mini": [("openrouter", "deepseek/deepseek-r1"), ("deepseek", "deepseek-v4-pro")],
    "claude-3.5-haiku": [("openrouter", "openai/gpt-4o-mini"), ("deepseek", "deepseek-v4-flash")],
    "gpt-4o-mini": [("openrouter", "anthropic/claude-3.5-haiku"), ("deepseek", "deepseek-v4-flash")],
    "gemini-2.5-flash": [("openrouter", "openai/gpt-4o-mini"), ("deepseek", "deepseek-v4-flash")],
    "deepseek-chat": [("openrouter", "openai/gpt-4o-mini"), ("deepseek", "deepseek-v4-flash")],
    "llama-3.3-70b-instruct": [("deepseek", "deepseek-v4-flash"), ("openrouter", "openai/gpt-4o-mini")],
    "deepseek-v4-pro": [("openrouter", "openai/gpt-4o"), ("openai", "gpt-4o-mini")],
    "deepseek-v4-flash": [("openrouter", "meta-llama/llama-3.3-70b-instruct"), ("openai", "gpt-4o-mini")],
}
OLD_TIER_MODELS = {
    "premium": ["gpt-4o", "claude-opus-4", "claude-opus-4-5", "gemini-1.5-pro", "gpt-4-turbo"],
    "standard": ["gpt-4o-mini", "claude-sonnet-4", "claude-sonnet-4-5", "gemini-1.5-flash", "llama-3.1-70b", "llama-3.1-70b-versatile"],
    "fast": ["gpt-3.5-turbo", "claude-haiku-4-5", "claude-haiku-3", "gemini-flash", "llama-3.1-8b", "llama-3.1-8b-instant"],
}
OLD_TIER_CHAINS = {
    "premium": ["openai", "anthropic", "google", "openrouter"],
    "standard": ["openai", "anthropic", "google", "groq", "openrouter"],
    "fast": ["groq", "openai", "anthropic", "openrouter"],
}


# ---------------------------------------------------------------------------
# One file, two copies, one shape
# ---------------------------------------------------------------------------

def test_packaged_copy_is_byte_identical_to_the_docs_contract():
    assert DOCS_COPY.exists(), "docs/contracts/fallback_policy.json is the canonical file"
    assert fp.packaged_policy_path().read_bytes() == DOCS_COPY.read_bytes()


def test_actions_are_the_closed_set_and_each_is_explained():
    pol = fp.base_policy()
    assert pol["actions"] == [
        "retry_same", "cooldown_then_next", "next_in_chain",
        "context_window_chain", "content_policy_chain", "give_up",
    ]
    assert set(pol["action_meaning"]) == set(pol["actions"])
    assert fp.WALK_ACTIONS <= set(pol["actions"])


def test_every_catalogue_code_has_a_row_and_every_row_uses_a_known_action():
    pol = fp.base_policy()
    rows = pol["decision_table"]["error_code"]
    missing = [c for c in error_catalog.CATALOG if c not in rows]
    assert not missing, f"catalogue codes without a policy row: {missing}"
    bad = {c: a for c, a in rows.items() if a not in pol["actions"]}
    assert not bad
    assert pol["decision_table"]["error_code_default"] == "give_up"


def test_terminal_cause_and_stop_reason_rows_match_the_fix118_119_contract():
    contract = json.loads(TERMINAL.read_text(encoding="utf-8"))
    pol = fp.base_policy()
    assert set(pol["decision_table"]["terminal_cause"]) == set(contract["causes"])
    assert set(pol["decision_table"]["stop_reason"]) == set(contract["stop_reasons"])
    # a delivered answer, or an ending observed after the backend was done,
    # never re-runs the turn
    for cause, action in pol["decision_table"]["terminal_cause"].items():
        assert action == ("by_error_code" if cause == "error" else "give_up"), cause
    assert all(a == "give_up" for a in pol["decision_table"]["stop_reason"].values())


def test_proposals_are_not_loaded():
    raw = json.loads(fp.packaged_policy_path().read_text(encoding="utf-8"))
    assert "proposals" in raw and "qwen3.8-27b-fp8" in raw["proposals"]["chains"]
    assert "proposals" not in fp.base_policy()
    # the drafted chain is NOT a chain
    assert fp.chain_for("qwen3.8-27b-fp8", "sranai") == []
    assert model_tiers.get_fallback_chain("qwen3.8-27b-fp8", "sranai") == []


# ---------------------------------------------------------------------------
# Default = f042040, set by set
# ---------------------------------------------------------------------------

def test_eligible_codes_equal_the_pre_fix120_set():
    assert fp.eligible_error_codes() == frozenset(OLD_FALLBACK_ELIGIBLE)
    assert FALLBACK_ELIGIBLE_ERRORS == OLD_FALLBACK_ELIGIBLE


def test_failure_scope_sets_equal_the_pre_fix120_sets():
    pol = fp.base_policy()["failure_scope"]
    assert set(pol["request"]) == OLD_REQUEST_SCOPED
    assert set(pol["account"]) == OLD_ACCOUNT_SCOPED
    assert _REQUEST_SCOPED_CODES == frozenset(OLD_REQUEST_SCOPED)
    assert _ACCOUNT_SCOPED_CODES == frozenset(OLD_ACCOUNT_SCOPED)


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("context_length_exceeded", None, "request"),
        ("provider_model_not_found", None, "model"),
        ("provider_error", 404, "model"),
        ("provider_error", 401, "account"),
        ("provider_error", 402, "account"),
        ("provider_error", 403, "account"),
        ("provider_error", 503, "account"),
        ("provider_error", 400, "model"),
        ("provider_no_credit", None, "account"),
        ("something_new", None, "model"),
        (None, None, "model"),
    ],
)
def test_failure_scope_status_tie_breaks_are_the_old_ones(code, status, expected):
    assert fp.failure_scope_for(code, status) == expected


def test_classify_failure_scope_reads_the_same_table():
    from app.core.exceptions import ProviderError

    assert classify_failure_scope(ProviderError("x", code="provider_error", details={"status": 402})) == "account"
    assert classify_failure_scope(ProviderError("x", code="provider_error", details={"status": 404})) == "model"


def test_health_marker_and_ceilings_are_the_shipped_numbers():
    assert fp.provider_wide_codes() == frozenset(OLD_PROVIDER_WIDE)
    assert _PROVIDER_WIDE_CODES == frozenset(OLD_PROVIDER_WIDE)
    assert fp.health_cooldown_s() == 60 and _HEALTH_TTL_SECONDS == 60
    assert fp.limits("non_stream") == {
        "max_candidates": 2, "attempt_timeout_s": 10.0, "total_budget_s": 25.0, "min_attempt_runway_s": 2.0,
    }
    assert (MAX_FALLBACK_CANDIDATES, FALLBACK_ATTEMPT_TIMEOUT_S, FALLBACK_TOTAL_BUDGET_S, _MIN_ATTEMPT_RUNWAY_S) == (2, 10.0, 25.0, 2.0)
    # streaming: attempt ceiling only, no wall-clock ceiling (as before)
    assert fp.limits("stream") == {"max_candidates": 2, "total_budget_s": None, "min_attempt_runway_s": 2.0}


def test_breaker_cost_cap_and_opt_ins_ship_off():
    pol = fp.base_policy()
    assert pol["breaker"]["enabled"] is False
    assert pol["cost_cap"]["enabled"] is False
    assert pol["cost_cap"]["max_cost_ratio_vs_requested"] is None
    assert pol["cost_cap"]["tenant_daily_usd"] is None
    assert pol["opt_in"] == {"context_window_fallback": False, "content_policy_fallback": False} or set(pol["opt_in"]) >= {"context_window_fallback", "content_policy_fallback"}
    assert pol["opt_in"]["context_window_fallback"] is False
    assert pol["opt_in"]["content_policy_fallback"] is False
    assert pol["kill_switch"] == {"_comment": pol["kill_switch"]["_comment"], "setting_key": "fallback_enabled", "default": True}
    assert pol["chains"]["context_window"] == {} and pol["chains"]["content_policy"] == {}


def test_curated_and_tier_tables_are_the_pre_fix120_literals():
    curated = {k: [(h["provider"], h["model"]) for h in v] for k, v in model_tiers.CURATED_FALLBACKS.items()}
    assert curated == OLD_CURATED
    assert {k: v["models"] for k, v in model_tiers.TIERS.items()} == OLD_TIER_MODELS
    assert {k: v["fallback_chain"] for k, v in model_tiers.TIERS.items()} == OLD_TIER_CHAINS
    # the same walk, whichever entry point
    for model, provider in [("gpt-4o", "openai"), ("anthropic/claude-sonnet-4", "openrouter"), ("o3-mini", "openai"), ("gpt-3.5-turbo", "openai")]:
        assert model_tiers.get_fallback_chain(model, provider) == fp.chain_for(model, provider)


def test_retry_same_rows_are_the_adapter_retry_statuses():
    rows = fp.base_policy()["decision_table"]["http_status"]
    assert {int(k) for k, v in rows.items() if v == "retry_same"} == set(_RETRYABLE_STATUS)
    assert fp.decide(http_status=429).action == "retry_same"
    assert fp.decide(http_status=400).action == "give_up"


# ---------------------------------------------------------------------------
# The decision table, row by row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", sorted(OLD_FALLBACK_ELIGIBLE))
def test_eligible_rows_walk_with_cooldown(code):
    d = fp.decide(error_code=code)
    assert d.action == "cooldown_then_next" and d.walks


@pytest.mark.parametrize("code", sorted(set(error_catalog.CATALOG) - OLD_FALLBACK_ELIGIBLE))
def test_every_other_catalogue_code_gives_up_by_default(code):
    d = fp.decide(error_code=code)
    assert d.action == "give_up" and not d.walks, (code, d)


def test_unknown_code_and_no_signal_give_up():
    assert fp.decide(error_code="never_heard_of_it").action == "give_up"
    assert fp.decide().action == "give_up"


def test_opt_in_chains_are_give_up_until_the_tenant_opts_in():
    off = fp.decide(error_code="context_length_exceeded")
    assert off.action == "give_up" and "opt-in" in off.reason
    on = fp.apply_overrides(fp.base_policy(), {"opt_in": {"context_window_fallback": True}})
    assert fp.decide(error_code="context_length_exceeded", policy=on).action == "context_window_chain"
    assert fp.decide(error_code="content_policy_blocked", policy=on).action == "give_up"
    on2 = fp.apply_overrides(fp.base_policy(), {"opt_in": {"content_policy_fallback": True}})
    assert fp.decide(error_code="content_policy_blocked", policy=on2).action == "content_policy_chain"
    # opting in without a chain still walks nothing
    assert fp.chain_for_action("context_window_chain", "gpt-4o", "openai", policy=on) == []
    with_chain = fp.apply_overrides(on, {"chains": {"context_window": {"gpt-4o": [{"provider": "google", "model": "gemini-1.5-pro"}]}}})
    assert fp.chain_for_action("context_window_chain", "gpt-4o", "openai", policy=with_chain) == [{"provider": "google", "model": "gemini-1.5-pro"}]


@pytest.mark.parametrize("cause", ["stopped_by_user", "completed", "timeout", "disconnected", "eof_without_terminal", "interrupted"])
def test_terminal_causes_never_walk(cause):
    assert fp.decide(terminal_cause=cause, error_code="provider_timeout").action == "give_up"


def test_terminal_cause_error_defers_to_the_code():
    assert fp.decide(terminal_cause="error", error_code="provider_timeout").action == "cooldown_then_next"


@pytest.mark.parametrize("reason", ["refusal", "content_filter", "max_tokens", "end_turn", "context_window_exceeded", "unknown"])
def test_stop_reasons_never_walk(reason):
    assert fp.decide(stop_reason=reason, error_code="provider_timeout").action == "give_up"


# ---------------------------------------------------------------------------
# The forbidden list — hard, not a default
# ---------------------------------------------------------------------------

def test_every_hard_forbidden_code_gives_up_and_cannot_be_overridden():
    pol = fp.base_policy()
    for code in pol["hard_forbidden"]["error_code"]:
        assert fp.decide(error_code=code).action == "give_up", code
        with pytest.raises(ValueError, match="hard-forbidden"):
            fp.validate_override({"decision_table": {"error_code": {code: "cooldown_then_next"}}}, is_tenant=True)
    assert "stopped_by_user" in pol["hard_forbidden"]["terminal_cause"]
    assert "refusal" in pol["hard_forbidden"]["stop_reason"]
    # the caller's own quota / credentials / permissions are all in it
    for code in ("authentication_error", "authorization_error", "validation_error", "quota_exceeded", "insufficient_credits"):
        assert code in pol["hard_forbidden"]["error_code"]


def test_override_validation_refuses_what_the_file_does_not_allow():
    v = fp.validate_override
    with pytest.raises(ValueError):
        v({"boundary": {"rule": "allow"}}, is_tenant=True)
    with pytest.raises(ValueError):
        v({"hard_forbidden": {}}, is_tenant=True)
    with pytest.raises(ValueError, match="tenant-level"):
        v({"allowed_providers": ["sranai"]}, is_tenant=False)
    with pytest.raises(ValueError):
        v({"decision_table": {"terminal_cause": {"stopped_by_user": "next_in_chain"}}}, is_tenant=True)
    with pytest.raises(ValueError, match="unknown action"):
        v({"decision_table": {"error_code": {"provider_timeout": "retry_forever"}}}, is_tenant=True)
    with pytest.raises(ValueError):
        v({"chains": {"curated": {"gpt-4o": [{"provider": "x"}]}}}, is_tenant=True)
    with pytest.raises(ValueError):
        v({"chains": {"secret": {}}}, is_tenant=True)
    with pytest.raises(ValueError):
        v({"limits": {"stream": {"total_budget_s": "soon"}}}, is_tenant=True)
    with pytest.raises(ValueError):
        v({"breaker": {"enabled": "yes"}}, is_tenant=True)
    with pytest.raises(ValueError):
        v("not an object", is_tenant=True)
    # a legal one passes through untouched
    legal = {
        "chains": {"curated": {"qwen3.8-27b-fp8": [{"provider": "sranai", "model": "qwen3.6-35b-a3b"}]}},
        "limits": {"stream": {"total_budget_s": 25.0}},
        "breaker": {"enabled": True, "allowed_fails": 3},
        "cost_cap": {"enabled": True, "max_cost_ratio_vs_requested": 1.0},
        "opt_in": {"context_window_fallback": True},
        "allowed_providers": ["sranai"],
        "decision_table": {"error_code": {"provider_timeout": "next_in_chain"}},
    }
    assert v(legal, is_tenant=True) is legal


def test_apply_overrides_layers_deployment_then_tenant():
    base = fp.base_policy()
    dep = {"chains": {"curated": {"qwen3.8-27b-fp8": [{"provider": "sranai", "model": "qwen3.6-35b-a3b"}]}}, "limits": {"stream": {"total_budget_s": 25.0}}}
    ten = {"allowed_providers": ["sranai"], "breaker": {"enabled": True}}
    eff = fp.apply_overrides(base, dep, ten)
    assert fp.chain_for("qwen3.8-27b-fp8", "sranai", policy=eff) == [{"provider": "sranai", "model": "qwen3.6-35b-a3b"}]
    assert eff["limits"]["stream"]["total_budget_s"] == 25.0
    assert eff["limits"]["stream"]["max_candidates"] == 2  # untouched sibling
    assert eff["breaker"]["enabled"] is True and eff["breaker"]["allowed_fails"] == 3
    assert eff["allowed_providers"] == ["sranai"]
    # the base is not mutated, and the existing chains survive
    assert "allowed_providers" not in base
    assert fp.chain_for("qwen3.8-27b-fp8", "sranai") == []
    assert fp.chain_for("gpt-4o", "openai", policy=eff) == fp.chain_for("gpt-4o", "openai")
    # an override chain for a model the legacy tier table owns still wins
    # (without an override the tier rule stands, as before)
    eff3 = fp.apply_overrides(base, {"chains": {"curated": {"gpt-4o": [{"provider": "google", "model": "gemini-1.5-pro"}]}}})
    assert fp.chain_for("gpt-4o", "openai", policy=eff3) == [{"provider": "google", "model": "gemini-1.5-pro"}]
    assert fp.chain_for("gpt-4o", "openai")[0] == {"provider": "anthropic", "model": "claude-opus-4"}
    # a decision override for a non-forbidden code takes effect
    eff2 = fp.apply_overrides(base, {"decision_table": {"error_code": {"provider_model_not_found": "give_up"}}})
    assert fp.decide(error_code="provider_model_not_found", policy=eff2).action == "give_up"
    assert fp.decide(error_code="provider_model_not_found").action == "cooldown_then_next"


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------

def test_boundary_denies_outside_the_allowed_set_and_intersects():
    assert fp.allowed_set(None, None) is None
    assert fp.allowed_set({}, None) is None
    assert fp.allowed_set(None, ["openai", "sranai"]) == frozenset({"openai", "sranai"})
    assert fp.allowed_set({"allowed_providers": ["sranai"]}, None) == frozenset({"sranai"})
    assert fp.allowed_set({"allowed_providers": ["sranai"]}, ["openai", "sranai"]) == frozenset({"sranai"})
    assert fp.allowed_set({"allowed_providers": ["sranai"]}, ["openai"]) == frozenset()
    assert fp.outside_boundary("openrouter", frozenset({"sranai"})) is True
    assert fp.outside_boundary("sranai", frozenset({"sranai"})) is False
    assert fp.outside_boundary("openrouter", None) is False
    assert fp.base_policy()["boundary"]["overridable"] is False


# ---------------------------------------------------------------------------
# Kill switch + override reads (settings table stubbed)
# ---------------------------------------------------------------------------

@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


def test_kill_switch_absent_is_on_false_is_off_and_the_cache_clears(settings):
    assert fp.is_enabled() is True
    settings["fallback_enabled"] = "false"
    assert fp.is_enabled() is True  # cached
    fp.clear_cache()
    assert fp.is_enabled() is False
    settings["fallback_enabled"] = "true"
    fp.clear_cache()
    assert fp.is_enabled() is True


def test_kill_switch_read_error_means_as_before(monkeypatch):
    def boom(key):
        raise RuntimeError("no database")

    monkeypatch.setattr(fp, "_read_setting", boom)
    fp.clear_cache()
    assert fp.is_enabled() is True
    fp.clear_cache()


def test_override_rows_are_read_validated_and_a_bad_row_is_ignored(settings):
    settings["fallback_policy"] = json.dumps({"limits": {"stream": {"total_budget_s": 25.0}}})
    settings["fallback_policy:tenant:t1"] = json.dumps({"allowed_providers": ["sranai"]})
    settings["fallback_policy:tenant:t2"] = json.dumps({"boundary": {"rule": "allow"}})  # illegal
    settings["fallback_policy:tenant:t3"] = "{not json"
    eff1 = fp.effective_policy("t1")
    assert eff1["limits"]["stream"]["total_budget_s"] == 25.0 and eff1["allowed_providers"] == ["sranai"]
    eff2 = fp.effective_policy("t2")
    assert "allowed_providers" not in eff2 and eff2["limits"]["stream"]["total_budget_s"] == 25.0
    eff3 = fp.effective_policy("t3")
    assert "allowed_providers" not in eff3
    none = fp.effective_policy(None)
    assert none["limits"]["stream"]["total_budget_s"] == 25.0 and "allowed_providers" not in none


# ---------------------------------------------------------------------------
# Breaker + cost cap: off = as before, on = counts
# ---------------------------------------------------------------------------

class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, ttl):
        self.expiries[key] = ttl


@pytest.mark.asyncio
async def test_breaker_off_marks_at_once_and_never_touches_redis(monkeypatch):
    def boom():
        raise AssertionError("redis must not be touched when the breaker is off")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)
    assert await fp.breaker_allows_marking("openai", "gpt-4o") == (True, 60)


@pytest.mark.asyncio
async def test_breaker_on_marks_only_after_allowed_fails_in_the_window(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.services._redis_pool.get_async_redis", lambda: fake)
    pol = fp.apply_overrides(fp.base_policy(), {"breaker": {"enabled": True, "allowed_fails": 3, "window_s": 45, "cooldown_s": 90}})
    assert await fp.breaker_allows_marking("openai", "gpt-4o", pol) == (False, 90)
    assert await fp.breaker_allows_marking("openai", "gpt-4o", pol) == (False, 90)
    assert await fp.breaker_allows_marking("openai", "gpt-4o", pol) == (True, 90)
    assert fake.expiries["provider:fails:openai:gpt-4o"] == 45
    # another pair counts on its own
    assert await fp.breaker_allows_marking("openai", "gpt-4o-mini", pol) == (False, 90)


@pytest.mark.asyncio
async def test_breaker_on_but_redis_down_falls_back_to_marking(monkeypatch):
    def boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)
    pol = fp.apply_overrides(fp.base_policy(), {"breaker": {"enabled": True}})
    assert await fp.breaker_allows_marking("openai", "gpt-4o", pol) == (True, 60)


def test_cost_cap_off_blocks_nothing_and_on_blocks_the_dearer_hop():
    from app.services.providers.model_registry import get_all_candidates

    cands = sorted(get_all_candidates(), key=lambda c: c.cost_per_input_token)
    cheap, dear = cands[0], cands[-1]
    assert dear.cost_per_input_token > cheap.cost_per_input_token
    assert fp.cost_blocks_candidate(fp.base_policy(), cheap.model, dear.model) is None
    on = fp.apply_overrides(fp.base_policy(), {"cost_cap": {"enabled": True, "max_cost_ratio_vs_requested": 1.0}})
    assert fp.cost_blocks_candidate(on, cheap.model, dear.model) is not None
    assert fp.cost_blocks_candidate(on, dear.model, cheap.model) is None
    assert fp.cost_blocks_candidate(on, cheap.model, "no-such-model") is None
    # enabled with no ratio = nothing to compare
    on_noratio = fp.apply_overrides(fp.base_policy(), {"cost_cap": {"enabled": True}})
    assert fp.cost_blocks_candidate(on_noratio, cheap.model, dear.model) is None


def test_tenant_daily_cap_off_by_default_and_on_reads_spend():
    assert fp.cost_blocks_tenant(fp.base_policy(), "t1", lambda t: 999.0) is None
    on = fp.apply_overrides(fp.base_policy(), {"cost_cap": {"enabled": True, "tenant_daily_usd": 5.0}})
    assert fp.cost_blocks_tenant(on, "t1", lambda t: 4.99) is None
    assert fp.cost_blocks_tenant(on, "t1", lambda t: 5.0) is not None
    assert fp.cost_blocks_tenant(on, None, lambda t: 50.0) is None

    def boom(t):
        raise RuntimeError("db")

    assert fp.cost_blocks_tenant(on, "t1", boom) is None  # fail open


# ---------------------------------------------------------------------------
# Coverage — "no chain" is a number, not silence
# ---------------------------------------------------------------------------

def test_coverage_counts_and_labels_sources():
    from app.domain.routing import ModelCandidate

    cands = [
        ModelCandidate(provider="openai", model="gpt-4o"),                      # tier chain
        ModelCandidate(provider="openrouter", model="anthropic/claude-sonnet-4.5"),  # curated
        ModelCandidate(provider="sranai", model="qwen3.8-27b-fp8"),             # none
        ModelCandidate(provider="sranai", model="qwen3.6-35b-a3b"),             # none
    ]
    rep = fp.coverage(cands)
    assert rep["summary"] == {"total": 4, "with_chain": 2, "without_chain": 2}
    by = {r["model"]: r for r in rep["models"]}
    assert by["gpt-4o"]["source"] == "tier" and by["gpt-4o"]["has_chain"]
    assert by["anthropic/claude-sonnet-4.5"]["source"] == "curated"
    assert by["qwen3.8-27b-fp8"] == {"provider": "sranai", "model": "qwen3.8-27b-fp8", "has_chain": False, "chain": [], "source": "none"}
    # the no-chain rows come first
    assert [r["has_chain"] for r in rep["models"]] == [False, False, True, True]
    eff = fp.apply_overrides(fp.base_policy(), {"chains": {"curated": {"qwen3.8-27b-fp8": [{"provider": "sranai", "model": "qwen3.6-35b-a3b"}]}}})
    rep2 = fp.coverage(cands, policy=eff)
    assert rep2["summary"]["with_chain"] == 3
    assert {r["model"]: r["source"] for r in rep2["models"]}["qwen3.8-27b-fp8"] == "override"
