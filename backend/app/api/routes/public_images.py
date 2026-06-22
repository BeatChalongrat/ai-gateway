"""Image generation endpoint — POST /v1/images/generations.

Flow: auth -> inbound DLP on the prompt (block or mask) -> call the OpenAI
images client -> DLP-restore the revised_prompt -> log per-image cost.

Reuses the orchestrator's tested DLP helpers (scan -> classify+evaluate ->
mask) so image prompts get the same masking semantics as chat. NER is OFF on
the prompt scan: image prompts legitimately name their subjects, and the
regex detectors already cover the real egress risk (emails, phones, national
IDs, credentials, keys).
"""

import time
import uuid

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.core.exceptions import DLPViolationError, GatewayError, RateLimitExceededError
from app.domain.observability import RequestRecord, RequestStatus
from app.schemas.common import ErrorResponse
from app.schemas.public import (
    ImageData,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageUsage,
)
from app.services.dlp.transformer import redact_orphan_tokens
from app.services.orchestrator import GatewayOrchestrator
from app.services.policy.profile_service import get_runtime_profile_async
from app.services.providers.openai_images_client import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_MODELS,
    generate_image,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

_IMAGE_USER_WINDOW_SECONDS = 3600   # per-end-user hourly bucket
_IMAGE_TENANT_WINDOW_SECONDS = 86400  # per-tenant daily bucket


async def _enforce_image_rate_limits(
    orch: GatewayOrchestrator, auth: ResolvedApiKey
) -> None:
    """Throttle image generation — it has direct $ exposure per call.

    Two independent Redis sliding-window scopes (reusing the request
    limiter's backend, never the Rust sidecar):

      * per END-USER, ``IMAGE_RATE_LIMIT_USER_PER_HOUR`` / hour. Keyed on
        ``auth.rate_limit_key_id`` (the SAME trusted per-user key the chat
        limiter uses), so a SHARED web-chat key splits per user — and an
        external client SPOOFING ``X-User-ID`` (no internal secret) falls
        back to the shared ``key_id`` and so cannot mint fresh quota.
      * per TENANT, ``IMAGE_RATE_LIMIT_TENANT_PER_DAY`` / day — the
        aggregate cost backstop.

    Fail-open on a Redis outage (inherited from the limiter) — an image
    request is never blocked by an infra blip, only by real over-use.
    """
    user_key = str(getattr(auth, "rate_limit_key_id", None) or auth.key_id)

    user_rl = await orch.rate_limit_service.check_window_async(
        f"rl:img:user:{user_key}",
        settings.image_rate_limit_user_per_hour,
        _IMAGE_USER_WINDOW_SECONDS,
    )
    if not user_rl["allowed"]:
        retry_after = max(1, user_rl["reset_at"] - int(time.time()))
        raise RateLimitExceededError(
            "Image generation limit reached "
            f"({settings.image_rate_limit_user_per_hour}/hour per user).",
            details={
                "scope": "image_user",
                "limit": user_rl["limit"],
                "remaining": user_rl["remaining"],
                "reset_at": user_rl["reset_at"],
            },
            headers={"Retry-After": str(retry_after)},
        )

    tenant_rl = await orch.rate_limit_service.check_window_async(
        f"rl:img:tenant:{auth.tenant_id}",
        settings.image_rate_limit_tenant_per_day,
        _IMAGE_TENANT_WINDOW_SECONDS,
    )
    if not tenant_rl["allowed"]:
        retry_after = max(1, tenant_rl["reset_at"] - int(time.time()))
        raise RateLimitExceededError(
            "Image generation limit reached "
            f"({settings.image_rate_limit_tenant_per_day}/day per tenant).",
            details={
                "scope": "image_tenant",
                "limit": tenant_rl["limit"],
                "remaining": tenant_rl["remaining"],
                "reset_at": tenant_rl["reset_at"],
            },
            headers={"Retry-After": str(retry_after)},
        )


