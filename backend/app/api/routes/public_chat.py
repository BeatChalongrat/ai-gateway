"""Public chat completions route — thin transport layer.

Non-streaming requests delegate to GatewayOrchestrator.
Streaming requests resolve context via the orchestrator, run the same
inbound DLP mask, stream from the provider adapter, then buffer +
restore the full body before emitting it back to the client.
"""

import json
import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, check_tenant_access, get_db_session, get_resolved_api_key
from app.core.config import settings
from app.core.exceptions import (
    DLPViolationError,
    ModerationViolationError,
    NotFoundError,
    ProviderUnavailableError,
    ValidationError,
)
from app.api.routes.admin_ai_review import maybe_ai_review_background
from app.domain.observability import (
    RequestRecord,
    RequestStatus,
    build_dlp_findings_json,
    extract_messages_text,
)
from app.models.request_log import RequestLogORM
from app.schemas.common import ErrorResponse
from app.schemas.common import UsageInfo
from app.schemas.public import (
    ChatCompletionsRequest,
    ChatCompletionsResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from app.services.orchestrator import GatewayOrchestrator
from app.services.policy.profiles import get_profile
from app.services.usage_service import _estimate_cost

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/chat/completions",
    response_model=ChatCompletionsResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        403: {"model": ErrorResponse, "description": "DLP policy blocked the request"},
        422: {"model": ErrorResponse, "description": "Invalid request parameters"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal error"},
        502: {"model": ErrorResponse, "description": "Provider returned an error"},
        503: {"model": ErrorResponse, "description": "Provider unavailable"},
        504: {"model": ErrorResponse, "description": "Provider timed out"},
    },
)
async def chat_completions(
    payload: ChatCompletionsRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: Session = Depends(get_db_session),
):
    orchestrator = GatewayOrchestrator()

    if not payload.stream:
        return await orchestrator.handle_chat_completion(
            auth=auth, payload=payload, session=session
        )

    # Captured here so the latency we record on the request_log row covers
    # the full request lifetime, not just the time inside event_generator.
    t_start = time.perf_counter()

    # Streaming order — mirrors handle_chat_completion:
    #   1. Resolve project context
    #   2. DLP mask input  (must run before router chain — semantic
    #      router embeddings must never see raw PII)
    #   3. Moderation
    #   4. Router chain on masked messages (may rewrite payload.model)
    #   5. select_route (now respects the router's model choice)
    #   6. supports_streaming check → fall back to non-stream if needed
    #   7. Stream → buffer → DLP restore → log with router_used
    ctx = orchestrator.resolve_streaming_context(
        auth=auth, payload=payload, session=session
    )

    log = logger.bind(
        request_id=ctx.request_id,
        tenant_id=ctx.tenant_id,
        project_id=ctx.project_id,
    )

    profile_id = ctx.policy_profile_id or settings.dlp_default_profile
    profile = get_profile(profile_id)

    # 1. Inbound DLP: classify, block if needed, mask with indexed tokens.
    # Same flow as GatewayOrchestrator.handle_chat_completion steps 2-4.
    from app.domain.dlp import EnforcementDecision

    # DLP trace snapshot 1/4: raw user input, captured before any DLP /
    # moderation work runs so blocked streams still record what was sent.
    input_raw = extract_messages_text(payload.messages)

    messages_to_send = list(payload.messages)
    span_map: dict[str, str] = {}
    # Per-detector mask index counters carried from inbound to outbound so
    # that a new PII finding in the provider response doesn't re-use an
    # index already bound to an inbound original. See transformer docstring.
    inbound_counters: dict[str, int] = {}
    input_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)

    if settings.dlp_enabled:
        # Per-tenant NER gate — identical to orchestrator.handle_chat_completion.
        # Drop PresidioDetector from this request's registry if the
        # tenant has NER off (resolved in project_service against both
        # the tenant row and the org_settings default).
        input_registry = orchestrator.detector_registry
        if not ctx.ner_enabled:
            input_registry = input_registry.without("presidio_ner")
        detection = input_registry.scan_messages(
            payload.messages, direction="input"
        )
        input_enforcement = orchestrator._scan_and_evaluate(detection, profile)

        if input_enforcement.is_blocked:
            orchestrator.audit_service.record_blocked_request(
                ctx=ctx, enforcement=input_enforcement, direction="input"
            )
            # Raising here (before returning StreamingResponse) lets the
            # central error handler turn it into a 403 JSON response —
            # the client never sees a partial SSE body.
            raise DLPViolationError(
                "Request blocked by DLP policy.",
                details={
                    "policy_profile": input_enforcement.policy_profile_id,
                    "reasons": input_enforcement.reasons,
                    "direction": "input",
                },
            )

        if input_enforcement.requires_transformation:
            messages_to_send, span_map, inbound_counters = orchestrator.transformer.mask_messages(
                payload.messages, input_enforcement.match_actions
            )

    # DLP trace snapshot 2/4: messages as they will be streamed to the
    # provider — same semantics as the non-streaming path. Equal to
    # ``input_raw`` when no PII was masked.
    input_masked = extract_messages_text(messages_to_send)

    # 1b. Moderation (OpenAI /v1/moderations) — opt-in per policy profile.
    # Mirrors GatewayOrchestrator step 4b2. Runs on the post-DLP-mask
    # messages so the moderation endpoint never sees raw PII. Fails open
    # on any error (timeout, bad key, parse error).
    if profile.moderation_enabled:
        moderation_input = " ".join(
            m.content for m in messages_to_send
            if isinstance(m.content, str)
        )
        openai_key = None
        if session is not None:
            openai_key = orchestrator.provider_key_service.get_api_key(
                session, "openai"
            )
        openai_key = openai_key or settings.openai_api_key
        if openai_key and moderation_input:
            t_mod = time.perf_counter()
            mod_result = await orchestrator.moderation_service.check(
                moderation_input, openai_key
            )
            logger.info(
                "moderation_scan_complete",
                flagged=mod_result.flagged,
                categories=list(mod_result.categories.keys()),
                error=mod_result.error,
                moderation_ms=int((time.perf_counter() - t_mod) * 1000),
                streaming=True,
            )
            if mod_result.flagged:
                if session is not None:
                    try:
                        orchestrator.request_log_service.persist(
                            session,
                            RequestRecord(
                                request_id=ctx.request_id or "",
                                tenant_id=ctx.tenant_id,
                                project_id=ctx.project_id,
                                provider="",
                                model=payload.model,
                                status=RequestStatus.BLOCKED,
                                policy_action="moderation",
                                policy_profile_id=profile.profile_id,
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                estimated_cost_usd=0.0,
                                error_code="moderation_flagged",
                                input_raw=input_raw,
                                input_masked=input_masked,
                                dlp_findings_json=build_dlp_findings_json(
                                    input_enforcement.match_actions,
                                    span_map=span_map,
                                ),
                            ),
                        )
                        # Commit now — the ModerationViolationError raised
                        # below propagates through the dependency's except
                        # branch, which would otherwise roll this row back.
                        session.commit()
                    except Exception:
                        # Logging failures must never mask the block.
                        pass
                # Raising before returning StreamingResponse lets the
                # central error handler emit a 403 JSON body — the
                # client never sees a partial SSE stream.
                raise ModerationViolationError(
                    "Content flagged by moderation.",
                    details={
                        "categories": list(mod_result.categories.keys()),
                        "scores": mod_result.scores,
                    },
                )

    # 1c. Router chain — semantic > keyword > service, on DLP-masked
    # messages. Mutates payload.model when a router fires. Any failure
    # falls back to plain "service" routing so the stream never breaks.
    dict_messages = [m.model_dump() for m in messages_to_send]
    try:
        router_used, _matched_rule = orchestrator._apply_router_chain(
            ctx=ctx,
            payload=payload,
            dict_messages=dict_messages,
            session=session,
            log=log,
        )
    except Exception as exc:
        log.warning("router_chain_failed", error=str(exc))
        router_used = "service"

    # 2. Select route (respects the router chain's model choice).
    route = orchestrator.routing_service.select_route(
        ctx=ctx, payload=payload, policy_decision=input_enforcement
    )

    # 3. Supports-streaming check — fall back to the non-streaming path
    # if the selected adapter can't stream (e.g. router picked a
    # non-streaming model).
    adapter = orchestrator.provider_dispatcher.adapters.get(route.provider_name)
    if adapter is None or not adapter.supports_streaming:
        return await orchestrator.handle_chat_completion(
            auth=auth, payload=payload, session=session
        )

    # 4. Resolve provider API key.
    provider_api_key = orchestrator.provider_key_service.get_api_key(
        session, route.provider_name
    )
    if not provider_api_key:
        raise ProviderUnavailableError(
            f"No API key configured for provider '{route.provider_name}'.",
            details={"provider": route.provider_name},
        )

    async def event_generator():
        # Phase 3.5 Commits 30-31 — incremental DLP streaming.
        # Phase 2C (C13) closed P0-7 by buffering the whole response,
        # running DLP on the full body, then emitting as a single
        # content delta. That kept the wire safe but killed real-time
        # UX. This generator restores incremental emission while
        # keeping the wire safe via a rolling ~50-char tail:
        #
        #   window = tail_masked + new raw chunk text
        #   DLP scan on the full window catches PII spanning a chunk
        #   boundary (tail carries enough context).
        #   emit everything EXCEPT the last MIN_TAIL_CHARS chars of
        #   the masked window; keep the last MIN_TAIL_CHARS as tail.
        #
        # Trade-off: patterns longer than MIN_TAIL_CHARS spanning chunk
        # boundaries in a way that their leading fragment individually
        # is NOT a match could leak. In practice, DLP detectors don't
        # match partial patterns (an email regex needs the full `@...`),
        # so emails/phones/SSNs/credit-cards are safe at N=50.
        # Documented as a known limitation.
        MIN_TAIL_CHARS = 50

        tail_masked = ""
        cumulative_raw = ""
        emitted_parts: list[str] = []
        accumulated_outbound_actions = []
        counters_mut = dict(inbound_counters)
        final_finish_reason: str | None = None
        final_model: str = route.model_name
        provider_usage = None

        def _dlp_scan_and_mask(
            text: str,
        ) -> tuple[str, list]:
            """Scan ``text`` outbound + mask in-place.

            Returns (masked_text, match_actions). On any exception the
            chunk is DROPPED and a warning logged — we fail closed
            rather than emit raw text past a broken DLP.
            """
            if not text:
                return "", []
            if not (
                settings.dlp_enabled
                and settings.dlp_scan_responses
                and profile.scan_responses
            ):
                return text, []
            try:
                detection = orchestrator._scan_text_as_result(
                    text, direction="output", ner_enabled=ctx.ner_enabled,
                )
                enforcement = orchestrator._scan_and_evaluate(detection, profile)
                if not enforcement.requires_transformation:
                    return text, enforcement.match_actions
                masked, _, new_counters = orchestrator.transformer.mask_text(
                    text,
                    enforcement.match_actions,
                    start_counters=counters_mut,
                )
                # Mutate via the enclosing variable binding.
                counters_mut.clear()
                counters_mut.update(new_counters)
                return masked, list(enforcement.match_actions)
            except Exception as exc:
                log.warning(
                    "sse_dlp_chunk_failed",
                    error=str(exc)[:200],
                    dropped_chars=len(text),
                )
                # Fail-closed: drop the chunk rather than emit raw.
                return "", []

        def _emit_chunk(body_text: str) -> str:
            """Serialise a content-delta SSE frame."""
            return "data: {}\n\n".format(
                json.dumps(
                    {
                        "id": ctx.request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": final_model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": body_text},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            )

        async for chunk in adapter.stream_chat(
            payload, messages_to_send,
            resolved_model=route.model_name,
            api_key=provider_api_key,
        ):
            if chunk.delta:
                cumulative_raw += chunk.delta
                # Combine retained masked tail + new raw chunk text.
                # The scanner won't re-match already-masked placeholders
                # (`<EMAIL_1>` is not a valid email), so keeping masked
                # text in the tail is safe and deduplication-friendly.
                window = tail_masked + chunk.delta
                masked_window, actions = _dlp_scan_and_mask(window)
                if actions:
                    accumulated_outbound_actions.extend(actions)

                if len(masked_window) > MIN_TAIL_CHARS:
                    safe = masked_window[:-MIN_TAIL_CHARS]
                    tail_masked = masked_window[-MIN_TAIL_CHARS:]
                    emit_text = (
                        orchestrator.transformer.restore(safe, span_map)
                        if span_map
                        else safe
                    )
                    if emit_text:
                        emitted_parts.append(emit_text)
                        yield _emit_chunk(emit_text)
                else:
                    tail_masked = masked_window

            if chunk.model:
                final_model = chunk.model
            if chunk.finish_reason is not None:
                final_finish_reason = chunk.finish_reason
            if chunk.usage is not None:
                provider_usage = chunk.usage

        # Flush the remaining tail. The tail already went through a
        # DLP scan in its most recent iteration; no need to re-mask.
        if tail_masked:
            final_emit = (
                orchestrator.transformer.restore(tail_masked, span_map)
                if span_map
                else tail_masked
            )
            if final_emit:
                emitted_parts.append(final_emit)
                yield _emit_chunk(final_emit)

        # Capture DLP trace + build the response-enforcement summary
        # from the per-chunk match actions we accumulated. overall_action
        # is computed from match_actions automatically.
        output_raw = cumulative_raw
        full_text = "".join(emitted_parts)
        response_enforcement = EnforcementDecision(
            match_actions=accumulated_outbound_actions,
            policy_profile_id=profile.profile_id,
        )

        # 4. Persist DLP summary + basic request metrics. Mirrors the
        # fields populated by GatewayOrchestrator.handle_chat_completion.
        # The dependency-injected `session` is closed by the time this
        # generator runs (route returned StreamingResponse → get_db_session
        # already hit its finally block), so open a fresh session here.
        from app.core.db import SessionLocal

        overall_action = max(
            input_enforcement.overall_action,
            response_enforcement.overall_action,
        )
        dlp_categories = sorted(
            {ma.match.category for ma in input_enforcement.match_actions}
            | {ma.match.category for ma in response_enforcement.match_actions}
        )

        # Resolve usage: prefer provider-reported counts (from the trailing
        # usage chunk under stream_options.include_usage); fall back to the
        # heuristic estimator when the provider didn't surface them.
        if provider_usage is not None:
            usage = UsageInfo(
                prompt_tokens=provider_usage.prompt_tokens,
                completion_tokens=provider_usage.completion_tokens,
                total_tokens=provider_usage.total_tokens,
                estimated_cost_usd=_estimate_cost(
                    route.model_name, provider_usage.total_tokens
                ),
            )
        else:
            usage = orchestrator.usage_service.estimate(
                payload=payload,
                response_text=full_text,
                model_name=route.model_name,
            )

        latency_ms = int((time.perf_counter() - t_start) * 1000)

        log_session = SessionLocal()
        try:
            orchestrator.request_log_service.persist(
                log_session,
                RequestRecord(
                    request_id=ctx.request_id or "",
                    tenant_id=ctx.tenant_id,
                    project_id=ctx.project_id,
                    provider=route.provider_name,
                    model=route.model_name,
                    status=RequestStatus.SUCCESS,
                    policy_action=overall_action.name.lower(),
                    policy_profile_id=profile.profile_id,
                    latency_ms=latency_ms,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    estimated_cost_usd=usage.estimated_cost_usd,
                    dlp_input_findings=len(input_enforcement.match_actions),
                    dlp_input_action=input_enforcement.overall_action.name.lower(),
                    dlp_output_findings=len(response_enforcement.match_actions),
                    dlp_categories=dlp_categories,
                    router_used=router_used,
                    input_raw=input_raw,
                    input_masked=input_masked,
                    output_raw=output_raw,
                    output_final=full_text,
                    dlp_findings_json=build_dlp_findings_json(
                        input_enforcement.match_actions,
                        response_enforcement.match_actions,
                        span_map=span_map,
                    ),
                ),
            )
            log_session.commit()

            persisted_row = orchestrator.request_log_service.get_by_id(
                log_session, ctx.request_id or ""
            )
            if persisted_row is not None:
                await orchestrator.alert_evaluator.evaluate_after_request(
                    log_session, persisted_row
                )
                log_session.commit()
        except Exception:
            # Logging failures must never break the stream.
            log_session.rollback()
        finally:
            log_session.close()

        # Realtime AI review hook — scans output_raw (the raw provider
        # response before restore) for PII the outbound DLP missed.
        # Fire-and-forget — never blocks the stream finish.
        maybe_ai_review_background(
            ctx.request_id or "",
            output_raw,
            api_key_id=str(auth.key_id) if auth else None,
        )

        # 5. Finish-reason frame + [DONE]. Content deltas were already
        # emitted incrementally above (one per chunk whose masked
        # window grew past MIN_TAIL_CHARS, plus one tail flush).
        yield "data: {}\n\n".format(json.dumps({
            "id": ctx.request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": final_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": final_finish_reason,
            }],
        }))
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Request-Id": ctx.request_id or "",
        },
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid rating value"},
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        403: {"model": ErrorResponse, "description": "Not authorized for this request"},
        404: {"model": ErrorResponse, "description": "request_id not found"},
    },
)
def submit_feedback(
    body: FeedbackRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: Session = Depends(get_db_session),
) -> FeedbackResponse:
    """Record a thumbs-up / thumbs-down for a prior chat completion."""
    if body.rating not in (1, -1):
        raise ValidationError(
            "rating must be 1 (thumbs-up) or -1 (thumbs-down).",
            details={"received": body.rating},
        )

    row = session.get(RequestLogORM, body.request_id)
    if row is None:
        raise NotFoundError(
            f"Request '{body.request_id}' not found.",
            details={"request_id": body.request_id},
        )

    # Tenant isolation: callers can only rate requests belonging to their tenant.
    check_tenant_access(auth, row.tenant_id)

    now = datetime.now(timezone.utc)
    row.feedback_rating = body.rating
    row.feedback_comment = (body.comment or None)
    row.feedback_at = now
    session.flush()

    return FeedbackResponse(
        request_id=row.request_id,
        rating=row.feedback_rating,
        recorded_at=now,
    )
