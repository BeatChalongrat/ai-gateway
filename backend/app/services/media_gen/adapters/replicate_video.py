"""ReplicateVideoAdapter — real video provider (Sprint 2B).

NO live calls are made in this sprint: every test mocks httpx via respx, and
Beat runs the live smoke separately. The exact Replicate "create prediction"
shape (``POST /v1/models/{owner}/{name}/predictions``) is flagged for Beat to
confirm against the live API during the smoke run.

**Token isolation (load-bearing):** the API token is read from ``settings``
only at the moment a request is built, and is placed ONLY in the
``Authorization`` header. It is never stored on the instance, never logged,
and never included in an exception message. ``settings.replicate_api_token``
itself is declared ``repr=False`` so it can't leak via ``repr(settings)``.

**Cost safety:** ``submit_job`` reserves the L1 session budget (atomic) as
the FIRST action, before any provider call, and refunds it on ANY failure.
Duration is hard-clamped server-side. Because the ABC ``submit_job(prompt,
meta)`` signature carries no ``model_config``, the route stores the
pre-computed ``estimate_usd`` and the Replicate model path (``replicate_model``)
in ``input_metadata``; ``submit_job`` refuses to call the provider without a
positive estimate to reserve against.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderError
from app.services.media_gen.base import MediaGenAdapter, PollResult, SubmitResult
from app.services.media_gen.spend_guard import (
    SpendCapExceeded,
    check_and_reserve_session_budget,
    release_session_budget,
)

log = logging.getLogger(__name__)

REPLICATE_API_BASE = "https://api.replicate.com/v1"
_MAX_OUTPUT_BYTES = 200 * 1024 * 1024  # 200 MiB — matches the chat-edge cap


class ProviderAuthError(ProviderError):
    code = "provider_auth_error"
    message = "Provider authentication failed."


class ContentPolicyBlocked(ProviderError):
    code = "content_policy_blocked"
    message = "The prompt was blocked by the provider's content policy."


def _clamp_duration(meta: dict) -> int:
    requested = int((meta or {}).get("duration_sec", 5) or 5)
    return max(1, min(requested, settings.replicate_max_video_duration_sec))


def _inc_api_error(error_class: str) -> None:
    try:
        from app.observability.metrics import REPLICATE_API_ERRORS_TOTAL

        REPLICATE_API_ERRORS_TOTAL.labels(error_class).inc()
    except Exception:  # noqa: BLE001
        pass


def _safe_detail(resp: httpx.Response) -> str:
    try:
        d = resp.json()
        return str(d.get("detail") or d.get("error") or "content policy violation")[:300]
    except Exception:  # noqa: BLE001
        return "content policy violation"


class ReplicateVideoAdapter(MediaGenAdapter):
    provider_name = "replicate"
    supported_job_types = ["video"]

    # No constructor args — the registry instantiates via ``cls()``. The
    # token is read at call time, never stored on the instance.
    def __init__(self) -> None:
        if not settings.replicate_api_token:
            raise ValueError("REPLICATE_API_TOKEN not configured")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.replicate_api_token}"}

    def estimate_cost_usd(self, input_metadata: dict, model_config: Any) -> Decimal:
        duration = _clamp_duration(input_metadata)
        per_second = Decimal(
            str(getattr(model_config, "cost_per_second", None) or "0.07")
        )
        return per_second * duration

    async def submit_job(
        self, input_prompt: str, input_metadata: dict
    ) -> SubmitResult:
        meta = input_metadata or {}
        estimate = Decimal(str(meta.get("estimate_usd", "0") or "0"))
        if estimate <= 0:
            # Fail safe — never call the provider without a positive estimate
            # to reserve against (an unmetered call could burn budget).
            raise SpendCapExceeded("missing or non-positive cost estimate")

        # --- L1 GUARD: first thing, before any HTTP call ----------------
        reserved = await check_and_reserve_session_budget(estimate)
        if not reserved:
            raise SpendCapExceeded(f"session budget exceeded (estimate ${estimate})")

        try:
            model_path = str(meta.get("replicate_model") or "").strip()
            if not model_path:
                raise ProviderError("missing replicate model path in job metadata")
            duration = _clamp_duration(meta)
            payload: dict = {"input": {"prompt": input_prompt, "duration": duration}}

            wh = (settings.replicate_webhook_base_url or "").strip()
            if settings.replicate_webhook_secret and wh:
                payload["webhook"] = (
                    f"{wh.rstrip('/')}/webhooks/replicate"
                    f"?secret={settings.replicate_webhook_secret}"
                )
                payload["webhook_events_filter"] = ["start", "completed"]

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{REPLICATE_API_BASE}/models/{model_path}/predictions",
                    json=payload,
                    headers=self._headers(),
                )
            if resp.status_code == 401:
                _inc_api_error("auth")
                raise ProviderAuthError("Replicate authentication failed")
            if resp.status_code == 422:
                _inc_api_error("content_policy")
                raise ContentPolicyBlocked(_safe_detail(resp))
            resp.raise_for_status()
            data = resp.json()
            return SubmitResult(
                external_job_id=str(data["id"]),
                initial_status="processing",
                sync_result=None,
            )
        except Exception:
            # ALWAYS refund the reservation on ANY failure after reserving.
            await release_session_budget(estimate)
            raise

    async def poll_job(self, external_job_id: str) -> PollResult:
        async with httpx.AsyncClient(
            timeout=float(settings.replicate_request_timeout_sec)
        ) as client:
            resp = await client.get(
                f"{REPLICATE_API_BASE}/predictions/{external_job_id}",
                headers=self._headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status in ("starting", "processing"):
            return PollResult(status="processing", progress_pct=None)
        if status == "succeeded":
            output = data.get("output")
            if isinstance(output, list):
                url = output[0] if output else None
            else:
                url = output if isinstance(output, str) else None
            return PollResult(
                status="done",
                progress_pct=100,
                result_url=url,
                result_metadata={"replicate_metrics": data.get("metrics", {})},
            )
        # failed / canceled
        return PollResult(
            status="failed",
            error_code=str(status or "failed"),
            error_message=(str(data.get("error") or "")[:500] or "provider failure"),
        )

    async def cancel_job(self, external_job_id: str) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{REPLICATE_API_BASE}/predictions/{external_job_id}/cancel",
                headers=self._headers(),
            )
        return resp.status_code == 200

    async def fetch_result_bytes(self, result_url: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=float(settings.replicate_request_timeout_sec)
        ) as client:
            head = await client.head(result_url)
            clen = int(head.headers.get("content-length", "0") or "0")
            if clen > _MAX_OUTPUT_BYTES:
                raise ValueError(f"output too large: {clen} bytes")
            resp = await client.get(result_url)
            resp.raise_for_status()
            data = resp.content
        if len(data) > _MAX_OUTPUT_BYTES:
            raise ValueError(f"output too large: {len(data)} bytes")
        return data
