"""P5-03: Rule-based routing engine tests.

Verifies:
- Explicit model resolves to correct provider
- Unknown model falls back to openai
- Auto routing by use_case
- Legacy bridge works
- Routing always returns concrete model (never "auto")
"""

import pytest

from app.domain.routing import RoutingRequest, RoutingResult
from app.services.routing_service import RoutingService


@pytest.fixture()
def svc():
    return RoutingService()


class TestExplicitModelRouting:
    def test_openai_model(self, svc):
        result = svc.route(RoutingRequest(requested_model="gpt-4o"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_anthropic_model(self, svc):
        result = svc.route(RoutingRequest(requested_model="claude-3-5-sonnet-20241022"))
        assert result.provider == "anthropic"

    def test_openrouter_prefix(self, svc):
        result = svc.route(RoutingRequest(requested_model="meta-llama/llama-3-70b"))
        assert result.provider == "openrouter"

    def test_unknown_model_fallback(self, svc):
        result = svc.route(RoutingRequest(requested_model="unknown-model-xyz"))
        assert result.provider == "openai"
        assert "Unknown" in result.reasons[0] or "falling back" in result.reasons[0]

    def test_explicit_model_never_returns_auto(self, svc):
        result = svc.route(RoutingRequest(requested_model="gpt-4o-mini"))
        assert result.model != "auto"


class TestAutoRouting:
    def test_general_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="general"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_coding_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="coding"))
        assert result.provider == "anthropic"
        assert "claude" in result.model

    def test_reasoning_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="reasoning"))
        assert result.provider == "anthropic"

    def test_unknown_use_case_defaults_to_general(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="poetry"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_auto_never_returns_auto_model(self, svc):
        for use_case in ("general", "coding", "reasoning", "unknown"):
            result = svc.route(RoutingRequest(requested_model="auto", use_case=use_case))
            assert result.model != "auto"


class TestRoutingResultShape:
    def test_has_reasons(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert len(result.reasons) > 0

    def test_is_routing_result(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert isinstance(result, RoutingResult)


class TestLegacyBridge:
    def test_select_route_works(self, svc):
        from app.domain.entities import RequestContext, RouteDecision
        from app.schemas.common import Message
        from app.schemas.public import ChatCompletionsRequest

        ctx = RequestContext(tenant_id="t", project_id="p", project_name="P")
        payload = ChatCompletionsRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="hi")],
        )
        route = svc.select_route(ctx, payload, None)
        assert isinstance(route, RouteDecision)
        assert route.provider_name == "openai"
        assert route.model_name == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Fix 5 — preferred_provider as a soft preference in auto routing
# ---------------------------------------------------------------------------


class TestPreferredProviderAuto:
    def test_preferred_overrides_default_when_available(self, svc):
        # Default for use_case=general is gpt-4o-mini (openai). With
        # preferred_provider='anthropic' the auto pick must shift to a
        # candidate from anthropic.
        result = svc.route(RoutingRequest(
            requested_model="auto",
            use_case="general",
            preferred_provider="anthropic",
        ))
        assert result.provider == "anthropic"
        assert any(
            "preferred_provider='anthropic'" in r for r in result.reasons
        )

    def test_preferred_unknown_falls_back_silently(self, svc):
        # No candidate exists for an unknown provider — the soft
        # preference quietly defers to the hardcoded auto default
        # instead of raising.
        result = svc.route(RoutingRequest(
            requested_model="auto",
            use_case="general",
            preferred_provider="fictional-provider",
        ))
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_preferred_respects_allowed_providers(self, svc):
        # Preference must not break the hard allowed_providers
        # constraint — anthropic is preferred but excluded, so the
        # auto pick stays inside the allowed set.
        result = svc.route(RoutingRequest(
            requested_model="auto",
            use_case="general",
            preferred_provider="anthropic",
            allowed_providers=frozenset(["openai"]),
        ))
        assert result.provider == "openai"


