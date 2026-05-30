"""Webhook delivery service with retry and status tracking.

Delivers alert events to configured webhook URLs with bounded retry
and exponential backoff. Delivery status is tracked for audit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# H15: bounded so a single dead webhook can't stall the alert sweep for
# ~33s (was 3 retries × 10s timeout + backoff). Worst case is now
# 5s + 1s backoff + 5s ≈ 11s, and the await yields the loop throughout.
MAX_RETRIES = 2
BASE_DELAY = 1.0
TIMEOUT = 5.0


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass
class WebhookDelivery:
    """Tracks a single webhook delivery attempt."""

    webhook_url: str
    payload: dict
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    delivered_at: datetime | None = None

    def mark_delivered(self) -> None:
        self.status = DeliveryStatus.DELIVERED
        self.delivered_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = DeliveryStatus.FAILED
        self.last_error = error


class WebhookService:
    """Deliver payloads to webhook URLs with retry."""

    def __init__(self, max_retries: int = MAX_RETRIES, timeout: float = TIMEOUT) -> None:
        self._max_retries = max_retries
        self._timeout = timeout
        self._deliveries: list[WebhookDelivery] = []

    async def deliver(self, url: str, payload: dict) -> WebhookDelivery:
        """Attempt to deliver payload to URL with retries."""
        delivery = WebhookDelivery(webhook_url=url, payload=payload)
        self._deliveries.append(delivery)

        for attempt in range(self._max_retries):
            delivery.attempts = attempt + 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    delivery.mark_delivered()
                    logger.info(
                        "webhook_delivered",
                        url=url,
                        status_code=resp.status_code,
                        attempts=delivery.attempts,
                    )
                    return delivery
            except Exception as exc:
                delivery.last_error = str(exc)
                if attempt < self._max_retries - 1:
                    delay = BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)

        delivery.mark_failed(delivery.last_error or "max retries exceeded")
        logger.warning(
            "webhook_failed",
            url=url,
            attempts=delivery.attempts,
            error=delivery.last_error,
        )
        return delivery

    @property
    def recent_deliveries(self) -> list[WebhookDelivery]:
        return list(self._deliveries[-100:])
