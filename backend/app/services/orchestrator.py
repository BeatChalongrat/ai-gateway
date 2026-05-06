"""Gateway orchestrator — coordinates the request lifecycle.

Pipeline:
    1. Resolve project context from API key
    2. DLP scan input messages (detect → classify → evaluate)
    3. If blocked → raise DLPViolationError
    4. If mask/redact → transform messages
    5. Route to provider
    6. Dispatch to provider adapter (with transformed messages)
    7. Estimate usage
    8. DLP scan response (detect → classify → evaluate → mask/block)
    9. Audit
    10. Return response

DLP can be disabled globally via DLP_ENABLED=false in settings.
"""

from __future__ import annotations

import time
import uuid

import structlog

from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey
from app.core.config import settings
from app.core.exceptions import (
    DLPViolationError,
    GuardrailViolationError,
    ModerationViolationError,
    RateLimitExceededError,
)
from app.domain.dlp import DetectionResult, EnforcementAction, EnforcementDecision
from app.domain.entities import RequestContext, RouteDecision
from app.schemas.common import Message, UsageInfo
from app.schemas.public import (
    ChatCompletionsChoice,
    ChatCompletionsRequest,
    ChatCompletionsResponse,
)
from app.api.routes.admin_ai_review import maybe_ai_review_background
from app.domain.observability import (
    build_dlp_findings_json,
    extract_messages_text,
)
from app.observability.metrics import (
    DLP_SCAN_DURATION,
    LLM_PROVIDER_LATENCY,
    REDIS_COMMAND_DURATION,
)
from app.observability.phase_timer import record_phase
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry, DetectorRegistry
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import get_profile
from app.services.project_service import ProjectService
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.routing_service import RoutingService
from app.services.audit_service import AuditService
from app.services.request_log_service import RequestLogService
from app.services.usage_service import UsageService, _estimate_cost
from app.services.rate_limit_service import RateLimitService
from app.services.quota_service import QuotaService
from app.services.budget_service import BudgetService
from app.services.provider_key_service import ProviderKeyService
from app.services.cache_service import CacheService
from app.services.alert_evaluator import AlertEvaluator
from app.services.guardrail_service import GuardrailService
from app.services.keyword_router import KeywordRouter
from app.services.keyword_rule_service import KeywordRuleService
from app.services.moderation_service import ModerationService
from app.services.semantic_router import (
    SemanticRouter,
    SemanticRouterDisabled,
)

logger = structlog.get_logger(__name__)


