"""P10-04: Webhook delivery hardening tests."""

import httpx
import pytest
import respx

from app.services.webhook_service import DeliveryStatus, WebhookService


class TestWebhookDelivery:
    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        respx.post("https://hook.example.com/test").mock(
            return_value=httpx.Response(200)
        )
        svc = WebhookService(max_retries=1)
        delivery = await svc.deliver("https://hook.example.com/test", {"event": "test"})
        assert delivery.status == DeliveryStatus.DELIVERED
        assert delivery.attempts == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_retry_then_succeed(self):
        call_count = 0
        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(503)
            return httpx.Response(200)

        respx.post("https://hook.example.com/retry").mock(side_effect=side_effect)
        svc = WebhookService(max_retries=3)
        delivery = await svc.deliver("https://hook.example.com/retry", {"x": 1})
        assert delivery.status == DeliveryStatus.DELIVERED
        assert delivery.attempts == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        respx.post("https://hook.example.com/fail").mock(
            return_value=httpx.Response(500)
        )
        svc = WebhookService(max_retries=2)
        delivery = await svc.deliver("https://hook.example.com/fail", {"x": 1})
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.attempts == 2
        assert delivery.last_error is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_delivery_tracked(self):
        respx.post("https://hook.example.com/track").mock(
            return_value=httpx.Response(200)
        )
        svc = WebhookService(max_retries=1)
        await svc.deliver("https://hook.example.com/track", {"a": 1})
        assert len(svc.recent_deliveries) == 1
