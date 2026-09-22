"""FIX128 — an account-scoped failure takes out ONE key's endpoint, not the provider.

Found on the test stack (docs/testing/test-stack.md): Auto picked a model whose
``sranai`` key pointed at a dead endpoint; the refused connection was
account-scoped, so qwen — another ``sranai`` key at another endpoint, working —
was skipped for the rest of the turn ("account_failed_this_request") and the
provider-wide marker kept it out of the NEXT Auto turn too. The turn ended
``all_models_failed``. Since FIX85 the endpoint belongs to the key; the account
is provider + the endpoint that serves the model.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.domain.routing import ModelCandidate
from app.services.gateway import fallback_orchestrator as fo

DEAD_EP = "http://127.0.0.1:1/v1"
LIVE_EP = "https://gateway.9arm.co/v1"

CATALOGUE = {
    "ts-dead-model": ModelCandidate(provider="sranai", model="ts-dead-model", endpoint=DEAD_EP),
    "ts-dead-sibling": ModelCandidate(provider="sranai", model="ts-dead-sibling", endpoint=DEAD_EP),
    "qwen3.8-27b-fp8": ModelCandidate(provider="sranai", model="qwen3.8-27b-fp8", endpoint=LIVE_EP),
    "gpt-4o": ModelCandidate(provider="openai", model="gpt-4o", endpoint=""),
}


@pytest.fixture(autouse=True)
def _catalogue(monkeypatch):
    import app.services.providers.model_registry as reg

    monkeypatch.setattr(reg, "get_candidate", lambda m: CATALOGUE.get(m))
    from tests import _redis_isolation

    _redis_isolation.clear_health_keys()
    yield
    _redis_isolation.clear_health_keys()


def test_account_is_the_endpoint_when_recorded_and_the_provider_otherwise():
    assert fo.account_of("sranai", "ts-dead-model") == f"sranai@{DEAD_EP}"
    assert fo.account_of("sranai", "qwen3.8-27b-fp8") == f"sranai@{LIVE_EP}"
    assert fo.account_of("openai", "gpt-4o") == "openai"          # unrecorded → provider-wide, as before
    assert fo.account_of("sranai", "unknown-model") == "sranai"
    assert fo.account_of("openai", "qwen3.8-27b-fp8") == "openai"  # provider mismatch → no borrowing


def test_a_dead_endpoint_does_not_rule_out_a_sibling_on_another_endpoint():
    skip_providers: set[str] = set()
    skip_pairs: set[tuple[str, str]] = set()
    scope = fo.rule_out_after_failure(
        ProviderUnavailableError("connection refused"), "sranai", "ts-dead-model",
        skip_providers=skip_providers, skip_pairs=skip_pairs,
    )
    assert scope == "account"
    assert "sranai" not in skip_providers, "the whole provider was ruled out by one key's endpoint"
    assert f"sranai@{DEAD_EP}" in skip_providers
    # the streaming walk's test, as written in the orchestrator
    ruled_out = lambda p, m: p in skip_providers or fo.account_of(p, m) in skip_providers  # noqa: E731
    assert not ruled_out("sranai", "qwen3.8-27b-fp8")
    assert ruled_out("sranai", "ts-dead-sibling")


def test_the_next_turn_skips_the_dead_endpoint_only():
    async def go():
        await fo.mark_provider_unhealthy("sranai", "ts-dead-model", error_code="provider_unavailable")
        marked = await fo.unhealthy_pairs([
            ("sranai", "ts-dead-model"), ("sranai", "ts-dead-sibling"), ("sranai", "qwen3.8-27b-fp8"),
        ])
        provider_wide = await fo.is_provider_unhealthy("sranai")
        qwen = await fo.is_provider_unhealthy("sranai", "qwen3.8-27b-fp8")
        return marked, provider_wide, qwen

    marked, provider_wide, qwen = asyncio.run(go())
    assert ("sranai", "ts-dead-model") in marked
    assert ("sranai", "ts-dead-sibling") in marked, "a model on the same dead endpoint stayed in the pool"
    assert ("sranai", "qwen3.8-27b-fp8") not in marked
    assert provider_wide is False, "the provider-wide marker still blocks every sranai model"
    assert qwen is False


def test_an_unrecorded_provider_keeps_the_provider_wide_marker():
    async def go():
        await fo.mark_provider_unhealthy("openai", "gpt-4o", error_code="provider_unavailable")
        return await fo.is_provider_unhealthy("openai")

    assert asyncio.run(go()) is True
