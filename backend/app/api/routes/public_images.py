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
from app.core.exceptions import DLPViolationError
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

    # --- Inbound DLP on the prompt -------------------------------------
    span_map: dict[str, str] = {}
    masked_prompt = payload.prompt
    dlp_action = "allow"
    dlp_findings = 0
    if settings.dlp_enabled:
        profile = await get_runtime_profile_async(
            session, settings.dlp_default_profile
        )
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

        if enforcement.requires_transformation:
            masked_prompt, span_map, _ = orch.transformer.mask_text(
                payload.prompt, enforcement.match_actions
            )
            dlp_action = "mask"

    # --- Call the provider with the masked prompt ----------------------
    result = await generate_image(
        session,
        model=model,
        prompt=masked_prompt,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        style=payload.style,
        response_format=payload.response_format,
    )

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
    logger.info(
        "image_generation",
        request_id=request_id, model=model, cost_usd=result.cost_usd,
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
