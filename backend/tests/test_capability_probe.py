"""FIX47.6 Part B — capability-probe outcome classification.

The classifier is what keeps the mismatch table honest: an account out
of credits or a throttle must never read as "the model is dead" (the
FIX42 trap, measured live again this round when the whole openai
catalogue "died" of an empty billing balance), and only a provider's
own rejection counts as a capability verdict.
"""

from app.core.exceptions import GatewayError, ProviderError, RateLimitExceededError
from app.services.capability_probe import _allow_capability, _classify_error


def test_insufficient_quota_classifies_as_no_credit():
    exc = RateLimitExceededError(
        "Provider 'openai' rate limit exceeded.",
        details={
            "provider": "openai",
            "status": 429,
            "body": {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}},
        },
    )
    outcome, detail = _classify_error(exc)
    assert outcome == "no_credit"
    assert "insufficient_quota" in detail


def test_plain_429_classifies_as_rate_limited():
    exc = RateLimitExceededError(
        "Provider 'openai' rate limit exceeded.",
        details={"provider": "openai", "status": 429, "body": {}},
    )
    assert _classify_error(exc)[0] == "rate_limited"


def test_provider_4xx_classifies_as_rejected():
    exc = ProviderError(
        "Provider 'openrouter' has no endpoint for the requested model (404).",
        details={"provider": "openrouter", "status": 404},
    )
    assert _classify_error(exc)[0] == "rejected_by_provider"


def test_unknown_exception_classifies_as_transport_error():
    assert _classify_error(ConnectionError("boom"))[0] == "transport_error"


def test_gateway_error_without_details_is_rejected_by_provider():
    assert _classify_error(GatewayError("nope"))[0] == "rejected_by_provider"


def test_allow_capability_flips_only_the_local_snapshot():
    """The probe's gate bypass mutates the process-local registry map
    and nothing else — the DB row object is never involved."""
    from app.services.providers.model_registry import _registry

    cand = next(c for c in _registry.get_all_candidates() if not c.supports_tools)
    try:
        _allow_capability(cand.model, tools=True)
        assert _registry._model_map[cand.model].supports_tools is True
        # The candidate object itself is frozen — a NEW object was swapped in.
        assert cand.supports_tools is False
    finally:
        _registry._model_map[cand.model] = cand