async def _persist(
    orch: GatewayOrchestrator,
    session: AsyncSession,
    *,
    request_id: str,
    auth: ResolvedApiKey,
    model: str,
    status: RequestStatus,
    latency_ms: int,
    cost_usd: float,
    policy_action: str,
    dlp_findings: int,
    input_masked: str,
) -> None:
    """Best-effort request_log row (per-image cost; no tokens)."""
    try:
        await orch.request_log_service.persist_async(
            session,
            RequestRecord(
                request_id=request_id,
                tenant_id=auth.tenant_id,
                project_id=auth.project_id,
                provider="openai",
                model=model,
                status=status,
                policy_action=policy_action,
                latency_ms=latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=cost_usd,
                input_masked=input_masked,
                dlp_input_findings=dlp_findings,
                dlp_input_action=policy_action,
            ),
        )
        # Commit BEFORE any raise — the async-db dependency rolls back on a
        # propagating exception, which would otherwise discard a BLOCKED row.
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — logging must never mask the result
        logger.warning("image_log_persist_failed", error=str(exc))


@router.post(
    "/images/generations",
    response_model=ImageGenerationResponse,
    response_model_exclude_none=True,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        403: {"model": ErrorResponse, "description": "DLP policy blocked the prompt"},
        422: {"model": ErrorResponse, "description": "Invalid request parameters"},
        502: {"model": ErrorResponse, "description": "Provider returned an error"},
        503: {"model": ErrorResponse, "description": "Provider unavailable"},
    },
)
async def generate_images(
    payload: ImageGenerationRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> ImageGenerationResponse:
    t0 = time.perf_counter()
    request_id = str(uuid.uuid4())
    model = payload.model if payload.model in IMAGE_MODELS else DEFAULT_IMAGE_MODEL
    orch = GatewayOrchestrator()

    # --- Cost-control rate limit (before any provider / DLP work) ------
    await _enforce_image_rate_limits(orch, auth)

    # --- Portal credit gate — images have direct $ exposure per call ---
    from app.services.gateway.portal_credit_check import (
        check_portal_credit_async,
        deduct_portal_credit_async,
    )

    await check_portal_credit_async(
        session,
        str(auth.key_id),
        portal_user_id=getattr(auth, "portal_user_id", None),
    )

    # --- Inbound DLP on the prompt -------------------------------------
    span_map: dict[str, str] = {}
    masked_prompt = payload.prompt
    dlp_action = "allow"
    dlp_findings = 0
    if settings.dlp_enabled:
        # Media-generation DLP profile (Sprint 2A): identity-class PII
        # PASSES through (no quality-degrading masking of the user's own
        # subject), while credentials/secrets/financial hard-BLOCK. Swapped
        # from the DEFAULT profile, which masked identity.
        profile = await get_runtime_profile_async(session, "media_gen")
        detection = await orch._scan_text_as_result_async(
            payload.prompt, direction="input", ner_enabled=False
        )
        enforcement = orch._scan_and_evaluate(detection, profile)
        dlp_findings = len(enforcement.match_actions)

        if enforcement.is_blocked:
            await _persist(
                orch, session, request_id=request_id, auth=auth, model=model,
                status=RequestStatus.BLOCKED,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                cost_usd=0.0, policy_action="block", dlp_findings=dlp_findings,
                input_masked="[BLOCKED_BY_DLP]",
            )
            raise DLPViolationError(
                "Image prompt blocked by DLP policy.",
                details={
                    "direction": "input",
                    "findings_count": dlp_findings,
                    "categories": sorted(
                        {ma.match.category.upper() for ma in enforcement.match_actions}
                    ),
                },
            )

        # Sprint 4 — content-policy detectors (CSAM / copyright / public figure)
        # apply to image gen via the shared MEDIA_GEN gate (same as video/audio).
        from app.services.media_gen.submit_helpers import content_policy_blocked

        if content_policy_blocked(payload.prompt, profile):
            await _persist(
                orch, session, request_id=request_id, auth=auth, model=model,
                status=RequestStatus.BLOCKED,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                cost_usd=0.0, policy_action="block", dlp_findings=dlp_findings,
                input_masked="[BLOCKED_BY_CONTENT_POLICY]",
            )
            raise DLPViolationError(
                "Image prompt blocked by content policy.",
                details={"direction": "input", "categories": ["CONTENT_POLICY"]},
            )

        if enforcement.requires_transformation:
            masked_prompt, span_map, _ = orch.transformer.mask_text(
                payload.prompt, enforcement.match_actions
            )
            dlp_action = "mask"

    # --- Call the provider with the masked prompt ----------------------
    logger.info(
        "backend_image_generation_started",
        request_id=request_id,
        tenant_id=str(auth.tenant_id),
        user_id=str(getattr(auth, "user_id", "")) or None,
        provider="openai",
        model=model,
        endpoint="/v1/images/generations",
        timeout_ms=int(settings.image_generation_timeout_seconds * 1000),
        openai_timeout_ms=int(settings.openai_image_timeout_seconds * 1000),
    )
    try:
        result = await generate_image(
            session,
            model=model,
            prompt=masked_prompt,
            request_id=request_id,
            n=payload.n,
            size=payload.size,
            quality=payload.quality,
            style=payload.style,
            response_format=payload.response_format,
        )
    except GatewayError as exc:
        details = getattr(exc, "details", {}) or {}
        body = details.get("body") if isinstance(details, dict) else None
        err_body = body.get("error") if isinstance(body, dict) else None
        logger.warning(
            "image_generation_provider_error",
            request_id=request_id,
            tenant_id=str(auth.tenant_id),
            user_id=str(getattr(auth, "user_id", "")) or None,
            model=model,
            provider="openai",
            resolved_endpoint="/v1/images/generations",
            status_code=details.get("status") if isinstance(details, dict) else None,
            openai_error_type=err_body.get("type") if isinstance(err_body, dict) else None,
            openai_error_code=err_body.get("code") if isinstance(err_body, dict) else None,
            openai_error_message=err_body.get("message") if isinstance(err_body, dict) else str(exc),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            retry_count=settings.provider_max_retries,
            fallback_result="none",
        )
        raise

    # --- Restore masked tokens in the revised_prompt -------------------
    out_data: list[ImageData] = []
    for item in result.data:
        revised = item.get("revised_prompt")
        if revised and span_map:
            revised = orch.transformer.restore(revised, span_map)
            revised = redact_orphan_tokens(revised, span_map)
        out_data.append(
            ImageData(
                url=item.get("url"),
                b64_json=item.get("b64_json"),
                revised_prompt=revised,
            )
        )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    await _persist(
        orch, session, request_id=request_id, auth=auth, model=model,
        status=RequestStatus.SUCCESS, latency_ms=latency_ms,
        cost_usd=result.cost_usd, policy_action=dlp_action,
        dlp_findings=dlp_findings, input_masked=masked_prompt,
    )

    # Portal credit deduction — per-image COST (images report no
    # tokens), × tenant markup × USAGE_CREDITS_PER_USD, min 1 credit.
    # Best-effort: the image is already generated, a ledger hiccup must
    # not fail the response.
    try:
        await deduct_portal_credit_async(
            session,
            str(auth.key_id),
            request_id,
            cost_usd=float(result.cost_usd or 0.0),
            tokens_used=0,
            tenant_id=str(auth.tenant_id),
            portal_user_id=getattr(auth, "portal_user_id", None),
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_credit_deduct_failed", error=str(exc))
    logger.info(
        "image_generation",
        request_id=request_id, model=model, cost_usd=result.cost_usd,
        provider="openai", endpoint="/v1/images/generations",
        dlp_action=dlp_action, findings=dlp_findings,
        latency_ms=latency_ms, count=len(out_data),
    )

    return ImageGenerationResponse(
        created=result.created,
        data=out_data,
        model=result.model,
        provider="openai",
        usage=ImageUsage(cost_usd=result.cost_usd),
    )
