"""Public chat completions route — thin transport layer.

Non-streaming requests delegate to GatewayOrchestrator.
Streaming requests resolve context via the orchestrator, run the same
inbound DLP mask, stream from the provider adapter, then buffer +
restore the full body before emitting it back to the client.
"""

import asyncio
import json
import orjson
import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import ResolvedApiKey, check_tenant_access, get_db_session, get_resolved_api_key
from app.core.db import get_async_db
from app.core.config import settings
from app.core.exceptions import (
    DLPViolationError,
    GuardrailViolationError,
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
    safe_input_masked_snapshot,
    safe_input_snapshot,
    safe_output_raw_snapshot,
    safe_output_snapshot,
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
from app.services.policy.profile_service import (
    get_runtime_profile,
    get_runtime_profile_async,
)
from app.services.policy.profiles import get_profile as get_runtime_profile_fallback
from app.services.usage_service import _estimate_cost, _estimate_reasoning_cost

logger = structlog.get_logger(__name__)

router = APIRouter()


def _sse_dumps(obj, ensure_ascii: bool = False) -> str:
    """Serialise an SSE frame with orjson (Phase-1 B9).

    Drop-in for the prior ``json.dumps(obj, ensure_ascii=False)`` — orjson
    always emits compact UTF-8 with non-ASCII unescaped, so ``ensure_ascii``
    is accepted as a no-op for call-site compatibility. Returns ``str`` for
    the ``"data: {}\\n\\n".format(...)`` SSE framing.
    """
    return orjson.dumps(obj).decode("utf-8")


@router.post(
    "/chat/completions",
    response_model=ChatCompletionsResponse,
    # Drop None-valued optional fields from the serialised response
    # body. Required so that plain (non-tool) responses don't gain a
    # noisy `"tool_calls": null` on the wire, and so that an
    # assistant-with-tool_calls reply serialises `content: null` only
    # when the model actually returned null.
    response_model_exclude_none=True,
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
    response: Response,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
):
    orchestrator = GatewayOrchestrator()

    if not payload.stream:
        # Phase-2 fallback metadata is surfaced through ``out_meta`` —
        # the orchestrator populates it after dispatch (and after the
        # fallback chain, if one fired). We translate it into the
        # X-Gateway-* response headers below so SDK callers can see
        # which provider actually answered without parsing the body.
        out_meta: dict = {}
        result = await orchestrator.handle_chat_completion(
            auth=auth,
            payload=payload,
            session=session,
            out_meta=out_meta,
        )
        if out_meta.get("provider_used"):
            response.headers["X-Gateway-Provider"] = str(
                out_meta["provider_used"]
            )
        if out_meta.get("model_used"):
            response.headers["X-Gateway-Model"] = str(out_meta["model_used"])
        if out_meta.get("fallback_used"):
            response.headers["X-Gateway-Fallback"] = "true"
        response.headers["X-Gateway-Attempt"] = str(
            out_meta.get("attempt_number", 1)
        )
        return result

    # Captured here so the latency we record on the request_log row covers
    # the full request lifetime, not just the time inside event_generator.
    t_start = time.perf_counter()

    # Streaming order — mirrors handle_chat_completion:
    #   1. Resolve project context
    #   2. DLP mask input  (must run before router chain — semantic
    #      router embeddings must never see raw PII)
    #   3. Guardrail check (regex blocklist on post-DLP-mask messages)
    #   4. Moderation
    #   5. Router chain on masked messages (may rewrite payload.model)
    #   6. select_route (now respects the router's model choice)
    #   7. supports_streaming check → fall back to non-stream if needed
    #   8. Stream → buffer → DLP restore → log with router_used
    # Phase-2 (hybrid): the streaming pre-phase DB reads (project context,
    # tenant lookups) run off the event loop so the connection checkout
    # doesn't block the loop under concurrency. The request session is
    # released before the stream body; the end-of-stream log persist runs
    # on its own short-lived session.
    ctx = await orchestrator.resolve_streaming_context(
        auth=auth, payload=payload, session=session,
    )

    log = logger.bind(
        request_id=ctx.request_id,
        tenant_id=ctx.tenant_id,
        project_id=ctx.project_id,
    )

    profile_id = ctx.policy_profile_id or settings.dlp_default_profile
    profile = (
        await get_runtime_profile_async(session, profile_id)
        if session is not None
        else get_runtime_profile_fallback(profile_id)
    )
    log.info(
        "policy_profile_selected",
        policy_profile=profile.profile_id,
        severity_threshold_block=(
            profile.severity_threshold_block.name.lower()
            if profile.severity_threshold_block is not None
            else None
        ),
    )

    # 1. Inbound DLP: classify, block if needed, mask with indexed tokens.
    # Same flow as GatewayOrchestrator.handle_chat_completion steps 2-4.
    from app.domain.dlp import EnforcementAction, EnforcementDecision
    from app.domain.policy import PolicyProfile

    # DLP trace snapshot 1/4: raw user input, captured before any DLP /
    # moderation work runs so blocked streams still record what was sent.
    input_raw = extract_messages_text(payload.messages)

    # Safe Trace audit channel — encrypt once for every persist site in
    # this stream. Mirrors orchestrator.handle_chat_completion.
    from app.core.dlp_audit_crypto import (
        DlpAuditCryptoError,
        encrypt as encrypt_audit_blob,
    )
    try:
        input_raw_encrypted = encrypt_audit_blob(input_raw or "")
    except DlpAuditCryptoError:
        input_raw_encrypted = None

    messages_to_send = list(payload.messages)
    span_map: dict[str, str] = {}
    # Per-detector mask index counters carried from inbound to outbound so
    # that a new PII finding in the provider response doesn't re-use an
    # index already bound to an inbound original. See transformer docstring.
    inbound_counters: dict[str, int] = {}
    input_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)

    # Cross-turn determinism — mirrors the non-streaming branch in
    # ``orchestrator.handle_chat_completion``. Opt-in via
    # ``payload.metadata.conversation_id`` + vault enabled. The vault
    # snapshot becomes a value→token reverse map that the transformer
    # consults during masking so the same PII keeps the same ``<rN>``
    # across turns. Without ``conversation_id`` or with vault off, the
    # masking call falls back to fresh per-request counters.
    conversation_key_stream = None  # legacy parameter kept for signature compat
    _conv_id_for_key = payload.metadata.get("conversation_id")
    vault_reverse_map_stream: dict[str, str] = {}
    stream_inbound_start_counters: dict[str, int] | None = None
    if (
        _conv_id_for_key
        and settings.dlp_enabled
        and settings.dlp_conversation_vault_enabled
    ):
        from app.services.dlp.conversation_vault import conversation_vault as _cv_stream
        from app.services.dlp.transformer import (
            recover_counters_from_vault as _recover_counters,
        )
        _existing_vault = await _cv_stream.get_all_tokens_async(_conv_id_for_key)
        if _existing_vault:
            vault_reverse_map_stream = {
                value: token for token, value in _existing_vault.items()
            }
            # Recover BOTH the legacy ``__global__`` counter (for
            # ``<rN>`` tokens) AND any per-category counters (for
            # ``<<CAT_N>>`` pseudonym tokens). A vault that pre-dates
            # pseudonym mode just yields ``{__global__: N}`` — exact
            # legacy behaviour preserved.
            _recovered = _recover_counters(_existing_vault)
            if _recovered:
                stream_inbound_start_counters = _recovered

    if settings.dlp_enabled:
        # Per-tenant NER gate — identical to orchestrator.handle_chat_completion.
        # The orchestrator caches two pre-built registries (one with
        # Presidio, one with the ``thai_name_no_prefix`` regex
        # fallback) and we pick by ``ctx.ner_enabled``.
        input_registry = orchestrator._select_registry(ctx.ner_enabled)
        # B2: full-message inbound scan runs off the event loop.
        detection = await orchestrator._scan_messages_async(
            input_registry,
            payload.messages,
            direction="input",
            scan_system_messages=profile.scan_system_messages,
        )
        input_enforcement = orchestrator._scan_and_evaluate(detection, profile)

        if input_enforcement.is_blocked:
            # Persist BLOCKED row + commit BEFORE raising — see
            # rationale at orchestrator.py inbound DLP block. The
            # streaming variant runs this check before mask, so
            # input_masked is intentionally None here.
            if session is not None:
                try:
                    await orchestrator.request_log_service.persist_async(
                        session,
                        RequestRecord(
                            request_id=ctx.request_id or "",
                            tenant_id=ctx.tenant_id,
                            project_id=ctx.project_id,
                            provider="",
                            model=payload.model,
                            status=RequestStatus.BLOCKED,
                            policy_action="block",
                            policy_profile_id=profile.profile_id,
                            latency_ms=int((time.perf_counter() - t_start) * 1000),
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            estimated_cost_usd=0.0,
                            error_code="dlp_violation",
                            input_raw="[BLOCKED_BY_DLP]",
                            input_raw_encrypted=input_raw_encrypted,
                            dlp_findings_json=build_dlp_findings_json(
                                input_enforcement.match_actions,
                            ),
                        ),
                    )
                    await session.commit()
                except Exception as exc:
                    log.warning("dlp_block_log_persist_failed", error=str(exc))
            await orchestrator.audit_service.record_blocked_request_async(
                ctx=ctx, enforcement=input_enforcement, direction="input"
            )
            # Raising here (before returning StreamingResponse) lets the
            # central error handler turn it into a 403 JSON response —
            # the client never sees a partial SSE body.
            raise DLPViolationError(
                "Request blocked by DLP policy.",
                details={
                    "action": "block",
                    "policy_profile": input_enforcement.policy_profile_id,
                    "categories": sorted(
                        {ma.match.category.upper() for ma in input_enforcement.match_actions}
                    ),
                    "findings_count": len(input_enforcement.match_actions),
                    "reasons": input_enforcement.reasons,
                    "direction": "input",
                },
            )

        if input_enforcement.requires_transformation:
            messages_to_send, span_map, inbound_counters = orchestrator.transformer.mask_messages(
                payload.messages,
                input_enforcement.match_actions,
                conversation_key=conversation_key_stream,
                start_counters=stream_inbound_start_counters,
                vault_reverse_map=vault_reverse_map_stream or None,
            )

    # Cross-turn DLP vault — mirrors the non-streaming branch in
    # ``orchestrator.handle_chat_completion``. Pulled out of the
    # ``settings.dlp_enabled`` block above so the resolver can still
    # run on the response when DLP is disabled inbound but the
    # caller has sent a ``conversation_id`` referencing an earlier
    # turn's mask. ``conversation_id`` stays None for callers that
    # don't opt in — single-turn behaviour is unchanged.
    conversation_id = payload.metadata.get("conversation_id")
    if conversation_id and span_map and settings.dlp_conversation_vault_enabled:
        from app.services.dlp.conversation_vault import conversation_vault
        await conversation_vault.extend_async(conversation_id, span_map)

    output_actions = (
        profile.category_actions_output
        if profile.category_actions_output is not None
        else profile.category_actions
    )
    pre_restore_profile = PolicyProfile(
        profile_id=profile.profile_id,
        name=profile.name,
        category_actions=profile.category_actions,
        category_actions_output={
            cat: (
                EnforcementAction.MASK
                if action == EnforcementAction.RESTORE
                else action
            )
            for cat, action in output_actions.items()
        },
        severity_threshold_block=profile.severity_threshold_block,
        scan_responses=profile.scan_responses,
        moderation_enabled=profile.moderation_enabled,
        scan_system_messages=profile.scan_system_messages,
    )
    # Restore allow-list — delegated to the orchestrator helper so
    # streaming inherits the same Layer 2 secret-restore exclusion as
    # the non-stream path. The previous inline loop did not honour
    # ``_NEVER_RESTORE_CATEGORIES``, so a custom DB profile that
    # restored credentials would have leaked secrets back to the user
    # on streaming requests. See ``orchestrator._compute_restore_allowed_tokens``.
    # Pass the ORIGINAL ``profile`` (not ``pre_restore_profile``) —
    # ``pre_restore_profile`` swaps RESTORE→MASK and would yield an
    # empty allow-list for every category.
    restore_allowed_tokens = orchestrator._compute_restore_allowed_tokens(
        profile, span_map, input_enforcement,
    )
    # Filtered span_map for the orphan-redact pass(es) below. Secret
    # tokens are dropped from this map so the orphan pass treats them
    # as unknown placeholders and strips them, instead of seeing them
    # in span_map and concluding "restore handled it" (restore did NOT
    # — Layer 2 hard-blocks credential/secret). Mirrors the section P3
    # treatment in ``orchestrator.handle_chat_completion``.
    _redact_span_map = orchestrator._strip_never_restore_tokens(
        span_map, input_enforcement,
    )

    # Inject system prompt explaining DLP placeholders to the model.
    # Gated on the inbound DLP findings count so clean prompts go
    # straight to the provider without the DLP-context preamble.
    if input_enforcement.match_actions:
        from app.services.dlp_prompt import inject_dlp_system_prompt
        # Stream path mirrors the non-stream orchestrator — enumerate
        # the request's ``<<...>>`` placeholders into the prompt so
        # the LLM treats them as a closed set.
        messages_to_send = inject_dlp_system_prompt(
            messages_to_send, span_map=span_map,
        )
        logger.info(
            "dlp_system_prompt_injected",
            findings=len(input_enforcement.match_actions),
        )

    # DLP trace snapshot 2/4: messages as they will be streamed to the
    # provider — same semantics as the non-streaming path. Equal to
    # ``input_raw`` when no PII was masked.
    input_masked = extract_messages_text(messages_to_send)

    # 1b. Guardrail check — mirrors orchestrator.py:317-371. Runs on the
    # post-DLP-mask messages so guardrail rules see the same content the
    # provider would. Pre-stream so a block returns a clean 403 (the
    # central error handler maps PolicyViolationError → 403 JSON before
    # StreamingResponse is constructed; the client never sees a partial
    # SSE body — same contract as the DLP and moderation pre-stream
    # blocks above and below).
    dict_messages_for_guard = [m.model_dump() for m in messages_to_send]
    guardrail_result = orchestrator.guardrail_service.check(
        dict_messages_for_guard
    )
    if not guardrail_result.passed:
        log.info(
            "guardrail_blocked",
            rule=guardrail_result.rule_name,
            reason=guardrail_result.reason,
            streaming=True,
        )
        if session is not None:
            try:
                await orchestrator.request_log_service.persist_async(
                    session,
                    RequestRecord(
                        request_id=ctx.request_id or "",
                        tenant_id=ctx.tenant_id,
                        project_id=ctx.project_id,
                        provider="",
                        model=payload.model,
                        status=RequestStatus.BLOCKED,
                        # ``policy_action='block'`` aligns with the
                        # dashboard / alert filters; the *kind* of
                        # block is preserved on ``error_code``.
                        policy_action="block",
                        policy_profile_id=profile.profile_id,
                        latency_ms=int((time.perf_counter() - t_start) * 1000),
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        estimated_cost_usd=0.0,
                        error_code="guardrail_blocked",
                        input_raw=safe_input_snapshot(
                            input_raw, input_masked, input_enforcement
                        ),
                        input_masked=safe_input_masked_snapshot(
                            input_masked, input_enforcement
                        ),
                        input_raw_encrypted=input_raw_encrypted,
                        dlp_findings_json=build_dlp_findings_json(
                            input_enforcement.match_actions,
                            span_map=span_map,
                        ),
                    ),
                )
                # Commit BEFORE raise — the dep-injected session at
                # api/deps.py:33-43 calls session.rollback() in its
                # except branch when the violation propagates,
                # which would otherwise discard the row.
                await session.commit()
            except Exception as exc:
                log.warning("guardrail_log_persist_failed", error=str(exc))
        raise GuardrailViolationError(
            guardrail_result.reason,
            details={"rule": guardrail_result.rule_name},
        )

    # 1c. Moderation (OpenAI /v1/moderations) — opt-in per policy profile.
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
            openai_key = await orchestrator.provider_key_service.get_api_key_async(
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
                        await orchestrator.request_log_service.persist_async(
                            session,
                            RequestRecord(
                                request_id=ctx.request_id or "",
                                tenant_id=ctx.tenant_id,
                                project_id=ctx.project_id,
                                provider="",
                                model=payload.model,
                                status=RequestStatus.BLOCKED,
                                # ``policy_action='block'`` aligns with
                                # the dashboard / alert filters; *kind*
                                # is in ``error_code``. See orchestrator
                                # guardrail/moderation blocks for the
                                # rationale.
                                policy_action="block",
                                policy_profile_id=profile.profile_id,
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                estimated_cost_usd=0.0,
                                error_code="moderation_flagged",
                                input_raw=safe_input_snapshot(
                                    input_raw, input_masked, input_enforcement
                                ),
                                input_masked=safe_input_masked_snapshot(
                                    input_masked, input_enforcement
                                ),
                                input_raw_encrypted=input_raw_encrypted,
                                dlp_findings_json=build_dlp_findings_json(
                                    input_enforcement.match_actions,
                                    span_map=span_map,
                                ),
                            ),
                        )
                        # Commit now — the ModerationViolationError raised
                        # below propagates through the dependency's except
                        # branch, which would otherwise roll this row back.
                        await session.commit()
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

    # 1d. Router chain — semantic > keyword > service, on DLP-masked
    # messages. Mutates payload.model when a router fires. Any failure
    # falls back to plain "service" routing so the stream never breaks.
    dict_messages = [m.model_dump() for m in messages_to_send]
    try:
        router_used, _matched_rule = await orchestrator._apply_router_chain(
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
    # Auto Thinking Mode: enable adaptive reasoning when the resolved
    # model can think and the caller didn't opt out. Must run before
    # ``_show_reasoning`` is computed below so the SSE emitter knows to
    # forward reasoning frames.
    orchestrator._maybe_enable_adaptive_reasoning(payload, route.model_name)

    # P0 downgrade signal — reasoning requested but the resolved model
    # can't reason. Computed once here (after the final route + adaptive-
    # reasoning auto-enable) and emitted as the first SSE frame inside the
    # generator so the client can warn the user instead of seeing a plain
    # answer with no thinking. None on the normal path.
    _stream_downgrade_reason = orchestrator._reasoning_downgrade_reason(
        payload, route.model_name
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
    provider_api_key = await orchestrator.provider_key_service.get_api_key_async(
        session, route.provider_name
    )
    if not provider_api_key:
        raise ProviderUnavailableError(
            f"No API key configured for provider '{route.provider_name}'.",
            details={"provider": route.provider_name},
        )

    # Bind the cross-turn DLP vault resolver once per request so
    # every per-window emit + the final tail flush can pass the
    # same callable into ``transformer.restore``. ``None`` when the
    # caller didn't send a ``conversation_id`` — the restore path
    # then falls back to single-turn orphan-warning behaviour.
    _vault_resolver_stream = None
    if conversation_id and settings.dlp_conversation_vault_enabled:
        from app.services.dlp.conversation_vault import (
            conversation_vault as _stream_vault,
        )
        _vault_resolver_stream = lambda tokens, _cid=conversation_id: (
            _stream_vault.resolve(_cid, tokens)
        )

    # Capture the per-request ``SessionLocal`` factory so
    # ``event_generator`` can mint a fresh session at log-write time
    # without reaching into the module-global ``app.core.db.SessionLocal``
    # at generator-run time.  Per-request resolution:
    #   (a) Preserves the ``app.core.db.SessionLocal`` autouse
    #       monkeypatch in tests (the import below re-reads the
    #       attribute every request, after the fixture's setattr).
    #   (b) Is the future hook for tenant-aware engine selection.
    #       Today: always the global default. Tomorrow: this is where
    #       a multi-tenant deployment would call ``pick_factory(ctx)``
    #       to pick a read replica, sharded engine, or async driver.
    # Closes known-issues #6.
    # Track A: the in-generator post-stream persist runs AFTER the request
    # dep (async) session has closed AND after the SSE response — there is
    # no provider call to release a connection from at that point (the H1
    # win is the dep session closing BEFORE the stream body). So this stays
    # a short-lived SYNC SessionLocal: it avoids running an async engine
    # inside the SSE generator (which executes outside the request's event-
    # loop context) and preserves the original, well-tested persist path.
    from app.core.db import SessionLocal as _SessionLocal
    log_session_factory = _SessionLocal

    # ----------------------------------------------------------------
    # Phase-2 streaming fallback (pre-check).
    #
    # Mid-stream provider swaps would corrupt SSE because headers and
    # content-type are already on the wire, so we do the fallback
    # decision BEFORE the response is built. Two mechanisms:
    #
    #   1. Redis pre-check — if the primary provider has a recent
    #      ``provider:health:{name}`` marker (set by any failed
    #      dispatch in the last 60s), swap to the first viable
    #      streaming candidate from the fallback chain right here.
    #      Headers will reflect the post-swap route.
    #
    #   2. In-generator retry (below) — if the first chunk fetch
    #      raises a fallback-eligible GatewayError BEFORE any byte
    #      has been yielded to the client, the generator walks the
    #      fallback chain a second time. Headers cannot be rewritten
    #      after StreamingResponse construction, so this path is
    #      a best-effort body recovery — the X-Gateway-* headers will
    #      describe the primary even though the body came from the
    #      fallback. The retry is gated on ``first_chunk_yielded``
    #      so a successful partial stream is never abandoned.
    # ----------------------------------------------------------------
    from app.services.gateway.fallback_orchestrator import (
        clear_provider_health as _clear_health,
        is_fallback_eligible as _is_fallback_eligible,
        is_provider_unhealthy as _is_unhealthy,
        mark_provider_unhealthy as _mark_unhealthy,
    )
    from datetime import datetime as _dt, timezone as _tz

    _stream_original_provider = route.provider_name
    _stream_original_model = route.model_name
    _stream_fallback_used = False
    _stream_attempt_number = 1
    _stream_fallback_attempts: list[dict] = []

    # Hard capabilities (vision/tools) so a streaming pre-check / mid-
    # stream swap never falls back to a model that can't serve them.
    from app.services.capability_detection import detect_required_capabilities

    _stream_required_caps = detect_required_capabilities(payload)

    if await _is_unhealthy(route.provider_name):
        fb_runner = orchestrator._get_fallback_orchestrator()
        fb_candidate = fb_runner.select_streaming_candidate(
            original_provider=_stream_original_provider,
            original_model=_stream_original_model,
            session=session,
            required=_stream_required_caps,
        )
        if fb_candidate is not None:
            log.info(
                "streaming_pre_check_swap",
                original_provider=_stream_original_provider,
                original_model=_stream_original_model,
                fallback_provider=fb_candidate["provider"],
                fallback_model=fb_candidate["model"],
            )
            route.provider_name = fb_candidate["provider"]
            route.model_name = fb_candidate["model"]
            provider_api_key = fb_candidate["api_key"]
            adapter = orchestrator.provider_dispatcher.adapters[
                fb_candidate["provider"]
            ]
            _stream_fallback_used = True
            _stream_attempt_number = 2
            _stream_fallback_attempts.append(
                {
                    "provider": _stream_original_provider,
                    "model": _stream_original_model,
                    "error_code": "health_marker_skip",
                    "error_message": (
                        "Skipped at streaming pre-check — Redis health "
                        "marker indicated recent failure within TTL."
                    ),
                    "latency_ms": 0.0,
                    "attempted_at": _dt.now(_tz.utc).isoformat(),
                }
            )

    async def event_generator():
        # ``nonlocal`` so the in-generator fallback can rebind the
        # flags captured by the StreamingResponse headers and the
        # request_log persist block below. Pre-check fallback (above)
        # runs in the enclosing function so it doesn't need nonlocal.
        nonlocal _stream_fallback_used, _stream_attempt_number
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
        # so emails/phones/SSNs/credit-cards are safe at N=120.
        # Sized to cover the longest sensitive pattern we detect —
        # ``db_connection_string`` matches up to ~100 chars
        # (``postgresql://user:pwd@host:5432/db?sslmode=require…``).
        # 120 = ~100 + 20-char safety margin for variants like long
        # Thai-address blocks and the base64 tail of a PEM private key
        # header line, while still keeping per-chunk emit latency low.
        MIN_TAIL_CHARS = 120

        tail_masked = ""
        cumulative_raw = ""
        masked_provider_parts: list[str] = []
        emitted_parts: list[str] = []
        accumulated_outbound_actions = []
        counters_mut = dict(inbound_counters)
        # Streaming-side accumulator of every outbound mask token minted
        # across the per-chunk DLP passes. Fed by ``mask_text`` calls in
        # the chunk helper; consumed at the tail flush so the orphan-
        # redact pass can distinguish AI-invented placeholders from
        # legitimate outbound masks (same contract as the non-streaming
        # orchestrator path).
        _stream_outbound_span_map: dict[str, str] = {}
        final_finish_reason: str | None = None
        final_model: str = route.model_name
        provider_usage = None

        # P0 downgrade notice — first frame on the wire when reasoning was
        # requested but the resolved model can't reason. ``reasoning_notice``
        # is a sibling of ``reasoning_summary_delta`` / ``analysis_status``;
        # unknown-type frames are safely ignored by older clients.
        if _stream_downgrade_reason:
            log.info(
                "reasoning_downgraded",
                requested_mode=getattr(payload.reasoning, "mode", None),
                resolved_model=route.model_name,
            )
            yield "data: {}\n\n".format(
                _sse_dumps(
                    {
                        "id": ctx.request_id,
                        "type": "reasoning_notice",
                        "created": int(time.time()),
                        "model": route.model_name,
                        "reasoning_downgraded": True,
                        "reason": _stream_downgrade_reason,
                    },
                    ensure_ascii=False,
                )
            )

        # Per-request tool_call argument accumulator. Keyed by the
        # tool_call index we forward to the client. Each entry tracks
        # {id, name, args} so the post-stream DLP pass can scan the
        # complete arguments string once the stream closes. We cannot
        # mask retroactively (bytes already on the wire), so this is
        # an audit-only pass — findings are logged for governance, the
        # client still receives the unmasked stream. The non-streaming
        # path in orchestrator.py section 8d DOES mask before
        # returning; the asymmetry is documented there.
        _streaming_tool_call_args: dict[int, dict] = {}

        # Reasoning / thinking SSE state (T2). Tracks the analysis
        # phase so the emitter can bracket the reasoning stream with
        # ``analysis_status: started`` / ``analysis_status: completed``
        # events and accumulate reasoning text for the post-stream
        # token-count estimate. ``show_reasoning`` is the per-request
        # gate: model still thinks (better answers) when False, but
        # the client never sees the reasoning bytes — only the
        # ``reasoning_tokens`` total in the final usage frame. Mirrors
        # the OpenAI Responses-API contract.
        _reasoning_cfg = getattr(payload, "reasoning", None)
        _show_reasoning = bool(
            _reasoning_cfg
            and _reasoning_cfg.show_summary
            and _reasoning_cfg.mode != "off"
        )
        _reasoning_started = False
        _reasoning_completed = False
        _reasoning_text_total = ""
        # T3: rolling-window DLP buffer for reasoning text — mirrors
        # the content-channel ``tail_masked`` pattern but is its own
        # independent buffer because the two channels stream
        # interleaved fragments and must not share masked-token state.
        # Same MIN_TAIL_CHARS (120) ceiling so a PII pattern spanning
        # two reasoning chunks (e.g. an email split across boundary)
        # is still caught when the second half arrives. The reasoning
        # channel deliberately skips the post-mask restore + orphan
        # redact passes — reasoning is the model's internal monologue,
        # so masked tokens should stay masked rather than be replaced
        # with the user's original PII.
        _reasoning_tail_masked = ""

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
                # NER disabled on outbound scan — same rationale as the
                # non-streaming orchestrator (Section 8 + 8c). Presidio
                # English NER over Thai AI output mints noise tokens
                # that survive restore as mid-word splices.
                detection = orchestrator._scan_text_as_result(
                    text, direction="output", ner_enabled=False,
                )
                enforcement = orchestrator._scan_and_evaluate(
                    detection, pre_restore_profile
                )
                if enforcement.is_blocked:
                    return "", enforcement.match_actions
                if not enforcement.requires_transformation:
                    return text, enforcement.match_actions
                masked, outbound_window_map, new_counters = orchestrator.transformer.mask_text(
                    text,
                    enforcement.match_actions,
                    start_counters=counters_mut,
                    conversation_key=conversation_key_stream,
                    vault_reverse_map=vault_reverse_map_stream or None,
                )
                # Outbound vault extend is intentionally skipped on the
                # streaming path: this nested helper runs sync inside
                # the asyncio generator loop, where the ``run_sync``
                # bridge into the shared Redis async client triggers a
                # cross-loop Future error and corrupts the cached
                # transport — every subsequent vault op (including the
                # legitimate inbound resolver) then fails with
                # "TCPTransport closed". Outbound entries here are
                # audit-only; the user-facing masking invariant is
                # enforced by ``span_map`` + the per-chunk orphan-redact
                # below regardless of whether the audit row is written.
                # The non-streaming orchestrator still writes outbound
                # tokens to the vault via ``await extend_async`` in
                # section 8.
                # Mutate via the enclosing variable binding.
                counters_mut.clear()
                counters_mut.update(new_counters)
                # Accumulate the outbound mask tokens for the tail-
                # flush orphan redact below. Per-chunk maps don't
                # overlap by construction (start_counters threads
                # across) so a plain update is safe.
                if outbound_window_map:
                    _stream_outbound_span_map.update(outbound_window_map)
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
            """Serialise a content-delta SSE frame.

            ``ensure_ascii=False`` keeps Thai / CJK content in the
            stream as UTF-8 instead of ``\\uXXXX`` escapes — matches
            the project-wide UTF8JSONResponse contract and reads
            cleanly when piping the stream through ``curl``.
            """
            return "data: {}\n\n".format(
                _sse_dumps(
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
                    },
                    ensure_ascii=False,
                )
            )

        def _emit_tool_calls_chunk(tool_calls: list[dict]) -> str:
            """Serialise a tool_calls-delta SSE frame.

            OpenAI streams tool_calls as partial fragments: the first
            chunk carries ``id`` + ``function.name`` + an empty
            ``function.arguments``; later chunks append to ``arguments``
            character-by-character. We forward each fragment verbatim
            so the client can stitch the JSON itself (same contract a
            direct OpenAI stream provides).

            Tool-call fragments are NOT routed through the per-chunk
            DLP scan. They're structured JSON the model generates from
            already-masked inbound context; the per-chunk argument
            slices are not self-contained text the DLP detectors can
            reason about. Argument-level DLP is a follow-up.
            """
            return "data: {}\n\n".format(
                _sse_dumps(
                    {
                        "id": ctx.request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": final_model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"tool_calls": tool_calls},
                                "finish_reason": None,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )

        def _emit_reasoning_chunk(text: str) -> str:
            """Serialise a reasoning-delta SSE frame (T2).

            Provider-neutral event type ``reasoning_summary_delta`` so
            the same frame shape carries Anthropic thinking, OpenAI
            o-series reasoning summary, DeepSeek-R1 reasoning_content,
            etc. The frame intentionally omits the OpenAI
            ``chat.completion.chunk`` envelope — the SDK should
            distinguish reasoning from content by event type, not by
            position inside the choices array. Clients that don't yet
            understand the event type will simply ignore the frame
            (their parser will skip lines whose ``type`` is unknown).
            """
            return "data: {}\n\n".format(
                _sse_dumps(
                    {
                        "id": ctx.request_id,
                        "type": "reasoning_summary_delta",
                        "created": int(time.time()),
                        "model": final_model,
                        "text": text,
                    },
                    ensure_ascii=False,
                )
            )

        def _emit_analysis_status(status: str) -> str:
            """Serialise an analysis-status SSE frame (T2).

            Brackets the reasoning stream. ``started`` is emitted on
            the first reasoning_delta; ``completed`` is emitted when
            the model transitions to visible output (or at stream end
            if no visible output ever arrives). Lets the client show
            a "thinking…" indicator that flips to "thinking complete"
            without parsing every reasoning fragment.
            """
            return "data: {}\n\n".format(
                _sse_dumps(
                    {
                        "id": ctx.request_id,
                        "type": "analysis_status",
                        "created": int(time.time()),
                        "status": status,
                    },
                    ensure_ascii=False,
                )
            )

        from app.services.dlp.transformer import sanitize_hallucinated_brackets

        # Mutable state for the in-generator fallback retry. The outer
        # ``while True`` walks the fallback chain at most
        # ``MAX_STREAMING_FALLBACKS`` times after the primary; once any
        # chunk has been emitted (``first_chunk_yielded = True``), a
        # mid-stream error is logged and the SSE is closed gracefully
        # instead of attempting a swap that would corrupt the body.
        first_chunk_yielded = False
        skip_providers: set[str] = set()
        MAX_STREAMING_FALLBACKS = 2

        # Local refs we may rebind on fallback. ``adapter`` /
        # ``provider_api_key`` come from the enclosing scope; we keep
        # nonlocal bindings via these copies so the retry block can
        # point ``stream_chat`` at the new candidate.
        stream_adapter = adapter
        stream_api_key = provider_api_key

        while True:
            try:
                async for chunk in stream_adapter.stream_chat(
                    payload, messages_to_send,
                    resolved_model=route.model_name,
                    api_key=stream_api_key,
                ):
                    # Reasoning / thinking branch (T2). Runs before the
                    # content / tool_calls branches because Anthropic
                    # emits thinking blocks BEFORE the visible text;
                    # processing reasoning first keeps the
                    # ``analysis_status: started → completed`` ordering
                    # natural. Always accumulate reasoning text for
                    # the post-stream usage estimate even when the
                    # caller didn't opt in to ``show_summary`` — the
                    # token count is part of billing and must reflect
                    # actual model spend.
                    if chunk.reasoning_delta:
                        _reasoning_text_total += chunk.reasoning_delta
                        if _show_reasoning:
                            if not _reasoning_started:
                                _reasoning_started = True
                                yield _emit_analysis_status("started")
                                first_chunk_yielded = True
                            # T3 rolling-window DLP. Same pattern as
                            # the content channel: scan
                            # tail+new_chunk together, emit everything
                            # except the last MIN_TAIL_CHARS, retain
                            # the tail so PII spanning a chunk
                            # boundary still matches when the second
                            # half arrives. Boundary-split protection
                            # for masked tokens (``<<...>>`` and
                            # ``<...>``) mirrors the content path so
                            # an already-masked placeholder is never
                            # split mid-stream.
                            r_window = _reasoning_tail_masked + chunk.reasoning_delta
                            r_masked_window, r_actions = _dlp_scan_and_mask(r_window)
                            if r_actions:
                                accumulated_outbound_actions.extend(r_actions)
                            if len(r_masked_window) > MIN_TAIL_CHARS:
                                r_safe = r_masked_window[:-MIN_TAIL_CHARS]
                                _reasoning_tail_masked = r_masked_window[-MIN_TAIL_CHARS:]
                                # Pull a trailing ``<<...`` (no
                                # matching ``>>``) back into the tail
                                # so we never split a pseudonym token.
                                _last_dbl_open = r_safe.rfind("<<")
                                _last_dbl_close = r_safe.rfind(">>")
                                if (
                                    _last_dbl_open != -1
                                    and _last_dbl_close < _last_dbl_open
                                ):
                                    _reasoning_tail_masked = (
                                        r_safe[_last_dbl_open:] + _reasoning_tail_masked
                                    )
                                    r_safe = r_safe[:_last_dbl_open]
                                # Same guard for legacy ``<rN>`` shape.
                                _last_lt = r_safe.rfind("<")
                                if _last_lt != -1 and r_safe.rfind(">") < _last_lt:
                                    _reasoning_tail_masked = (
                                        r_safe[_last_lt:] + _reasoning_tail_masked
                                    )
                                    r_safe = r_safe[:_last_lt]
                                if r_safe:
                                    yield _emit_reasoning_chunk(r_safe)
                            else:
                                _reasoning_tail_masked = r_masked_window
                    # Analysis-status transition: any non-reasoning
                    # signal (content delta / tool_calls / finish)
                    # arriving after reasoning started flips us into
                    # the "completed" state exactly once. Gated on
                    # ``_show_reasoning`` so we don't emit a phantom
                    # ``completed`` to clients that never saw the
                    # ``started`` event.
                    if (
                        _show_reasoning
                        and _reasoning_started
                        and not _reasoning_completed
                        and (
                            chunk.delta
                            or chunk.tool_calls
                            or chunk.finish_reason
                        )
                    ):
                        _reasoning_completed = True
                        yield _emit_analysis_status("completed")

                    if chunk.delta:
                        cumulative_raw += chunk.delta
                        # Combine retained masked tail + new raw chunk text.
                        # The scanner won't re-match already-masked placeholders
                        # (`<EMAIL_1>` is not a valid email), so keeping masked
                        # text in the tail is safe and deduplication-friendly.
                        window = tail_masked + chunk.delta
                        masked_window, actions = _dlp_scan_and_mask(window)
                        # Drop ``[[CAT_N]]`` shapes the model invented. Runs on
                        # the full window (which already buffers split tokens
                        # via ``tail_masked``) so a double-bracket spanning two
                        # chunks is still caught when the closing ``]]`` lands.
                        masked_window = sanitize_hallucinated_brackets(masked_window, span_map)
                        if actions:
                            accumulated_outbound_actions.extend(actions)

                        if len(masked_window) > MIN_TAIL_CHARS:
                            safe = masked_window[:-MIN_TAIL_CHARS]
                            tail_masked = masked_window[-MIN_TAIL_CHARS:]
                            # Guard against splitting an already-masked
                            # token at the safe/tail boundary. Two
                            # shapes to protect:
                            #
                            #   1. ``<<CAT_N>>`` (pseudonym mode) —
                            #      could split as ``<<EMAI`` + ``L_1>>``
                            #      OR as ``<<EMAIL_1>`` + ``>`` (just
                            #      the closing ``>>`` split). Check for
                            #      a trailing ``<<…`` with no matching
                            #      ``>>`` after it.
                            #   2. ``<rN>`` (legacy) — could split as
                            #      ``<r`` + ``0>``. Check for a
                            #      trailing single ``<`` with no
                            #      matching ``>`` after it.
                            #
                            # Pass 1 must run first because case 2's
                            # logic would only push from the second
                            # ``<`` of ``<<`` and leak the first one.
                            _last_dbl_open = safe.rfind("<<")
                            _last_dbl_close = safe.rfind(">>")
                            if (
                                _last_dbl_open != -1
                                and _last_dbl_close < _last_dbl_open
                            ):
                                tail_masked = safe[_last_dbl_open:] + tail_masked
                                safe = safe[:_last_dbl_open]
                            _last_lt = safe.rfind("<")
                            if _last_lt != -1 and safe.rfind(">") < _last_lt:
                                tail_masked = safe[_last_lt:] + tail_masked
                                safe = safe[:_last_lt]
                            masked_provider_parts.append(safe)
                            # When a conversation_id is bound, every emit
                            # needs the vault resolver so a token echoed from
                            # an earlier turn is unmasked even though the
                            # per-window scan returned no new findings.
                            if span_map or _vault_resolver_stream is not None:
                                emit_text = orchestrator.transformer.restore(
                                    safe, span_map,
                                    vault_resolver=_vault_resolver_stream,
                                    allowed_tokens=restore_allowed_tokens,
                                )
                            else:
                                emit_text = safe
                            # Strip any orphan ``<rN>`` the model invented
                            # before the chunk hits the wire. Counter-mode
                            # tokens (``<r5>``, ``<r12>``) are easy to
                            # hallucinate, and the non-streaming path
                            # already strips them at section P3 — without
                            # this per-chunk pass the streaming user sees
                            # bare ``<rN>`` placeholders the rest of the
                            # pipeline would have removed. The 120-char
                            # rolling tail upstream guarantees ``safe`` is
                            # past the split-token risk window.
                            from app.services.dlp.transformer import (
                                redact_orphan_tokens as _stream_orphan_redact,
                            )
                            # Use the secret-stripped span_map so any
                            # LLM-echoed credential token (``<r0>``)
                            # falls into the orphan path and gets
                            # deleted, matching the non-stream P3.
                            emit_text = _stream_orphan_redact(
                                emit_text, _redact_span_map,
                            )
                            if emit_text:
                                emitted_parts.append(emit_text)
                                yield _emit_chunk(emit_text)
                                first_chunk_yielded = True
                        else:
                            tail_masked = masked_window

                    # Tool-call deltas — independent of content text.
                    # A chunk MAY carry both ``delta`` (text) and
                    # ``tool_calls`` (e.g. a model that narrates "Let
                    # me check the weather…" then invokes the function
                    # in the same chunk), in which case both branches
                    # fire and we emit two SSE frames for that chunk —
                    # one content delta, one tool_calls delta. The
                    # client merges them by ``choices[0].index`` /
                    # ``delta.tool_calls[].index`` per the OpenAI
                    # streaming contract.
                    if chunk.tool_calls:
                        yield _emit_tool_calls_chunk(chunk.tool_calls)
                        first_chunk_yielded = True
                        # Feed each fragment into the per-request
                        # accumulator. First chunk for an index carries
                        # id + function.name; later chunks may only
                        # carry function.arguments fragments. The
                        # buffered string is DLP-scanned once the
                        # stream finishes (see post-loop block).
                        for _tc in chunk.tool_calls:
                            if not isinstance(_tc, dict):
                                continue
                            _idx = _tc.get("index")
                            if _idx is None:
                                continue
                            _bucket = _streaming_tool_call_args.setdefault(
                                _idx, {"id": "", "name": "", "args": ""}
                            )
                            if _tc.get("id"):
                                _bucket["id"] = _tc["id"]
                            _fn = _tc.get("function") or {}
                            if _fn.get("name"):
                                _bucket["name"] = _fn["name"]
                            _arg_fragment = _fn.get("arguments")
                            if isinstance(_arg_fragment, str) and _arg_fragment:
                                _bucket["args"] += _arg_fragment

                    if chunk.model:
                        final_model = chunk.model
                    if chunk.finish_reason is not None:
                        final_finish_reason = chunk.finish_reason
                    if chunk.usage is not None:
                        provider_usage = chunk.usage
                # Stream completed successfully — clear the health
                # marker for the provider that just answered so the
                # next request's pre-check stops skipping it.
                await _clear_health(route.provider_name)
                break
            except Exception as stream_exc:  # noqa: BLE001
                from app.core.exceptions import GatewayError as _GwErr

                # Mid-stream errors cannot be recovered — headers and
                # part of the body are already on the wire. Log and
                # close the SSE gracefully; the client sees the partial
                # response with no extra error frame.
                if first_chunk_yielded:
                    log.warning(
                        "stream_mid_stream_error",
                        provider=route.provider_name,
                        model=route.model_name,
                        error=str(stream_exc)[:200],
                    )
                    if isinstance(stream_exc, _GwErr) and _is_fallback_eligible(
                        stream_exc
                    ):
                        await _mark_unhealthy(route.provider_name)
                    return

                # Pre-yield error → eligible for body-level fallback.
                if not isinstance(stream_exc, _GwErr) or not _is_fallback_eligible(
                    stream_exc
                ):
                    raise

                failed_provider = route.provider_name
                _stream_fallback_attempts.append(
                    {
                        "provider": failed_provider,
                        "model": route.model_name,
                        "error_code": getattr(
                            stream_exc, "code", "provider_error"
                        ),
                        "error_message": str(stream_exc)[:200],
                        "latency_ms": 0.0,
                        "attempted_at": _dt.now(_tz.utc).isoformat(),
                    }
                )
                await _mark_unhealthy(failed_provider)
                skip_providers.add(failed_provider)

                if len(_stream_fallback_attempts) > MAX_STREAMING_FALLBACKS:
                    raise

                fb_runner = orchestrator._get_fallback_orchestrator()
                # Mint a fresh session for the key lookup — the
                # dependency-injected ``session`` is already closed by
                # the time the generator runs (FastAPI's
                # ``get_db_session`` finally-block fires as soon as the
                # route handler returns ``StreamingResponse``).
                # ``select_streaming_candidate`` is sync; use a short-lived
                # SYNC session for this rare streaming-fallback key lookup
                # (the sync engine is retained for admin/portal/background)
                # rather than cascading async into the selector.
                from app.core.db import SessionLocal as _SyncSessionLocal
                _lookup_session = _SyncSessionLocal()
                try:
                    fb_candidate = fb_runner.select_streaming_candidate(
                        original_provider=_stream_original_provider,
                        original_model=_stream_original_model,
                        session=_lookup_session,
                        skip_providers=skip_providers,
                        required=_stream_required_caps,
                    )
                finally:
                    _lookup_session.close()

                if fb_candidate is None:
                    raise

                log.info(
                    "stream_in_generator_fallback",
                    from_provider=failed_provider,
                    to_provider=fb_candidate["provider"],
                    to_model=fb_candidate["model"],
                )
                route.provider_name = fb_candidate["provider"]
                route.model_name = fb_candidate["model"]
                stream_adapter = orchestrator.provider_dispatcher.adapters[
                    fb_candidate["provider"]
                ]
                stream_api_key = fb_candidate["api_key"]
                _stream_fallback_used = True
                _stream_attempt_number += 1
                continue

        # Flush the remaining tail. The tail already went through a
        # DLP scan in its most recent iteration; no need to re-mask.
        if tail_masked:
            # One last sanitize pass so a ``[[…]]`` that landed in the
            # tail buffer at end-of-stream is rewritten before emit.
            tail_masked = sanitize_hallucinated_brackets(tail_masked, span_map)
            masked_provider_parts.append(tail_masked)
            if span_map or _vault_resolver_stream is not None:
                final_emit = orchestrator.transformer.restore(
                    tail_masked, span_map,
                    vault_resolver=_vault_resolver_stream,
                    allowed_tokens=restore_allowed_tokens,
                )
            else:
                final_emit = tail_masked
            # Orphan-redact pass — same rule as the non-streaming
            # path: anything in the tail that isn't an inbound
            # ``span_map`` token becomes ``[REDACTED]``. That covers
            # both placeholders the model invented from the system
            # prompt's grammar and outbound masks the gateway minted
            # for LLM-generated PII — both of these read as a bare
            # ``<rN>`` to the user otherwise. Streaming can only
            # catch orphans on the tail (earlier chunks are already
            # on the wire), but that's where they cluster.
            from app.services.dlp.transformer import redact_orphan_tokens
            # Filtered span_map — see the per-chunk orphan-redact above
            # for why the secret tokens are excluded here.
            final_emit = redact_orphan_tokens(final_emit, _redact_span_map)
            if final_emit:
                emitted_parts.append(final_emit)
                yield _emit_chunk(final_emit)

        # Tool-call argument DLP — streaming, audit-only. The complete
        # arguments string for each tool_call is scanned once the
        # stream closes. Bytes are already on the wire so we cannot
        # mask retroactively; findings are surfaced as a warning-level
        # log line for SIEM / governance pipelines. Both the
        # categories and the tool_call id/name are recorded so an
        # operator can trace the leak back to the originating call.
        # The non-streaming counterpart (orchestrator.py section 8d)
        # masks instead of audit-logging — only because it has the
        # full payload before any byte leaves the gateway.
        if (
            _streaming_tool_call_args
            and settings.dlp_enabled
            and settings.dlp_scan_responses
            and profile.scan_responses
        ):
            for _idx, _bucket in _streaming_tool_call_args.items():
                _args = _bucket.get("args") or ""
                if not _args:
                    continue
                try:
                    _arg_detection = orchestrator._scan_text_as_result(
                        _args, direction="output", ner_enabled=False,
                    )
                    _arg_enforcement = orchestrator._scan_and_evaluate(
                        _arg_detection, pre_restore_profile
                    )
                except Exception as _scan_exc:
                    log.warning(
                        "dlp_tool_call_streaming_scan_failed",
                        error=str(_scan_exc)[:200],
                        tool_call_id=_bucket.get("id"),
                    )
                    continue
                if _arg_enforcement.match_actions:
                    accumulated_outbound_actions.extend(
                        _arg_enforcement.match_actions
                    )
                    log.warning(
                        "dlp_tool_call_arguments_audit_streaming",
                        tool_call_id=_bucket.get("id"),
                        tool_name=_bucket.get("name"),
                        action=_arg_enforcement.overall_action.name.lower(),
                        findings=len(_arg_enforcement.match_actions),
                        categories=sorted(
                            {ma.match.category for ma in _arg_enforcement.match_actions}
                        ),
                        note="streaming — arguments already emitted; can't mask retroactively",
                    )

        # Capture DLP trace + build the response-enforcement summary
        # from the per-chunk match actions we accumulated. overall_action
        # is computed from match_actions automatically.
        output_raw = cumulative_raw
        ai_to_service_masked = "".join(masked_provider_parts)
        full_text = "".join(emitted_parts)
        response_enforcement = EnforcementDecision(
            match_actions=accumulated_outbound_actions,
            policy_profile_id=profile.profile_id,
        )

        # 4. Persist DLP summary + basic request metrics. Mirrors the
        # fields populated by GatewayOrchestrator.handle_chat_completion.
        # The dependency-injected `session` is closed by the time this
        # generator runs (route returned StreamingResponse → get_db_session
        # already hit its finally block), so we mint a fresh session via
        # the per-request factory captured at route entry (see #6 note
        # at chat_completions where ``log_session_factory`` is bound).

        overall_action = max(
            input_enforcement.overall_action,
            response_enforcement.overall_action,
        )

        # Admin Secure Trace is request-process memory only. It must never be
        # written to request_logs or emitted to app logs.
        try:
            from app.services.secure_trace_store import (
                SecureTraceRecord,
                secure_trace_store,
            )

            secure_trace_store.put(
                SecureTraceRecord(
                    request_id=ctx.request_id or "",
                    tenant_id=ctx.tenant_id,
                    project_id=ctx.project_id,
                    user_to_service=input_raw,
                    service_to_ai=input_masked,
                    ai_to_service=ai_to_service_masked,
                    service_to_user=full_text,
                    policy_action=overall_action.name.lower(),
                    findings_count=(
                        len(input_enforcement.match_actions)
                        + len(response_enforcement.match_actions)
                    ),
                    detectors=sorted(
                        {
                            ma.match.detector_name
                            for ma in (
                                input_enforcement.match_actions
                                + response_enforcement.match_actions
                            )
                        }
                    ),
                    created_at=time.time(),
                )
            )
        except Exception as exc:
            log.warning("secure_trace_capture_failed", error=str(exc)[:200])
        dlp_categories = sorted(
            {ma.match.category for ma in input_enforcement.match_actions}
            | {ma.match.category for ma in response_enforcement.match_actions}
        )

        # Resolve usage: prefer provider-reported counts (from the trailing
        # usage chunk under stream_options.include_usage); fall back to the
        # heuristic estimator when the provider didn't surface them.
        # T3: reasoning_tokens resolution mirrors the non-stream
        # orchestrator path — provider-reported takes precedence; fall
        # back to a length estimate on the streamed reasoning text
        # (~4 chars/token) for Anthropic which folds thinking into
        # ``output_tokens``. Result is plumbed onto UsageInfo and into
        # the request_log row so the admin dashboard can attribute
        # spend to reasoning separately from regular completion.
        _stream_reasoning_tok = 0
        if provider_usage is not None and provider_usage.reasoning_tokens:
            _stream_reasoning_tok = provider_usage.reasoning_tokens
        elif _reasoning_text_total:
            _stream_reasoning_tok = max(1, len(_reasoning_text_total) // 4)

        if provider_usage is not None:
            from app.services.currency_service import currency_service
            _usd = _estimate_cost(
                route.model_name,
                provider_usage.prompt_tokens,
                provider_usage.completion_tokens,
            )
            _cost_info = currency_service.format_cost(_usd)
            usage = UsageInfo(
                prompt_tokens=provider_usage.prompt_tokens,
                completion_tokens=provider_usage.completion_tokens,
                total_tokens=provider_usage.total_tokens,
                estimated_cost_usd=_usd,
                cost_thb=_cost_info["thb"],
                cost_display=_cost_info["display"],
                reasoning_tokens=_stream_reasoning_tok or None,
            )
        else:
            usage = orchestrator.usage_service.estimate(
                payload=payload,
                response_text=full_text,
                model_name=route.model_name,
            )
            if _stream_reasoning_tok:
                usage = usage.model_copy(update={
                    "reasoning_tokens": _stream_reasoning_tok,
                })

        latency_ms = int((time.perf_counter() - t_start) * 1000)

        # Serialize the streaming fallback attempt log (if any) for
        # the request_log row. The JSONB column on RequestLogORM
        # serializes the list itself — keep it as a Python list here
        # to match the non-streaming orchestrator's contract and
        # avoid double-encoding.
        _stream_fallback_attempts_list: list[dict] | None = (
            _stream_fallback_attempts if _stream_fallback_attempts else None
        )

        log_session = log_session_factory()
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
                    # T3: persist reasoning attribution. Defaults to 0
                    # on non-reasoning streams so the request_log row
                    # is byte-identical to the pre-T3 shape.
                    reasoning_tokens=_stream_reasoning_tok,
                    reasoning_cost=_estimate_reasoning_cost(
                        route.model_name, _stream_reasoning_tok
                    ),
                    fallback_used=_stream_fallback_used,
                    fallback_attempts=_stream_fallback_attempts_list,
                    original_provider=_stream_original_provider,
                    original_model=_stream_original_model,
                    dlp_input_findings=len(input_enforcement.match_actions),
                    dlp_input_action=input_enforcement.overall_action.name.lower(),
                    dlp_output_findings=len(response_enforcement.match_actions),
                    dlp_categories=dlp_categories,
                    router_used=router_used,
                    # 4-stage DLP trace — see orchestrator success path
                    # for column semantics. Streaming captures the
                    # outbound-masked AI text in ``ai_to_service_masked``
                    # (joined ``masked_provider_parts``); ``full_text`` is
                    # the post-restore content the user actually saw.
                    input_raw=safe_input_snapshot(
                        input_raw, input_masked, input_enforcement
                    ),
                    input_masked=safe_input_masked_snapshot(
                        input_masked, input_enforcement
                    ),
                    output_raw=safe_output_raw_snapshot(
                        ai_to_service_masked, response_enforcement
                    ),
                    output_final=safe_output_snapshot(
                        output_raw, full_text, response_enforcement
                    ),
                    input_raw_encrypted=input_raw_encrypted,
                    dlp_findings_json=build_dlp_findings_json(
                        input_enforcement.match_actions,
                        response_enforcement.match_actions,
                        span_map=span_map,
                    ),
                ),
            )
            log_session.commit()

            # Alert evaluation moved OFF the request path (Phase-1 B6) — a
            # periodic background sweep (lifespan _alert_evaluation_sweep)
            # runs AlertEvaluator.evaluate_all instead, so the streaming
            # completion no longer pays the enabled-rules SELECT + the
            # get_by_id read-back + window aggregates on the loop.
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

        # T3: flush the reasoning rolling-window tail. The tail buffer
        # held the trailing MIN_TAIL_CHARS of the last masked window
        # so any PII spanning chunk boundaries would be caught — at
        # stream end we emit whatever remains, masked. No restore /
        # orphan-redact pass here (reasoning is internal monologue;
        # masked tokens stay masked rather than be replaced with
        # inbound PII).
        if _show_reasoning and _reasoning_tail_masked:
            yield _emit_reasoning_chunk(_reasoning_tail_masked)
            _reasoning_tail_masked = ""

        # Defensive analysis_status: completed (T2). If the stream
        # ended without ever transitioning out of the reasoning phase
        # (no content / tool_calls / finish_reason chunk arrived after
        # the thinking blocks closed), close the bracket here so the
        # client never sees a dangling ``started`` without a paired
        # ``completed``. Gated on ``_show_reasoning`` for symmetry
        # with the in-loop emit.
        if _show_reasoning and _reasoning_started and not _reasoning_completed:
            _reasoning_completed = True
            yield _emit_analysis_status("completed")

        # 5. Finish-reason frame + [DONE]. Content deltas were already
        # emitted incrementally above (one per chunk whose masked
        # window grew past MIN_TAIL_CHARS, plus one tail flush).
        # T2: when reasoning was used, attach a minimal ``usage``
        # block to the finish frame so clients can surface the
        # reasoning_tokens count without parsing the request_log.
        # Prefer the provider-reported count (OpenAI o-series
        # ``completion_tokens_details.reasoning_tokens``); fall back
        # to a length-based estimate (~4 chars/token) for providers
        # like Anthropic that fold thinking tokens into ``output_tokens``.
        _final_frame: dict = {
            "id": ctx.request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": final_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": final_finish_reason,
            }],
        }
        if _reasoning_text_total:
            provider_reasoning_tok = (
                provider_usage.reasoning_tokens
                if provider_usage is not None else 0
            )
            estimated = max(1, len(_reasoning_text_total) // 4)
            _final_frame["usage"] = {
                "reasoning_tokens": provider_reasoning_tok or estimated
            }
        yield "data: {}\n\n".format(
            _sse_dumps(_final_frame)
        )
        yield "data: [DONE]\n\n"

    # Headers are sealed before the generator runs; they describe the
    # route we DECIDED to stream from after the Redis pre-check (which
    # is the right answer in the steady state). The rare in-generator
    # fallback (when pre-check misses and the first chunk fails) will
    # serve the right body but leave these headers describing the
    # primary — accept as a documented limitation; the body never lies.
    _stream_headers = {
        "Cache-Control": "no-cache",
        "X-Request-Id": ctx.request_id or "",
        "X-Gateway-Provider": route.provider_name,
        "X-Gateway-Model": route.model_name,
        "X-Gateway-Attempt": str(_stream_attempt_number),
    }
    if _stream_fallback_used:
        _stream_headers["X-Gateway-Fallback"] = "true"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_stream_headers,
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
