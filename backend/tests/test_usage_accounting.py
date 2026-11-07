"""P6-04: Usage and cost accounting tests."""

import pytest

from app.domain.observability import UsageRecord
from app.domain.providers import ProviderResponse, ProviderUsage
from app.schemas.common import Message, UsageInfo
from app.schemas.public import ChatCompletionsRequest
from app.services.usage_service import UsageService


@pytest.fixture()
def svc():
    return UsageService()


class TestBuildUsageRecordFromProvider:
    def test_from_provider_response(self, svc):
        pr = ProviderResponse(
            content="hi", model="gpt-4o-mini",
            usage=ProviderUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        record = svc.build_usage_record(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o-mini",
            provider_response=pr,
        )
        assert isinstance(record, UsageRecord)
        assert record.is_estimate is False
        assert record.total_tokens == 15

    def test_from_usage_info_fallback(self, svc):
        ui = UsageInfo(prompt_tokens=5, completion_tokens=3, total_tokens=8)
        record = svc.build_usage_record(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o",
            usage_info=ui,
        )
        assert record.is_estimate is True
        assert record.total_tokens == 8

    def test_empty_fallback(self, svc):
        record = svc.build_usage_record(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o",
        )
        assert record.is_estimate is True
        assert record.total_tokens == 0


class TestCostEstimation:
    def test_gpt4o_mini_cost(self, svc):
        pr = ProviderResponse(
            content="hi", model="gpt-4o-mini",
            usage=ProviderUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
        )
        record = svc.build_usage_record(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o-mini",
            provider_response=pr,
        )
        assert record.estimated_cost_usd > 0
        assert record.estimated_cost_usd < 0.01  # budget model

    def test_different_models_different_costs(self, svc):
        usage = ProviderUsage(prompt_tokens=100, completion_tokens=100, total_tokens=200)
        cheap = svc.build_usage_record(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o-mini",
            provider_response=ProviderResponse(content="", model="gpt-4o-mini", usage=usage),
        )
        expensive = svc.build_usage_record(
            request_id="r2", tenant_id="t1", project_id="p1",
            provider="anthropic", model="claude-3-opus-20240229",
            provider_response=ProviderResponse(content="", model="claude-3-opus", usage=usage),
        )
        assert cheap.estimated_cost_usd < expensive.estimated_cost_usd


class TestHeuristicEstimate:
    def test_estimate_returns_usage_info(self, svc):
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="Hello world")],
        )
        result = svc.estimate(payload, "Hi there", "gpt-4o-mini")
        assert isinstance(result, UsageInfo)
        assert result.prompt_tokens > 0
        assert result.completion_tokens > 0
        assert result.reasoning_tokens is None

    def test_estimate_folds_reasoning_into_completion(self, svc):
        """Reasoning tokens on the estimate path must be billed like the
        provider-usage path bills them: inside completion_tokens. The
        old callers stamped reasoning_tokens on afterwards without
        touching completion_tokens/cost, so reasoning-heavy turns whose
        stream ended without a usage chunk under-billed by exactly
        reasoning_tokens x output rate (and could violate the documented
        reasoning_tokens <= completion_tokens invariant)."""
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="Hello world")],
        )
        plain = svc.estimate(payload, "Hi there", "gpt-4o-mini")
        with_reasoning = svc.estimate(
            payload, "Hi there", "gpt-4o-mini", reasoning_tokens=320
        )
        assert with_reasoning.reasoning_tokens == 320
        assert (
            with_reasoning.completion_tokens
            == plain.completion_tokens + 320
        )
        assert (
            with_reasoning.total_tokens == plain.total_tokens + 320
        )
        # The all-in cost prices the reasoning tokens too.
        assert with_reasoning.estimated_cost_usd > plain.estimated_cost_usd
        # Invariant from the reasoning_cost attribution helper.
        assert (
            with_reasoning.reasoning_tokens
            <= with_reasoning.completion_tokens
        )

    def test_estimate_negative_reasoning_treated_as_zero(self, svc):
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="Hello world")],
        )
        result = svc.estimate(payload, "Hi", "gpt-4o-mini", reasoning_tokens=-5)
        assert result.reasoning_tokens is None
        assert result.completion_tokens == max(1, len("Hi") // 4)