class GatewayOrchestrator:
    def __init__(self) -> None:
        self.project_service = ProjectService()
        self.detector_registry = get_default_registry()
        self.classifier = SensitivityClassifier()
        self.policy_evaluator = PolicyEvaluator()
        self.transformer = DLPTransformer()
        self.routing_service = RoutingService()
        self.provider_dispatcher = ProviderDispatcher()
        self.audit_service = AuditService()
        self.request_log_service = RequestLogService()
        self.usage_service = UsageService()
        self.rate_limit_service = RateLimitService()
        self.quota_service = QuotaService()
        self.budget_service = BudgetService()
        self.provider_key_service = ProviderKeyService()
        self.cache_service = CacheService()
        self.guardrail_service = GuardrailService()
        self.keyword_router = KeywordRouter()
        self.keyword_rule_service = KeywordRuleService()
        self.moderation_service = ModerationService()
        self.alert_evaluator = AlertEvaluator()
        # Semantic router is flag-gated — the disabled stub adds zero
        # cost and zero dependencies when the feature is off.
        if settings.semantic_router_enabled:
            self.semantic_router = SemanticRouter(
                model_name=settings.semantic_router_model,
                threshold=settings.semantic_router_threshold,
            )
        else:
            self.semantic_router = SemanticRouterDisabled()

    async def handle_chat_completion(
        self,
        auth: ResolvedApiKey,
        payload: ChatCompletionsRequest,
        session: Session | None = None,
    ) -> ChatCompletionsResponse:
        request_id = f"req_{uuid.uuid4().hex}"
        t_start = time.perf_counter()

        # 1. Resolve project context from authenticated identity.
        # ``policy_profile_override`` is the optional per-request field
        # on ChatCompletionsRequest; project_service applies it (or
        # raises 403 on escalation attempts) before any DLP runs.
        with record_phase("auth"):
            ctx = self.project_service.resolve_project_context(
                session,
                auth,
                request_id=request_id,
                policy_profile_override=payload.policy_profile_id,
            )

        log = logger.bind(
            request_id=request_id,
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
        )
        log.info("request_start", model=payload.model, stream=payload.stream)

        # DLP trace snapshot 1/4: raw user input, captured once before any
        # DLP / guardrail / moderation pass so blocked requests still
        # surface what was sent. Snapshots 2-4 are captured at their
        # respective pipeline stages further down.
        input_raw = extract_messages_text(payload.messages)

        # 1b. Rate limit check (Redis sliding window — per key, project, tenant)
        with record_phase("rate_limit"), REDIS_COMMAND_DURATION.labels(
            operation="rate_limit_pipeline"
        ).time():
            rl = self.rate_limit_service.check_all(
                key_id=str(auth.key_id),
                project_id=str(ctx.project_id),
                tenant_id=str(ctx.tenant_id),
            )
        if not rl["allowed"]:
            retry_after = max(1, rl["reset_at"] - int(time.time()))
            raise RateLimitExceededError(
                f"Rate limit exceeded for {rl['scope']} ({rl['limit']} req/min).",
                details={
                    "scope": rl["scope"],
                    "limit": rl["limit"],
                    "remaining": rl["remaining"],
                    "reset_at": rl["reset_at"],
                },
                headers={"Retry-After": str(retry_after)},
            )

        # 1c. Quota + budget checks (DB-backed)
        if session is not None:
            self.quota_service.check_request_quota(session, ctx.project_id)
            self.budget_service.check_budget(session, ctx.project_id)

        # 1d. Portal credit check — no-op for admin/tenant keys.
        # Runs BEFORE DLP so a suspended/zero-balance portal user doesn't
        # waste CPU on entity detection + provider calls. See
        # app.services.gateway.portal_credit_check for the policy.
        if session is not None:
            from app.services.gateway.portal_credit_check import (
                check_portal_credit,
            )
            check_portal_credit(session, str(auth.key_id))

        # Resolve policy profile: project-level → settings default → "default"
        profile_id = ctx.policy_profile_id or settings.dlp_default_profile
        profile = get_profile(profile_id)

        # 2. DLP scan input (skip entirely if DLP disabled).
        # Per-tenant NER gate: the shared detector_registry always
        # contains PresidioDetector (when presidio is installed), but
        # we drop it for this request when the tenant has NER off.
        # ``without()`` returns a shallow copy so the shared singleton
        # is never mutated.
        active_registry = self.detector_registry
        if not ctx.ner_enabled:
            active_registry = active_registry.without("presidio_ner")

        t_dlp = time.perf_counter()
        input_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)
        if settings.dlp_enabled:
            # Split DLP into regex + NER sub-phases so Round A vs B deltas
            # are readable. ``active_registry`` is the regex-only set when
            # ``ner_enabled`` is False on the tenant.
            with record_phase("dlp_regex"), DLP_SCAN_DURATION.labels(
                phase="regex"
            ).time():
                input_detection = active_registry.scan_messages(
                    payload.messages,
                    direction="input",
                    scan_system_messages=profile.scan_system_messages,
                )
            if "presidio_ner" in active_registry:
                # The registry combines detectors in a single pass, so we
                # can't separate NER timing perfectly post-hoc. Instead,
                # we record a zero-width ``dlp_ner`` observation when NER
                # is active so the phase shows up in the report — the
                # real NER cost is captured inside the ``dlp_regex``
                # bucket in Round A (NER off) vs Round B (NER on) delta.
                DLP_SCAN_DURATION.labels(phase="ner").observe(0.0)
                with record_phase("dlp_ner"):
                    pass
            input_enforcement = self._scan_and_evaluate(input_detection, profile)
        dlp_ms = int((time.perf_counter() - t_dlp) * 1000)
        log.info(
            "dlp_scan_complete",
            direction="input",
            action=input_enforcement.overall_action.name.lower(),
            findings=len(input_enforcement.match_actions),
            dlp_ms=dlp_ms,
        )

        # 3. Block if input policy says so
        if input_enforcement.is_blocked:
            # Persist BLOCKED row + commit BEFORE raising. The
            # dep-injected session at api/deps.py:33-43 calls
            # session.rollback() in its except branch when the
            # violation propagates, which would otherwise discard
            # the row. Mirrors the working pattern from
            # public_chat.py (streaming moderation).
            if session is not None:
                from app.domain.observability import RequestRecord, RequestStatus
                try:
                    self.request_log_service.persist(
                        session,
                        RequestRecord(
                            request_id=request_id,
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
                            input_raw=input_raw,
                            dlp_findings_json=build_dlp_findings_json(
                                input_enforcement.match_actions,
                            ),
                        ),
                    )
                    session.commit()
                except Exception as exc:
                    log.warning("dlp_block_log_persist_failed", error=str(exc))
            self.audit_service.record_blocked_request(
                ctx=ctx,
                enforcement=input_enforcement,
                direction="input",
            )
            raise DLPViolationError(
                "Request blocked by DLP policy.",
                details={
                    "policy_profile": input_enforcement.policy_profile_id,
                    "reasons": input_enforcement.reasons,
                    "direction": "input",
                },
            )

        # 4. Transform messages if needed (mask/redact)
        # mask_messages returns indexed tokens ([EMAIL_0], [EMAIL_1], …), a
        # span_map so we can restore them in the provider's response, and the
        # per-detector counters as they stand after this call. The counters
        # are threaded into any subsequent mask_text() so outbound indices
        # continue where inbound left off — preventing an outbound [EMAIL_0]
        # from clashing with an inbound [EMAIL_0] (which would make restore
        # splice the user's own PII over the AI-generated value).
        messages_to_send = list(payload.messages)
        span_map: dict[str, str] = {}
        inbound_counters: dict[str, int] = {}
        if input_enforcement.requires_transformation:
            messages_to_send, span_map, inbound_counters = self.transformer.mask_messages(
                payload.messages, input_enforcement.match_actions
            )

        # DLP trace snapshot 2/4: messages as they will be sent to the
        # provider — already mask-substituted with indexed tokens. Equal
        # to ``input_raw`` when no PII was detected (the modal collapses
        # that case to a "no changes" hint).
        input_masked = extract_messages_text(messages_to_send)

        # 4b. Guardrail check — regex blocklist on (post-DLP-transform) user/system content
        dict_messages = [m.model_dump() for m in messages_to_send]
        with record_phase("guardrails"):
            guardrail_result = self.guardrail_service.check(dict_messages)
        if not guardrail_result.passed:
            log.info(
                "guardrail_blocked",
                rule=guardrail_result.rule_name,
                reason=guardrail_result.reason,
            )
            if session is not None:
                from app.domain.observability import RequestRecord, RequestStatus
                try:
                    self.request_log_service.persist(
                        session,
                        RequestRecord(
                            request_id=request_id,
                            tenant_id=ctx.tenant_id,
                            project_id=ctx.project_id,
                            provider="",
                            model=payload.model,
                            status=RequestStatus.BLOCKED,
                            policy_action="guardrail",
                            policy_profile_id=profile.profile_id,
                            latency_ms=int((time.perf_counter() - t_start) * 1000),
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            estimated_cost_usd=0.0,
                            error_code="guardrail_blocked",
                            input_raw=input_raw,
                            input_masked=input_masked,
                            dlp_findings_json=build_dlp_findings_json(
                                input_enforcement.match_actions,
                                span_map=span_map,
                            ),
                        ),
                    )
                except Exception as exc:
                    log.warning("guardrail_log_persist_failed", error=str(exc))
            raise GuardrailViolationError(
                guardrail_result.reason,
                details={"rule": guardrail_result.rule_name},
            )

        # 4b2. Moderation (OpenAI /v1/moderations) — opt-in per policy profile.
        # Uses the OpenAI key directly (moderation is OpenAI-only), independent of
        # the target provider for this request. Fails open on any error.
        if profile.moderation_enabled:
            moderation_input = " ".join(
                m.get("content", "") for m in dict_messages
                if isinstance(m.get("content"), str)
            )
            openai_key = None
            if session is not None:
                openai_key = self.provider_key_service.get_api_key(session, "openai")
            openai_key = openai_key or settings.openai_api_key
            if openai_key and moderation_input:
                t_mod = time.perf_counter()
                mod_result = await self.moderation_service.check(
                    moderation_input, openai_key
                )
                log.info(
                    "moderation_scan_complete",
                    flagged=mod_result.flagged,
                    categories=list(mod_result.categories.keys()),
                    error=mod_result.error,
                    moderation_ms=int((time.perf_counter() - t_mod) * 1000),
                )
                if mod_result.flagged:
                    if session is not None:
                        from app.domain.observability import RequestRecord, RequestStatus
                        try:
                            self.request_log_service.persist(
                                session,
                                RequestRecord(
                                    request_id=request_id,
                                    tenant_id=ctx.tenant_id,
                                    project_id=ctx.project_id,
                                    provider="",
                                    model=payload.model,
                                    status=RequestStatus.BLOCKED,
                                    policy_action="moderation",
                                    policy_profile_id=profile.profile_id,
                                    latency_ms=int((time.perf_counter() - t_start) * 1000),
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
                        except Exception as exc:
                            log.warning("moderation_log_persist_failed", error=str(exc))
                    raise ModerationViolationError(
                        "Content flagged by moderation.",
                        details={
                            "categories": list(mod_result.categories.keys()),
                            "scores": mod_result.scores,
                        },
                    )

        # 4c. Routing hint chain: semantic > keyword (DB + default) > service.
        # Extracted into _apply_router_chain so the streaming path can
        # reuse the same logic on DLP-masked messages. Mutates
        # payload.model in place when a router fires.
        with record_phase("routing"):
            router_used, _matched_rule = self._apply_router_chain(
                ctx=ctx,
                payload=payload,
                dict_messages=dict_messages,
                session=session,
                log=log,
            )

            # 5. Route
            route = self.routing_service.select_route(
                ctx=ctx,
                payload=payload,
                policy_decision=input_enforcement,
            )
        log.info(
            "route_selected",
            provider=route.provider_name,
            model=route.model_name,
            constrained=route.constrained_by_policy,
        )

        # 6. Resolve provider API key (DB → env var fallback)
        provider_api_key = None
        if session is not None:
            provider_api_key = self.provider_key_service.get_api_key(session, route.provider_name)
        if not provider_api_key:
            from app.core.exceptions import ProviderUnavailableError
            raise ProviderUnavailableError(
                f"No API key configured for provider '{route.provider_name}'.",
                details={"provider": route.provider_name},
            )

        # 6b. Response cache check (only for non-streaming, pre-DLP-transform messages)
        # Cache keyed on (resolved model, original user messages, project).
        # We cache the already-DLP-scanned response, so a hit is safe to return as-is.
        cache_messages = [m.model_dump() for m in payload.messages]
        cache_hit = None
        if not payload.stream:
            with record_phase("cache"), REDIS_COMMAND_DURATION.labels(
                operation="cache_get"
            ).time():
                cache_hit = self.cache_service.get(
                    model=route.model_name,
                    messages=cache_messages,
                    project_id=ctx.project_id,
                )
        if cache_hit is not None:
            total_ms = int((time.perf_counter() - t_start) * 1000)
            log.info(
                "cache_hit",
                provider=route.provider_name,
                model=route.model_name,
                total_ms=total_ms,
            )
            # Mark and persist as a cache-hit request
            if session is not None:
                from app.domain.observability import RequestRecord, RequestStatus
                # Extract the cached assistant text for the trace.
                # cache_hit is the dict form of ChatCompletionsResponse.
                cached_text = ""
                try:
                    choices = cache_hit.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        cached_text = msg.get("content", "") or ""
                except Exception:
                    cached_text = ""
                try:
                    self.request_log_service.persist(
                        session,
                        RequestRecord(
                            request_id=request_id,
                            tenant_id=ctx.tenant_id,
                            project_id=ctx.project_id,
                            provider=route.provider_name,
                            model=route.model_name,
                            status=RequestStatus.SUCCESS,
                            policy_action=cache_hit.get("policy_action", "allow"),
                            policy_profile_id=profile.profile_id,
                            latency_ms=total_ms,
                            prompt_tokens=cache_hit.get("usage", {}).get("prompt_tokens", 0),
                            completion_tokens=cache_hit.get("usage", {}).get("completion_tokens", 0),
                            total_tokens=cache_hit.get("usage", {}).get("total_tokens", 0),
                            estimated_cost_usd=0.0,  # cached responses cost nothing
                            input_raw=input_raw,
                            input_masked=input_masked,
                            output_raw=cached_text,
                            output_final=cached_text,
                            dlp_findings_json=build_dlp_findings_json(
                                input_enforcement.match_actions,
                                span_map=span_map,
                            ),
                        ),
                    )
                except Exception as exc:
                    log.warning("cache_hit_log_persist_failed", error=str(exc))
            cache_hit["cached"] = True
            cache_hit["id"] = request_id
            return ChatCompletionsResponse(**cache_hit)

        # 7. Dispatch to provider with (potentially transformed) messages
        t_provider = time.perf_counter()
        with record_phase("provider"), LLM_PROVIDER_LATENCY.labels(
            provider=route.provider_name
        ).time():
            provider_response = await self.provider_dispatcher.dispatch(
                route=route,
                payload=payload,
                messages=messages_to_send,
                api_key=provider_api_key,
            )
        provider_ms = int((time.perf_counter() - t_provider) * 1000)
        log.info(
            "provider_complete",
            provider=route.provider_name,
            model=provider_response.model,
            finish_reason=provider_response.finish_reason,
            provider_ms=provider_ms,
        )

        # 7. Estimate usage (falls back to heuristic when provider doesn't report usage)
        if provider_response.usage:
            usage = UsageInfo(
                prompt_tokens=provider_response.usage.prompt_tokens,
                completion_tokens=provider_response.usage.completion_tokens,
                total_tokens=provider_response.usage.total_tokens,
                estimated_cost_usd=_estimate_cost(
                    route.model_name, provider_response.usage.total_tokens
                ),
            )
        else:
            usage = self.usage_service.estimate(
                payload=payload,
                response_text=provider_response.content,
                model_name=route.model_name,
            )

        # 8. DLP scan response THEN restore (order matters).
        #
        # We scan the *masked* provider output first so that:
        #   (a) Indexed tokens like [EMAIL_0] never match a detector regex,
        #       so echoes of the user's own PII are ignored — no double-mask.
        #   (b) NEW PII that the model invented or recalled (e.g. the model
        #       writing a fresh example email) is detected and masked
        #       BEFORE restore. These new findings are not in span_map and
        #       therefore survive the restore step.
        # After scan+mask, we run restore() to replace the indexed input
        # tokens with the user's original values. The result: user sees
        # their own input PII echoed back, but any new PII the model
        # produced is masked.
        #
        # streaming DLP restore handled in public_chat.py
        response_content = provider_response.content
        # DLP trace snapshot 3/4: provider response BEFORE any output DLP
        # work — captures exactly what the model returned, even if a
        # later mask/restore step rewrites it.
        output_raw = response_content
        response_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)

        if settings.dlp_enabled and settings.dlp_scan_responses and profile.scan_responses:
            with record_phase("dlp_regex"), DLP_SCAN_DURATION.labels(
                phase="regex"
            ).time():
                response_detection = self._scan_text_as_result(
                    response_content,
                    direction="output",
                    ner_enabled=ctx.ner_enabled,
                )
            response_enforcement = self._scan_and_evaluate(
                response_detection, profile
            )

            if response_enforcement.is_blocked:
                # Persist BLOCKED row + commit BEFORE raising — see
                # rationale in the inbound block path above. Outbound
                # block has the full provider context so we capture
                # input_raw/input_masked/output_raw and the usage
                # counters that ran before the block fired.
                if session is not None:
                    from app.domain.observability import RequestRecord, RequestStatus
                    try:
                        self.request_log_service.persist(
                            session,
                            RequestRecord(
                                request_id=request_id,
                                tenant_id=ctx.tenant_id,
                                project_id=ctx.project_id,
                                provider=route.provider_name,
                                model=route.model_name,
                                status=RequestStatus.BLOCKED,
                                policy_action="block",
                                policy_profile_id=profile.profile_id,
                                latency_ms=int((time.perf_counter() - t_start) * 1000),
                                prompt_tokens=usage.prompt_tokens if usage else 0,
                                completion_tokens=usage.completion_tokens if usage else 0,
                                total_tokens=usage.total_tokens if usage else 0,
                                estimated_cost_usd=usage.estimated_cost_usd if usage else 0.0,
                                error_code="dlp_violation",
                                input_raw=input_raw,
                                input_masked=input_masked,
                                output_raw=output_raw,
                                dlp_findings_json=build_dlp_findings_json(
                                    input_enforcement.match_actions,
                                    response_enforcement.match_actions,
                                    span_map=span_map,
                                ),
                            ),
                        )
                        session.commit()
                    except Exception as exc:
                        log.warning("dlp_block_log_persist_failed", error=str(exc))
                self.audit_service.record_blocked_request(
                    ctx=ctx,
                    enforcement=response_enforcement,
                    direction="output",
                )
                raise DLPViolationError(
                    "Response blocked by DLP policy — provider response contained sensitive data.",
                    details={
                        "policy_profile": response_enforcement.policy_profile_id,
                        "reasons": response_enforcement.reasons,
                        "direction": "output",
                    },
                )

            if response_enforcement.requires_transformation:
                # Use mask_text (indexed tokens) so new PII introduced by
                # the provider is masked with [EMAIL_N], [CREDENTIAL_N], …
                # — consistent with the inbound masking scheme. These new
                # tokens are NOT added to span_map (they have no original
                # to restore to), so they survive the restore step below.
                #
                # start_counters=inbound_counters is load-bearing: without
                # it, outbound would restart the index at 0 and re-emit
                # [EMAIL_0] that collides with an inbound [EMAIL_0] in
                # span_map. The subsequent restore() would then overwrite
                # the AI's newly-generated value with the user's original
                # PII. See transformer.mask_text docstring.
                response_content, _, _ = self.transformer.mask_text(
                    response_content,
                    response_enforcement.match_actions,
                    start_counters=inbound_counters,
                )

        # 8b. Restore indexed input tokens AFTER output scan+mask.
        # See the rationale block in step 8 above for why order matters.
        if span_map:
            response_content = self.transformer.restore(response_content, span_map)

        # 9. Determine overall policy action (highest across input + output)
        overall_action = max(
            input_enforcement.overall_action,
            response_enforcement.overall_action,
        )

        # Build direction-prefixed reasons from *actionable* matches only.
        # We skip ALLOW matches (which generate "no matching rule" noise when
        # a category isn't covered by the active profile) and the placeholder
        # "No sensitive content detected." message.
        def _actionable_reasons(enforcement, label: str) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for ma in enforcement.match_actions:
                if ma.action == EnforcementAction.ALLOW:
                    continue
                if ma.reason_message in seen:
                    continue
                seen.add(ma.reason_message)
                out.append(f"{label}: {ma.reason_message}")
            return out

        all_reasons: list[str] = (
            _actionable_reasons(input_enforcement, "Input")
            + _actionable_reasons(response_enforcement, "Output")
        )
        if not all_reasons:
            all_reasons = ["No sensitive content detected."]

        # 10. Audit success + persist request log
        total_ms = int((time.perf_counter() - t_start) * 1000)
        self.audit_service.record_success(
            ctx=ctx,
            route=route,
            enforcement=input_enforcement,
            usage=usage,
            input_enforcement=input_enforcement,
            output_enforcement=response_enforcement,
        )

        # 10b. Portal credit deduction — no-op for admin/tenant keys.
        # Writes a ``credit_transactions`` row (type=usage) and clamps
        # the balance at 0.
        if session is not None and usage is not None:
            from app.services.gateway.portal_credit_check import (
                deduct_portal_credit,
            )
            deduct_portal_credit(
                session,
                str(auth.key_id),
                int(getattr(usage, "total_tokens", 0) or 0),
                request_id,
            )

        # Persist to request_logs table for /admin/requests
        if session is not None:
            from app.domain.observability import RequestRecord, RequestStatus
            dlp_categories = sorted(
                {ma.match.category for ma in input_enforcement.match_actions}
                | {ma.match.category for ma in response_enforcement.match_actions}
            )
            record = RequestRecord(
                request_id=request_id,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                provider=route.provider_name,
                model=route.model_name,
                status=RequestStatus.SUCCESS,
                policy_action=overall_action.name.lower(),
                policy_profile_id=profile.profile_id,
                latency_ms=total_ms,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
                dlp_input_findings=len(input_enforcement.match_actions),
                dlp_input_action=input_enforcement.overall_action.name.lower(),
                dlp_output_findings=len(response_enforcement.match_actions),
                dlp_categories=dlp_categories,
                router_used=router_used,
                # DLP trace — snapshot 4/4 is the post-restore content
                # the user actually saw. ``output_raw`` was captured
                # above before output mask + restore.
                input_raw=input_raw,
                input_masked=input_masked,
                output_raw=output_raw,
                output_final=response_content,
                dlp_findings_json=build_dlp_findings_json(
                    input_enforcement.match_actions,
                    response_enforcement.match_actions,
                    span_map=span_map,
                ),
            )
            try:
                self.request_log_service.persist(session, record)
            except Exception as exc:
                log.warning("request_log_persist_failed", error=str(exc))

            # Alert evaluation — fire any matching rule against the just-
            # persisted row. Must never break the request response, so
            # any error (DB, webhook, etc.) is logged and swallowed.
            try:
                persisted_row = self.request_log_service.get_by_id(
                    session, request_id
                )
                if persisted_row is not None:
                    await self.alert_evaluator.evaluate_after_request(
                        session, persisted_row
                    )
            except Exception as exc:
                log.warning("alert_evaluator_failed", error=str(exc))

        log.info(
            "request_complete",
            status="success",
            policy_action=overall_action.name.lower(),
            provider=route.provider_name,
            model=route.model_name,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            total_ms=total_ms,
            provider_ms=provider_ms,
        )

        # 11. Build response (assembly phase — small but measured).
        # Wrapping the Pydantic model construction captures the
        # serialization cost that shows up on tight-loop load — it's
        # usually ~0.1ms but non-zero, which is the whole reason the
        # phase table needs its own bucket.
        with record_phase("response_assembly"):
            response = ChatCompletionsResponse(
                id=request_id,
                model=route.model_name,
                provider=route.provider_name,
                choices=[
                    ChatCompletionsChoice(
                        index=0,
                        message=Message(
                            role="assistant",
                            content=response_content,
                        ),
                    )
                ],
                usage=usage,
                policy_action=overall_action.name.lower(),
                policy_reasons=all_reasons,
            )

        # 12. Response cache store — only safe, non-streaming, non-blocked responses
        if (
            not payload.stream
            and overall_action.name.lower() not in ("block",)
            and not input_enforcement.is_blocked
            and not response_enforcement.is_blocked
        ):
            self.cache_service.set(
                model=route.model_name,
                messages=cache_messages,
                project_id=ctx.project_id,
                response=response.model_dump(),
            )

        # 13. Realtime AI review hook (fire-and-forget). Scans the raw
        # provider response (before DLP restore) for PII the outbound
        # pipeline missed. ``output_raw`` was captured at step 8 before
        # any mask/restore work.
        maybe_ai_review_background(
            request_id,
            output_raw,
            api_key_id=str(auth.key_id) if auth else None,
        )

        return response

    def _apply_router_chain(
        self,
        ctx: RequestContext,
        payload: ChatCompletionsRequest,
        dict_messages: list[dict],
        session: Session | None,
        log,
    ) -> tuple[str, str | None]:
        """Run semantic → keyword → service router chain.

        Mutates ``payload.model`` in place when a router fires. Returns
        ``(router_used, matched_rule_name)`` where ``router_used`` is
        one of ``"semantic"``, ``"keyword"``, or ``"service"``.

        Expected to run AFTER DLP masking so ``dict_messages`` contains
        indexed tokens in place of raw PII — the semantic router's
        embedding call must never see unmasked sensitive content.
        """
        _meta_allowed = payload.metadata.get("allowed_providers")
        allowed_providers = (
            frozenset(_meta_allowed) if _meta_allowed else None
        )

        router_used: str = "service"
        sem_model, sem_intent, sem_confidence = (
            self.semantic_router.get_model_hint(
                dict_messages, allowed_providers=allowed_providers
            )
        )
        matched_rule: str | None = None
        hinted_model: str = payload.model

        if sem_model is not None:
            # High-confidence semantic match — overrides everything else.
            router_used = "semantic"
            hinted_model = sem_model
            payload.model = sem_model
        else:
            # Load DB keyword rules for this tenant/project and merge
            # them with the hardcoded DEFAULT_RULES.
            db_rules = []
            if session is not None:
                try:
                    db_rules = (
                        self.keyword_rule_service.get_rules_for_request(
                            session, ctx.tenant_id, ctx.project_id
                        )
                    )
                except Exception as exc:
                    log.warning(
                        "keyword_rule_load_failed", error=str(exc)
                    )
            router_inst = (
                KeywordRouter(db_rules=db_rules)
                if db_rules
                else self.keyword_router
            )
            hinted_model, matched_rule = router_inst.get_model_hint(
                messages=dict_messages,
                requested_model=payload.model,
                allowed_providers=allowed_providers,
            )
            if matched_rule is not None and hinted_model != payload.model:
                router_used = "keyword"
                payload.model = hinted_model

        log.info(
            "routing_decision",
            router=router_used,
            intent=sem_intent,
            confidence=sem_confidence,
            matched_rule=matched_rule,
            model=payload.model,
        )

        return router_used, matched_rule

    def resolve_streaming_context(
        self,
        auth: ResolvedApiKey,
        payload: ChatCompletionsRequest,
        session: Session | None = None,
    ) -> RequestContext:
        """Resolve project context for a streaming request.

        Route selection is deferred to the caller so that the router
        chain can run against DLP-masked messages BEFORE
        ``routing_service.select_route`` picks a provider. The caller
        (``public_chat.py``) is responsible for:
        1. Running DLP masking on ``payload.messages``.
        2. Calling ``_apply_router_chain`` on the masked messages.
        3. Calling ``routing_service.select_route`` to pick provider.
        4. Checking ``adapter.supports_streaming`` and falling back to
           the non-streaming path if the selected adapter can't stream.
        """
        request_id = f"req_{uuid.uuid4().hex}"
        return self.project_service.resolve_project_context(
            session,
            auth,
            request_id=request_id,
            policy_profile_override=payload.policy_profile_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_and_evaluate(
        self,
        detection_result: DetectionResult,
        profile,
    ) -> EnforcementDecision:
        """Run classifier + evaluator on a detection result."""
        if detection_result.has_sensitive_content:
            detection_result.matches = self.classifier.classify(
                detection_result.matches
            )
        return self.policy_evaluator.evaluate(detection_result, profile)

    def _scan_text_as_result(
        self,
        text: str,
        direction: str = "output",
        *,
        ner_enabled: bool = True,
    ) -> DetectionResult:
        """Scan a single text string and wrap in a DetectionResult.

        Pass ``ner_enabled=False`` to drop PresidioDetector from this
        scan — used by the outbound response scan when the request's
        tenant has NER off.
        """
        registry = self.detector_registry
        if not ner_enabled:
            registry = registry.without("presidio_ner")
        matches = registry.scan_text(text)
        return DetectionResult(
            matches=matches,
            scanned_direction=direction,
            scan_target="response_content",
        )