# ---------------------------------------------------------------------------
# Fix 6 — model-compatible provider on explicit swap
# ---------------------------------------------------------------------------


class TestExplicitSwapCompatibility:
    def test_swap_skips_incompatible_provider(self, svc):
        # claude-3-5-sonnet's canonical owner (anthropic) is excluded
        # from allowed_providers. The only allowed provider is
        # openai, which does NOT own the model. The helper must log
        # the gap explicitly and return a clearly-explained fallback
        # rather than silently sending the request somewhere doomed.
        result = svc.route(RoutingRequest(
            requested_model="claude-3-5-sonnet-20241022",
            allowed_providers=frozenset(["openai"]),
        ))
        # Fallback is openai per the helper contract, but the reasons
        # list now explains that openai does not own this model.
        assert result.provider == "openai"
        joined = " ".join(result.reasons)
        assert "claude-3-5-sonnet" in joined
        assert "openai" in joined.lower()

    def test_swap_uses_compatible_owner_when_present(self, svc):
        # gpt-4o canonical owner is openai. Block openai but include
        # google + anthropic in the allowed set. Neither owns gpt-4o
        # → fallback to openai with WARNING (verified deterministically
        # — sorted iteration means anthropic is considered before
        # google, both rejected because neither owns gpt-4o).
        result = svc.route(RoutingRequest(
            requested_model="gpt-4o",
            allowed_providers=frozenset(["anthropic", "google"]),
        ))
        assert result.provider == "openai"
        assert any("anthropic" in r and "google" in r for r in result.reasons)

    def test_preferred_provider_wins_when_compatible(self, svc):
        # openai/gpt-4o resolves to openrouter via the openai/ prefix.
        # allowed includes both openrouter (compatible) and openai
        # (incompatible — gpt-4o-style prefix names belong to
        # openrouter). preferred_provider=openrouter must be selected.
        result = svc.route(RoutingRequest(
            requested_model="openai/gpt-4o",
            allowed_providers=frozenset(["openai", "openrouter"]),
            preferred_provider="openrouter",
        ))
        # No swap occurs because openrouter (canonical owner) IS in
        # allowed_providers — the request passes through unchanged.
        assert result.provider == "openrouter"

    def test_swap_is_deterministic_across_frozenset_orderings(self, svc):
        # Build the same logical request twice with frozensets of
        # different insertion order. The fallback target must not flap.
        req_a = RoutingRequest(
            requested_model="claude-3-5-sonnet-20241022",
            allowed_providers=frozenset({"groq", "anthropic_blocked", "deepseek"}),
        )
        req_b = RoutingRequest(
            requested_model="claude-3-5-sonnet-20241022",
            allowed_providers=frozenset({"deepseek", "anthropic_blocked", "groq"}),
        )
        # Neither set contains anthropic (canonical owner), so both
        # fall through to the openai literal — deterministically.
        assert svc.route(req_a).provider == svc.route(req_b).provider == "openai"


class TestFindCompatibleProvider:
    """Direct exercise of the helper so the contract is documented
    independently of the calling sites."""

    def test_preferred_wins_when_compatible(self, svc):
        provider, reason = svc._find_compatible_provider(
            "gemini-1.5-pro",
            frozenset({"openai", "google"}),
            preferred_provider="google",
        )
        assert provider == "google"
        assert "preferred_provider" in reason

    def test_sorted_allowed_picked_when_no_preferred(self, svc):
        provider, reason = svc._find_compatible_provider(
            "gemini-1.5-pro",
            frozenset({"openai", "google", "anthropic"}),
            preferred_provider=None,
        )
        assert provider == "google"
        assert "sorted(allowed_providers)" in reason

    def test_no_compatible_falls_back_to_openai(self, svc):
        # Claude family does not live on these providers.
        provider, reason = svc._find_compatible_provider(
            "claude-3-5-sonnet-20241022",
            frozenset({"openai", "groq"}),
            preferred_provider=None,
        )
        assert provider == "openai"
        assert "No provider in allowed set" in reason
