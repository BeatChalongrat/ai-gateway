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
from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, check_tenant_access, get_db_session, get_resolved_api_key
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
from app.services.policy.profile_service import get_runtime_profile
from app.services.policy.profiles import get_profile as get_runtime_profile_fallback
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
    response: Response,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: Session = Depends(get_db_session),
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
    ctx = orchestrator.resolve_streaming_context(
        auth=auth, payload=payload, session=session
    )

    log = logger.bind(
        request_id=ctx.request_id,
        tenant_id=ctx.tenant_id,
        project_id=ctx.project_id,
    )

    profile_id = ctx.policy_profile_id or settings.dlp_default_profile
    profile = (
        get_runtime_profile(session, profile_id)
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

    # Deterministic-tokenization key — mirrors the non-streaming
    # branch in ``orchestrator.handle_chat_completion``. Opt-in via
    # ``payload.metadata.conversation_id``; without it the masking
    # call falls back to legacy counter mode so existing streaming
    # tests keep their fixed token expectations.
    conversation_key_stream = None
    _conv_id_for_key = payload.metadata.get("conversation_id")
    if _conv_id_for_key and settings.dlp_enabled:
        from app.services.dlp.transformer import _make_conversation_key
        conversation_key_stream = _make_conversation_key(
            settings.dlp_hmac_key, _conv_id_for_key
        )

    if settings.dlp_enabled:
        # Per-tenant NER gate — identical to orchestrator.handle_chat_completion.
        # The orchestrator caches two pre-built registries (one with
        # Presidio, one with the ``thai_name_no_prefix`` regex
        # fallback) and we pick by ``ctx.ner_enabled``.
        input_registry = orchestrator._select_registry(ctx.ner_enabled)
        detection = input_registry.scan_messages(
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
                    orchestrator.request_log_service.persist(
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
                    session.commit()
                except Exception as exc:
                    log.warning("dlp_block_log_persist_failed", error=str(exc))
            orchestrator.audit_service.record_blocked_request(
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
        conversation_vault.extend(conversation_id, span_map)

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
    restore_allowed_tokens: set[str] = set()
    if span_map:
        value_to_tokens: dict[str, set[str]] = {}
        for token, original in span_map.items():
            value_to_tokens.setdefault(original, set()).add(token)
        for ma in input_enforcement.match_actions:
            if output_actions.get(ma.match.category) != EnforcementAction.RESTORE:
                continue
            restore_allowed_tokens.update(
                value_to_tokens.get(ma.match.matched_text, set())
            )

    # Inject system prompt explaining DLP placeholders to the model.
    # Gated on the inbound DLP findings count so clean prompts go
    # straight to the provider without the DLP-context preamble.
    if input_enforcement.match_actions:
        from app.services.dlp_prompt import inject_dlp_system_prompt
        messages_to_send = inject_dlp_system_prompt(messages_to_send)
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
                orchestrator.request_log_service.persist(
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
                session.commit()
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

    # 1d. Router chain — semantic > keyword > service, on DLP-masked
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

    if await _is_unhealthy(route.provider_name):
        fb_runner = orchestrator._get_fallback_orchestrator()
        fb_candidate = fb_runner.select_streaming_candidate(
            original_provider=_stream_original_provider,
            original_model=_stream_original_model,
            session=session,
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
                )
                # Mirror the non-streaming branch: outbound tokens
                # go into the vault (for audit/cross-turn lookups)
                # but are NEVER merged into ``span_map`` — restore
                # must not unmask hallucinated values.
                if (
                    conversation_id
                    and outbound_window_map
                    and settings.dlp_conversation_vault_enabled
                ):
                    from app.services.dlp.conversation_vault import (
                        conversation_vault as _outbound_vault,
                    )
                    _outbound_vault.extend(
                        conversation_id, outbound_window_map
                    )
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
                            if emit_text:
                                emitted_parts.append(emit_text)
                                yield _emit_chunk(emit_text)
                                first_chunk_yielded = True
                        else:
                            tail_masked = masked_window

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
                _lookup_session = log_session_factory()
                try:
                    fb_candidate = fb_runner.select_streaming_candidate(
                        original_provider=_stream_original_provider,
                        original_model=_stream_original_model,
                        session=_lookup_session,
                        skip_providers=skip_providers,
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
            # Orphan-redact pass — same idea as the non-streaming
            # path's ``redact_orphan_tokens`` call: any ``[CAT_N]``
            # surviving in the tail that isn't in the combined
            # inbound + outbound maps is something the model
            # invented from the placeholder system prompt's example
            # token. Streaming can only catch orphans on the tail
            # (earlier chunks are already on the wire), but that's
            # where they tend to cluster — the model writes its
            # "Hope this helps, [YOUR_NAME]" template at the end.
            from app.services.dlp.transformer import redact_orphan_tokens
            _stream_all_tokens = {**span_map, **_stream_outbound_span_map}
            final_emit = redact_orphan_tokens(final_emit, _stream_all_tokens)
            if final_emit:
                emitted_parts.append(final_emit)
                yield _emit_chunk(final_emit)

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
            )
        else:
            usage = orchestrator.usage_service.estimate(
                payload=payload,
                response_text=full_text,
                model_name=route.model_name,
            )

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
        }, ensure_ascii=False))
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
