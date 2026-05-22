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

import re
import time
import uuid

import structlog

from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey
from app.core.config import settings
from app.core.dlp_audit_crypto import DlpAuditCryptoError, encrypt as encrypt_audit_blob
from app.core.exceptions import (
    DLPViolationError,
    GuardrailViolationError,
    ModerationViolationError,
    RateLimitExceededError,
)
from app.domain.dlp import DetectionResult, EnforcementAction, EnforcementDecision
from app.domain.entities import RequestContext, RouteDecision
from app.domain.policy import PolicyProfile
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
    safe_input_masked_snapshot,
    safe_input_snapshot,
    safe_output_raw_snapshot,
    safe_output_snapshot,
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
from app.services.policy.profile_service import get_runtime_profile
from app.services.policy.profiles import get_profile as get_runtime_profile_fallback
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
from app.services.gateway.fallback_orchestrator import (
    FallbackOrchestrator,
    clear_provider_health,
    is_fallback_eligible,
    mark_provider_unhealthy,
)

logger = structlog.get_logger(__name__)

# Counter-form ``<rN>`` token matcher. Used to recover the highest
# numeric index from a vault snapshot so a fresh mask call starts at
# ``max + 1`` instead of colliding with vault-owned slots. The token
# emitter only uses digits in counter mode; legacy HMAC suffixes are
# ignored here on purpose (they can't influence the next counter).
_COUNTER_TOKEN_RE = re.compile(r"^<r(\d+)>$")


def _profile_uses_restore(profile: PolicyProfile | None) -> bool:
    # Belt-and-suspenders cache gate. The match-level guards at the
    # cache read/write sites already skip cache whenever the request's
    # DLP findings produced any MatchAction. This helper adds a
    # profile-level skip so a future detector miss on a RESTORE profile
    # can never seed the shared cache with one user's restored PII —
    # the cache key (project + model + messages hash) doesn't include
    # the API key, so a same-project repeat with different PII values
    # but the same prompt template would otherwise return user A's
    # data to user B.
    if profile is None:
        return False
    if profile.category_actions and EnforcementAction.RESTORE in profile.category_actions.values():
        return True
    if profile.category_actions_output and EnforcementAction.RESTORE in profile.category_actions_output.values():
        return True
    return False


class GatewayOrchestrator:
    def __init__(self) -> None:
        self.project_service = ProjectService()
        # Two cached registries — one per NER axis. Building both at
        # startup means each request just picks the right pre-built
        # instance via ``_select_registry(ctx.ner_enabled)`` instead of
        # re-running detector registration (and any spaCy model loads
        # behind ``presidio_ner``) on the hot path. The orchestrator
        # is stateless wrt these registries; ``DetectorRegistry`` is
        # safe to share across concurrent requests.
        self._registry_ner_on = get_default_registry(ner_enabled=True)
        self._registry_ner_off = get_default_registry(ner_enabled=False)
        self.classifier = SensitivityClassifier()
        self.policy_evaluator = PolicyEvaluator()
        self.transformer = DLPTransformer()
        self.routing_service = RoutingService()
        self.provider_dispatcher = ProviderDispatcher()
        # Phase-2 cross-provider fallback. Stateless wrapper around the
        # dispatcher; triggered from ``handle_chat_completion`` when the
        # primary dispatch raises a fallback-eligible GatewayError.
        self.fallback_orchestrator: FallbackOrchestrator | None = None
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

    def _get_fallback_orchestrator(self) -> FallbackOrchestrator:
        """Lazy singleton tied to the request orchestrator's services.

        Built on first use so import-time ordering in test fixtures —
        which monkeypatch ``ProviderDispatcher`` / ``ProviderKeyService``
        — still affects the dispatcher / key service the fallback sees.
        """
        if self.fallback_orchestrator is None:
            self.fallback_orchestrator = FallbackOrchestrator(
                dispatcher=self.provider_dispatcher,
                key_service=self.provider_key_service,
            )
        return self.fallback_orchestrator

    async def handle_chat_completion(
        self,
        auth: ResolvedApiKey,
        payload: ChatCompletionsRequest,
        session: Session | None = None,
        out_meta: dict | None = None,
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

        # Inject project-level routing config (migration 044). Caller-
        # supplied ``payload.metadata`` keys WIN — project config is the
        # default, not a hard constraint. Runs BEFORE the request_start
        # log so the logged ``model`` reflects the project default when
        # it has overridden the request's ``"auto"``.
        if session is not None:
            self._inject_project_routing_config(
                session=session,
                project_id=ctx.project_id,
                payload=payload,
                log=log,
            )

        log.info("request_start", model=payload.model, stream=payload.stream)

        # DLP trace snapshot 1/4: raw user input, captured once before any
        # DLP / guardrail / moderation pass so blocked requests still
        # surface what was sent. Snapshots 2-4 are captured at their
        # respective pipeline stages further down.
        input_raw = extract_messages_text(payload.messages)

        # Safe Trace audit channel: encrypt the raw prompt once here so
        # every downstream persist site (success / cache hit / guardrail
        # block / moderation block / DLP block) can include the same
        # ciphertext without re-running the KDF + GCM. ``input_raw``
        # itself is still clobbered to ``[MASKED_BY_DLP]`` by the safe
        # snapshot helpers — this is the recoverable side channel that
        # /admin/requests/{id}/trace decrypts for ADMIN callers.
        try:
            input_raw_encrypted = encrypt_audit_blob(input_raw or "")
        except DlpAuditCryptoError as exc:
            log.warning("dlp_audit_encrypt_failed", error=str(exc)[:200])
            input_raw_encrypted = None

        # 1b. Rate limit check (Redis sliding window — per key, project, tenant)
        with record_phase("rate_limit"), REDIS_COMMAND_DURATION.labels(
            operation="rate_limit_pipeline"
        ).time():
            rl = await self.rate_limit_service.check_all_async(
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

        # 2. DLP scan input (skip entirely if DLP disabled).
        # Per-tenant NER gate: pick the right pre-built registry from
        # the cached pair on the orchestrator. The NER-off variant has
        # the regex ``thai_name_no_prefix`` fallback in place of
        # Presidio so bare Thai names are still covered.
        active_registry = self._select_registry(ctx.ner_enabled)

        t_dlp = time.perf_counter()
        input_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)
        if settings.dlp_enabled:
            # Split DLP into regex + NER sub-phases so Round A vs B deltas
            # are readable. ``active_registry`` is the regex-only set when
            # ``ner_enabled`` is False on the tenant.
            with record_phase("dlp_regex"), DLP_SCAN_DURATION.labels(
                phase="regex"
            ).time():
                # Phase-3c engine selector. ``_run_dlp_scan`` routes
                # to the Rust sidecar / shadow comparison / legacy
                # in-process registry per ``dlp_engine`` setting.
                input_detection = await self._run_dlp_scan(
                    active_registry,
                    payload.messages,
                    direction="input",
                    scan_system_messages=profile.scan_system_messages,
                    ctx=ctx,
                    session=session,
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
            self.audit_service.record_blocked_request(
                ctx=ctx,
                enforcement=input_enforcement,
                direction="input",
            )
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

        # 4. Transform messages if needed (mask/redact)
        # mask_messages returns indexed tokens (``<r0>``, ``<r1>``, …),
        # a span_map so we can restore them in the provider's response,
        # and the counter dict as it stands after this call. The counter
        # is threaded into any subsequent mask_text() so outbound indices
        # continue where inbound left off — preventing an outbound
        # ``<r0>`` from clashing with an inbound ``<r0>``.
        # Cross-turn determinism: when the caller threads
        # ``conversation_id`` through metadata AND the vault is
        # enabled, load any tokens minted on prior turns and pass
        # them down as ``vault_reverse_map`` (value → prior token).
        # The transformer reuses the prior ``<rN>`` for any value
        # already in the map so ``alice@x.com`` keeps the same
        # placeholder across turns. ``start_counters`` is primed past
        # the highest vault index so freshly minted tokens skip over
        # slots the vault already owns.
        conversation_id = payload.metadata.get("conversation_id")
        conversation_key = None  # legacy parameter, retained for signature compat
        vault_reverse_map: dict[str, str] = {}
        inbound_start_counters: dict[str, int] | None = None
        if (
            conversation_id
            and settings.dlp_enabled
            and settings.dlp_conversation_vault_enabled
        ):
            from app.services.dlp.conversation_vault import conversation_vault
            existing_vault = await conversation_vault.get_all_tokens_async(
                conversation_id
            )
            if existing_vault:
                vault_reverse_map = {
                    value: token for token, value in existing_vault.items()
                }
                max_idx = -1
                for token in existing_vault.keys():
                    m = _COUNTER_TOKEN_RE.match(token)
                    if m:
                        max_idx = max(max_idx, int(m.group(1)))
                if max_idx >= 0:
                    inbound_start_counters = {"__global__": max_idx + 1}

        messages_to_send = list(payload.messages)
        span_map: dict[str, str] = {}
        inbound_counters: dict[str, int] = {}
        if input_enforcement.requires_transformation:
            messages_to_send, span_map, inbound_counters = self.transformer.mask_messages(
                payload.messages,
                input_enforcement.match_actions,
                conversation_key=conversation_key,
                start_counters=inbound_start_counters,
                vault_reverse_map=vault_reverse_map or None,
            )

        # Cross-turn DLP vault: persist this turn's span_map to Redis
        # so the next turn's ``restore`` can resolve orphaned echoes
        # of these tokens. Pure best-effort — Redis outage means no
        # carry-over, never a failed request. Uses the async variant
        # because the orchestrator runs inside the FastAPI event loop;
        # the sync ``run_sync`` bridge would fail with a different-loop
        # error here.
        if conversation_id and span_map and settings.dlp_conversation_vault_enabled:
            from app.services.dlp.conversation_vault import conversation_vault
            await conversation_vault.extend_async(conversation_id, span_map)

        # 4a. Inject a system prompt explaining DLP placeholders so the
        # model doesn't refuse masked messages. Gated on the inbound
        # DLP findings count — a clean request (no findings) reaches
        # the model with its messages untouched, so a "What is 2+2?"
        # prompt doesn't pay the DLP-context tokens.
        if input_enforcement.match_actions:
            from app.services.dlp_prompt import inject_dlp_system_prompt
            messages_to_send = inject_dlp_system_prompt(messages_to_send)
            log.info(
                "dlp_system_prompt_injected",
                findings=len(input_enforcement.match_actions),
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
                            # ``policy_action='block'`` is the enum-aligned
                            # value the dashboard (admin-ui/app/requests/
                            # page.tsx:190) and the dlp_block_spike alert
                            # rule (alert_evaluator.py:158) filter on. The
                            # *kind* of block (guardrail vs DLP vs
                            # moderation) is preserved in ``error_code``.
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
                                    # ``policy_action='block'`` aligns with
                                    # the dashboard / alert filters; *kind*
                                    # is in ``error_code``. See guardrail
                                    # block above for the rationale.
                                    policy_action="block",
                                    policy_profile_id=profile.profile_id,
                                    latency_ms=int((time.perf_counter() - t_start) * 1000),
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
                            # Commit BEFORE raise — see guardrail block.
                            session.commit()
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

        # 6b. Response cache check — keyed on the POST-mask messages so two
        # users in the same project hitting the same prompt template
        # (different PII values, same surrounding text) share a single
        # cache entry. The stored value is the LLM's masked response
        # (``<rN>`` tokens, no PII at rest in Redis). On hit, we re-run
        # restore + orphan redaction with THIS user's span_map, so each
        # caller gets their own original PII back. Streaming still
        # bypasses the cache (no good way to replay an SSE stream).
        cache_messages = [m.model_dump() for m in messages_to_send]
        # Defense-in-depth: when the profile uses RESTORE, scope the
        # cache key per api_key_id so a detector miss (raw PII leaking
        # into the "masked" response) cannot be replayed to a different
        # user hitting the same prompt template. Non-RESTORE requests
        # keep sharing the per-project cache across users.
        cache_api_key_id = (
            str(auth.key_id)
            if (_profile_uses_restore(profile) and auth is not None)
            else None
        )
        cache_hit = None
        if not payload.stream:
            with record_phase("cache"), REDIS_COMMAND_DURATION.labels(
                operation="cache_get"
            ).time():
                cache_hit = await self.cache_service.get_async(
                    model=route.model_name,
                    messages=cache_messages,
                    project_id=ctx.project_id,
                    api_key_id=cache_api_key_id,
                )
        if cache_hit is not None:
            total_ms = int((time.perf_counter() - t_start) * 1000)
            log.info(
                "cache_hit",
                provider=route.provider_name,
                model=route.model_name,
                total_ms=total_ms,
            )
            cached_text = ""
            try:
                choices = cache_hit.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    cached_text = msg.get("content", "") or ""
            except Exception:
                cached_text = ""
            # Capture the masked snapshot BEFORE restore so the
            # ``output_raw`` (AI → Service) trace column reflects what
            # the AI actually produced for the cached request — the
            # same invariant the fresh-LLM path holds via
            # ``ai_to_service_masked``. Without this, both
            # ``output_raw`` and ``output_final`` would carry the
            # restored text and the Admin UI's 4-stage trace would
            # show identical PII in stages 3 and 4 on every cache hit.
            cached_text_masked = cached_text
            if cached_text:
                # Same restore + orphan-redact pipeline that runs on a
                # fresh provider response (sections 8b + final orphan
                # pass). ``restore_allowed_tokens`` is computed from the
                # CURRENT user's span_map, so user A's cached <rN>
                # placeholders resolve to user B's actual PII on B's hit.
                # Tokens that are not in B's span_map (e.g. outbound
                # placeholders the LLM minted on A's original call)
                # become ``[REDACTED]`` — we never replay one user's
                # hallucinated PII to another.
                restore_allowed_tokens = self._compute_restore_allowed_tokens(
                    profile, span_map, input_enforcement
                )
                if restore_allowed_tokens:
                    cached_text = self.transformer.restore(
                        cached_text,
                        span_map,
                        allowed_tokens=restore_allowed_tokens,
                    )
                from app.services.dlp.transformer import (
                    redact_orphan_tokens as _redact_orphan_tokens,
                )
                _sanitised = _redact_orphan_tokens(cached_text, span_map)
                if _sanitised != cached_text:
                    log.info(
                        "dlp_orphan_redacted_cached",
                        before_len=len(cached_text),
                        after_len=len(_sanitised),
                    )
                cached_text = _sanitised
                try:
                    cache_hit["choices"][0]["message"]["content"] = cached_text
                except Exception:
                    pass
            # Mark and persist as a cache-hit request
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
                            status=RequestStatus.SUCCESS,
                            policy_action=cache_hit.get("policy_action", "allow"),
                            policy_profile_id=profile.profile_id,
                            latency_ms=total_ms,
                            prompt_tokens=cache_hit.get("usage", {}).get("prompt_tokens", 0),
                            completion_tokens=cache_hit.get("usage", {}).get("completion_tokens", 0),
                            total_tokens=cache_hit.get("usage", {}).get("total_tokens", 0),
                            estimated_cost_usd=0.0,  # cached responses cost nothing
                            dlp_input_findings=len(input_enforcement.match_actions),
                            dlp_input_action=input_enforcement.overall_action.name.lower(),
                            input_raw=safe_input_snapshot(
                                input_raw, input_masked, input_enforcement
                            ),
                            input_masked=safe_input_masked_snapshot(
                                input_masked, input_enforcement
                            ),
                            input_raw_encrypted=input_raw_encrypted,
                            # Stage 3 (AI → Service) uses the cached
                            # value AS IT SAT IN REDIS — masked, with
                            # ``<rN>`` tokens — so the admin trace
                            # matches the fresh-LLM path's
                            # ``ai_to_service_masked`` invariant.
                            # Stage 4 (Service → User) is the
                            # post-restore text the caller actually
                            # received.
                            output_raw=safe_output_raw_snapshot(
                                cached_text_masked, input_enforcement
                            ),
                            output_final=safe_output_snapshot(
                                cached_text_masked, cached_text, input_enforcement
                            ),
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

        # 7. Dispatch to provider with (potentially transformed) messages.
        #
        # Phase-2 fallback: a primary dispatch that fails with a
        # fallback-eligible GatewayError hands off to the
        # FallbackOrchestrator, which walks the cross-provider chain
        # from the model's tier. Success returns the alternate vendor's
        # ProviderResponse; ``provider`` / ``model`` are rewritten so
        # everything downstream (DLP scan, request_log row, response
        # body) sees the actual answering provider. The originally
        # routed pair is kept in ``original_provider`` / ``original_model``
        # for audit + dashboard aggregation.
        original_provider = route.provider_name
        original_model = route.model_name
        fallback_result = None
        t_provider = time.perf_counter()
        # Layer-1 fallback key resolver. ``dispatch_with_fallback``
        # calls this for each fallback hop's provider (the primary
        # key was already resolved above). Returns None when no key is
        # configured so the hop is skipped without burning a slot.
        def _resolve_fallback_key(provider_name: str) -> str | None:
            if session is None:
                return None
            return self.provider_key_service.get_api_key(
                session, provider_name
            )

        try:
            with record_phase("provider"), LLM_PROVIDER_LATENCY.labels(
                provider=route.provider_name
            ).time():
                (
                    provider_response,
                    used_provider,
                    used_model,
                ) = await self.provider_dispatcher.dispatch_with_fallback(
                    route=route,
                    payload=payload,
                    messages=messages_to_send,
                    api_key=provider_api_key,
                    key_resolver=_resolve_fallback_key,
                )
            # If Layer-1 fallback fired, rewrite the route so the rest
            # of the pipeline (DLP scan, request_log row, response
            # headers) reports the vendor that actually answered.
            if (
                used_provider != route.provider_name
                or used_model != route.model_name
            ):
                log.info(
                    "layer1_fallback_used",
                    original_provider=route.provider_name,
                    original_model=route.model_name,
                    used_provider=used_provider,
                    used_model=used_model,
                )
                route.provider_name = used_provider
                route.model_name = used_model
            # Primary succeeded — clear any stale health marker so the
            # streaming pre-check stops skipping this provider.
            await clear_provider_health(original_provider)
        except Exception as exc:  # noqa: BLE001
            from app.core.exceptions import GatewayError as _GwErr

            if isinstance(exc, _GwErr) and is_fallback_eligible(exc):
                # Tag the primary as unhealthy so the next streaming
                # request bypasses it via the Redis pre-check.
                await mark_provider_unhealthy(original_provider)
                log.warning(
                    "primary_dispatch_failed_attempting_fallback",
                    provider=route.provider_name,
                    model=route.model_name,
                    error_code=getattr(exc, "code", None),
                )
                fallback = self._get_fallback_orchestrator()
                from app.observability.metrics import (
                    LLM_PROVIDER_LATENCY as _LLM_LAT,
                )
                with record_phase("provider_fallback"):
                    fallback_result = await fallback.run(
                        original_provider=original_provider,
                        original_model=original_model,
                        request=payload,
                        messages=messages_to_send,
                        session=session,
                        ctx=ctx,
                    )
                provider_response = fallback_result.response
                # Rewrite the route so DLP scan, request_log row, and
                # response body all report the vendor that actually
                # answered. ``RouteDecision`` is frozen-style but its
                # attributes are settable (it's a non-frozen dataclass).
                route.provider_name = fallback_result.provider_used
                route.model_name = fallback_result.model_used
                _LLM_LAT.labels(
                    provider=fallback_result.provider_used
                ).observe(
                    sum(a.latency_ms for a in fallback_result.attempts) / 1000.0
                    + 0.0
                )
            else:
                raise
        provider_ms = int((time.perf_counter() - t_provider) * 1000)
        log.info(
            "provider_complete",
            provider=route.provider_name,
            model=provider_response.model,
            finish_reason=provider_response.finish_reason,
            provider_ms=provider_ms,
            fallback_used=fallback_result is not None,
            fallback_attempt=(
                fallback_result.attempt_number if fallback_result else 1
            ),
        )

        # 7. Estimate usage (falls back to heuristic when provider doesn't report usage)
        if provider_response.usage:
            from app.services.currency_service import currency_service
            _usd = _estimate_cost(
                route.model_name,
                provider_response.usage.prompt_tokens,
                provider_response.usage.completion_tokens,
            )
            _cost_info = currency_service.format_cost(_usd)
            usage = UsageInfo(
                prompt_tokens=provider_response.usage.prompt_tokens,
                completion_tokens=provider_response.usage.completion_tokens,
                total_tokens=provider_response.usage.total_tokens,
                estimated_cost_usd=_usd,
                cost_thb=_cost_info["thb"],
                cost_display=_cost_info["display"],
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
        # Output sanitizer pass — runs BEFORE the outbound DLP scan +
        # the inbound restore. Some models (gpt-4o-mini in particular)
        # ignore the system prompt's "do not invent bracketed tokens"
        # rule and emit ``[[CAT_N]]`` instead of echoing the original
        # ``[CAT_N]``. Left alone, those reach the user as a literal
        # ``[[NAME_1]]`` because restore() has no span_map binding for
        # the invented name. The sanitizer either unwraps the over-
        # bracketed form (when the inner token is a real binding) or
        # replaces the whole thing with ``[REDACTED]``.
        from app.services.dlp.transformer import sanitize_hallucinated_brackets
        response_content = sanitize_hallucinated_brackets(
            response_content, span_map
        )
        ai_to_service_masked = response_content
        response_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)
        # Outbound mask span_maps — initialised outside the
        # ``dlp_scan_responses`` gate so the post-restore orphan
        # redactor at the end of section 8 can always merge them
        # into the protected-tokens set even when scan-responses is
        # disabled. Sections 8 / 8c populate these conditionally.
        outbound_span_map: dict[str, str] = {}
        _final_outbound_span_map: dict[str, str] = {}

        if settings.dlp_enabled and settings.dlp_scan_responses and profile.scan_responses:
            # First scan the provider's raw response with RESTORE treated as
            # MASK. This catches any brand-new sensitive value the provider
            # invented before we restore our own request-scoped tokens.
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
            with record_phase("dlp_regex"), DLP_SCAN_DURATION.labels(
                phase="regex"
            ).time():
                # Outbound scan runs with ``ner_enabled=False`` regardless
                # of ``ctx.ner_enabled``. Presidio's English-only spaCy
                # model produces arbitrary spans on Thai AI replies (no
                # Thai tokeniser shipped), which then get masked to
                # ``[THAI_NAME_N]`` tokens that have no entry in the
                # inbound ``span_map`` and survive restore as user-visible
                # mid-word splices ("ขอบ[THAI_NAME_0]"). The Thai regex
                # detector (``thai_name.py``, title-prefix-gated) still
                # runs and catches real Thai names in the response.
                # Trade-off: loses English-NER outbound coverage too;
                # acceptable because outbound restore is regex-driven and
                # the inbound NER already masked anything the AI is
                # supposed to echo.
                response_detection = self._scan_text_as_result(
                    response_content,
                    direction="output",
                    ner_enabled=False,
                )
            response_enforcement = self._scan_and_evaluate(
                response_detection, pre_restore_profile
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
                                input_raw=safe_input_snapshot(
                                    input_raw, input_masked, input_enforcement
                                ),
                                input_masked=safe_input_masked_snapshot(
                                    input_masked, input_enforcement
                                ),
                                input_raw_encrypted=input_raw_encrypted,
                                output_raw="[BLOCKED_BY_DLP]",
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
                        "action": "block",
                        "policy_profile": response_enforcement.policy_profile_id,
                        "categories": sorted(
                            {ma.match.category.upper() for ma in response_enforcement.match_actions}
                        ),
                        "findings_count": len(response_enforcement.match_actions),
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
                response_content, outbound_span_map, _ = self.transformer.mask_text(
                    response_content,
                    response_enforcement.match_actions,
                    start_counters=inbound_counters,
                    conversation_key=conversation_key,
                    vault_reverse_map=vault_reverse_map or None,
                )
                # Outbound tokens are intentionally NOT merged into
                # the inbound ``span_map``: the restore step below
                # must not unmask a hallucinated value the AI
                # invented. They DO go into the vault so an audit
                # query can still resolve them — and the deterministic
                # mode means the same hallucinated value gets the
                # same token if echoed in a later turn.
                if (
                    conversation_id
                    and outbound_span_map
                    and settings.dlp_conversation_vault_enabled
                ):
                    from app.services.dlp.conversation_vault import (
                        conversation_vault,
                    )
                    await conversation_vault.extend_async(
                        conversation_id, outbound_span_map
                    )

            ai_to_service_masked = response_content

        # Snapshot for the response cache: the AI's reply with all
        # outbound-discovered PII masked but inbound ``<rN>`` tokens
        # still intact (restore hasn't run yet). This is what we want
        # at rest in Redis — no raw PII, and re-restorable per user.
        # Captured outside the ``dlp_scan_responses`` gate above so the
        # snapshot is always defined (it just equals
        # ``sanitize_hallucinated_brackets``'s output when outbound scan
        # is disabled, which is still safe to cache).
        cache_payload_content = response_content

        # 8b. Restore indexed input tokens AFTER output scan+mask.
        # See the rationale block in step 8 above for why order matters.
        # ``vault_resolver`` is wired only when a ``conversation_id``
        # was supplied — without one, there's no cross-turn store to
        # consult and ``restore`` falls back to the single-turn orphan
        # warning path. The lambda is created here (not closed over a
        # mutating variable) so the resolver always sees the same id.
        # Product requirement: the provider sees only placeholders, while the
        # caller gets their own values restored when the outbound policy says
        # RESTORE. The span_map is request-scoped; only tokens minted from this
        # request can be restored here.
        restore_allowed_tokens = self._compute_restore_allowed_tokens(
            profile, span_map, input_enforcement
        )
        if restore_allowed_tokens:
            response_content = self.transformer.restore(
                response_content,
                span_map,
                allowed_tokens=restore_allowed_tokens,
            )

        # 8c. Scan the final restored response again. RESTORE findings are
        # allowed to reach the user, but MASK/BLOCK findings are still enforced
        # and nothing raw from this final text is persisted to request_logs.
        if settings.dlp_enabled and settings.dlp_scan_responses and profile.scan_responses:
            with record_phase("dlp_regex"), DLP_SCAN_DURATION.labels(
                phase="regex"
            ).time():
                # Section 8c — final outbound scan after restore. Same
                # NER=False rationale as Section 8 above.
                final_response_detection = self._scan_text_as_result(
                    response_content,
                    direction="output",
                    ner_enabled=False,
                )
            response_enforcement = self._scan_and_evaluate(
                final_response_detection, profile
            )
            if response_enforcement.requires_transformation:
                response_content, _final_outbound_span_map, _ = self.transformer.mask_text(
                    response_content,
                    response_enforcement.match_actions,
                    start_counters=inbound_counters,
                    conversation_key=conversation_key,
                    vault_reverse_map=vault_reverse_map or None,
                )

        # P3 — orphan-token sanitiser. After ALL legitimate masking
        # (sections 8 + 8c) and restore (8b), any ``<rN>`` left in
        # ``response_content`` that isn't in the inbound ``span_map``
        # is either (a) a token the model invented from the placeholder
        # grammar in the system prompt, or (b) an outbound mask token
        # the gateway minted for LLM-generated PII. Both surface to the
        # user as a meaningless ``<rN>`` if untouched. Replacing them
        # with ``[REDACTED]`` keeps the masking guarantee (the value is
        # still not in the user-facing text) while reading naturally.
        # The same protection set is used on the cache-hit path so both
        # routes give identical output. Streaming uses the same rule in
        # public_chat.py via its own redact pass.
        from app.services.dlp.transformer import redact_orphan_tokens
        _before_redact = response_content
        response_content = redact_orphan_tokens(response_content, span_map)
        if _before_redact != response_content:
            redacted_tokens = sorted(
                set(re.findall(r"<r[A-Za-z0-9_\-]+>", _before_redact))
                - set(span_map.keys())
            )
            log.info(
                "dlp_orphan_redacted",
                before_len=len(_before_redact),
                after_len=len(response_content),
                orphan_tokens=redacted_tokens[:10],
                orphan_count=len(redacted_tokens),
            )

        # 9. Determine overall policy action (highest across input + output)
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
                    request_id=request_id,
                    tenant_id=ctx.tenant_id,
                    project_id=ctx.project_id,
                    user_to_service=input_raw,
                    service_to_ai=input_masked,
                    ai_to_service=ai_to_service_masked,
                    service_to_user=response_content,
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
            # JSONB column handles serialization — pass the list, not
            # a json.dumps string (would double-encode).
            fallback_attempts_list: list[dict] | None = None
            if fallback_result and fallback_result.attempts:
                fallback_attempts_list = [
                    a.to_dict() for a in fallback_result.attempts
                ]

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
                fallback_used=fallback_result is not None,
                fallback_attempts=fallback_attempts_list,
                original_provider=original_provider,
                original_model=original_model,
                dlp_input_findings=len(input_enforcement.match_actions),
                dlp_input_action=input_enforcement.overall_action.name.lower(),
                dlp_output_findings=len(response_enforcement.match_actions),
                dlp_categories=dlp_categories,
                router_used=router_used,
                # 4-stage DLP trace:
                #   1. input_raw    (User → Service)  — placeholder (raw has PII)
                #   2. input_masked (Service → AI)    — actual masked text, tokens only
                #   3. output_raw   (AI → Service)    — AI response AFTER outbound mask;
                #                                       the variable ``output_raw`` is the
                #                                       *pre-mask* AI text and may still
                #                                       carry hallucinated PII, so we
                #                                       persist ``ai_to_service_masked``
                #                                       (post-outbound-mask) instead.
                #   4. output_final (Service → User)  — placeholder (post-restore has PII)
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
                    output_raw, response_content, response_enforcement
                ),
                input_raw_encrypted=input_raw_encrypted,
                dlp_findings_json=build_dlp_findings_json(
                    input_enforcement.match_actions,
                    response_enforcement.match_actions,
                    span_map=span_map,
                ),
            )
            # Fire-and-forget enqueue. When the background worker is
            # running (production) the record is queued and inserted in
            # a batch by the worker on its own session. When the worker
            # is not running (pytest, pre-startup, post-shutdown) the
            # queue falls back to a synchronous persist on the caller's
            # session — preserving the legacy contract exactly.
            from app.services import request_log_queue
            try:
                request_log_queue.enqueue(record, fallback_session=session)
            except Exception as exc:
                log.warning("request_log_enqueue_failed", error=str(exc))

            # Alert evaluation — uses an in-memory ORM built from the
            # record so the path does not depend on the queue having
            # flushed yet. The evaluator only reads tenant_id /
            # project_id from this object (see
            # ``AlertEvaluator._scope_matches``); all analytical work
            # runs against the live ``session`` and the persisted history.
            try:
                in_memory_row = request_log_queue.build_request_log_orm(record)
                await self.alert_evaluator.evaluate_after_request(
                    session, in_memory_row
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

        # 10c. Populate out_meta for response headers. The caller
        # (public_chat.chat_completions) reads this dict and writes
        # ``X-Gateway-Provider`` / ``X-Gateway-Model`` /
        # ``X-Gateway-Attempt`` and, when ``fallback_used``,
        # ``X-Gateway-Fallback: true``. ``out_meta`` stays None on the
        # streaming path so we have to gate.
        if out_meta is not None:
            out_meta["provider_used"] = route.provider_name
            out_meta["model_used"] = route.model_name
            out_meta["fallback_used"] = fallback_result is not None
            out_meta["attempt_number"] = (
                fallback_result.attempt_number if fallback_result else 1
            )
            out_meta["original_provider"] = original_provider
            out_meta["original_model"] = original_model

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
                            # Function-calling passthrough. None when
                            # the model returned plain text; the route
                            # is decorated with
                            # response_model_exclude_none=True so the
                            # field doesn't appear on the wire then.
                            tool_calls=provider_response.tool_calls,
                        ),
                    )
                ],
                usage=usage,
                policy_action=overall_action.name.lower(),
                policy_reasons=all_reasons,
            )

        # 12. Response cache store — store the MASKED response (the
        # ``ai_to_service_masked`` snapshot captured before section 8b).
        # On hit, we re-run restore + orphan redact with the calling
        # user's span_map, so two users in the same project hitting the
        # same prompt template each get their own PII back. Streaming
        # and blocked responses are still excluded.
        if (
            not payload.stream
            and overall_action.name.lower() not in ("block",)
            and not input_enforcement.is_blocked
            and not response_enforcement.is_blocked
        ):
            cache_response = response.model_dump()
            try:
                cache_response["choices"][0]["message"]["content"] = cache_payload_content
            except (IndexError, KeyError, TypeError):
                # Defensive: if the response shape ever changes, fall
                # back to the restored content rather than skipping the
                # cache write outright. Cross-user safety is preserved
                # by the orphan-redact pass on cache hit.
                pass
            await self.cache_service.set_async(
                model=route.model_name,
                messages=cache_messages,
                project_id=ctx.project_id,
                response=cache_response,
                api_key_id=cache_api_key_id,
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

    def _inject_project_routing_config(
        self,
        *,
        session: Session,
        project_id: str,
        payload: ChatCompletionsRequest,
        log,
    ) -> None:
        """Merge per-project routing config into ``payload.metadata``.

        Reads ``default_model``, ``preferred_provider``,
        ``allowed_providers``, ``blocked_models`` from the ``projects``
        row (migration 044). Caller-supplied metadata keys are NOT
        overwritten — project config is a per-tenant default, not a
        hard constraint; the per-request body always wins so SDK
        callers retain full control.

        ``payload.model`` is rewritten to the project default ONLY when
        the caller left it at the schema default ``"auto"``. Any
        explicit model in the body (``"gpt-4o"``, ``"claude-..."`` …)
        is preserved.

        Safe to call when ``project_id`` is missing or the project
        record has no routing fields set — fast path returns immediately
        with no mutation. Logs at debug; never raises.
        """
        if not project_id:
            return
        try:
            from app.models.project import ProjectORM

            project = session.get(ProjectORM, project_id)
        except Exception as exc:
            log.warning("project_routing_config_load_failed", error=str(exc))
            return
        if project is None:
            return

        applied: list[str] = []

        # default_model: only if caller left payload.model at "auto".
        if project.default_model and payload.model == "auto":
            payload.model = project.default_model
            applied.append("default_model")

        # The three metadata keys: caller value wins; project fills the
        # gap. Empty arrays count as "set" and DO override the project
        # value (caller deliberately cleared the constraint).
        if (
            project.preferred_provider
            and "preferred_provider" not in payload.metadata
        ):
            payload.metadata["preferred_provider"] = project.preferred_provider
            applied.append("preferred_provider")

        if (
            project.allowed_providers
            and "allowed_providers" not in payload.metadata
        ):
            payload.metadata["allowed_providers"] = list(project.allowed_providers)
            applied.append("allowed_providers")

        if (
            project.blocked_models
            and "blocked_models" not in payload.metadata
        ):
            payload.metadata["blocked_models"] = list(project.blocked_models)
            applied.append("blocked_models")

        if applied:
            log.info("project_routing_config_applied", fields=applied)

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

        # Load DB keyword rules up-front so both the project-scoped
        # pre-check and the post-semantic full pass see the same rule
        # list. Failures are logged but never break routing — the
        # default-rule fallback still gives reasonable behaviour.
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

        router_used: str = "service"
        matched_rule: str | None = None
        hinted_model: str = payload.model
        sem_intent: str = "general"
        sem_confidence: float = 0.0

        # Step 1: project-scoped keyword rules outrank everything else,
        # including a high-confidence semantic match. A tenant who has
        # configured per-project routing has made an explicit, narrow
        # choice that the embedding classifier must not override.
        project_hint, project_rule = router_inst.get_project_scoped_hint(
            messages=dict_messages,
            requested_model=payload.model,
            allowed_providers=allowed_providers,
        )
        if project_rule is not None and project_hint != payload.model:
            router_used = "keyword_project"
            matched_rule = project_rule
            hinted_model = project_hint
            payload.model = project_hint
        else:
            # Step 2: semantic router (existing precedence).
            sem_model, sem_intent, sem_confidence = (
                self.semantic_router.get_model_hint(
                    dict_messages, allowed_providers=allowed_providers
                )
            )
            if sem_model is not None:
                router_used = "semantic"
                hinted_model = sem_model
                payload.model = sem_model
            else:
                # Step 3: full keyword pass — tenant + global DB rules
                # plus DEFAULT_RULES. Project rules already evaluated
                # above; re-running is harmless because they would
                # win the same way and the conditional check below
                # only flips ``router_used`` when the model actually
                # changes.
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

        Project-level routing config (migration 044) is injected here
        so the streaming path picks up the same defaults the non-
        streaming path does. Caller metadata still wins.
        """
        request_id = f"req_{uuid.uuid4().hex}"
        ctx = self.project_service.resolve_project_context(
            session,
            auth,
            request_id=request_id,
            policy_profile_override=payload.policy_profile_id,
        )
        if session is not None:
            stream_log = logger.bind(
                request_id=request_id,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
            )
            self._inject_project_routing_config(
                session=session,
                project_id=ctx.project_id,
                payload=payload,
                log=stream_log,
            )
        return ctx

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_registry(self, ner_enabled: bool) -> DetectorRegistry:
        """Pick the cached registry matching the request's NER axis.

        ``ner_enabled=True`` → registry with ``presidio_ner`` (when
        installed) and no ``thai_name_no_prefix`` fallback.
        ``ner_enabled=False`` → registry with the regex fallback in
        place of Presidio.

        Background paths that have no ``ctx`` (e.g. AI-review) should
        call this with ``ner_enabled=True`` to keep parity with the
        pre-fix behaviour."""
        return self._registry_ner_on if ner_enabled else self._registry_ner_off

    def _select_dlp_engine(
        self,
        ctx: RequestContext,
        session: Session | None,
    ) -> str:
        """Resolve the effective DLP engine for this request.

        Per-tenant ``tenants.dlp_engine`` override beats the global
        ``settings.dlp_engine`` when it isn't ``inherit``. Migration
        043 ensures every row has a value (default ``inherit``).
        """
        if session is None:
            return settings.dlp_engine
        try:
            from app.models.tenant import TenantORM

            tenant = session.get(TenantORM, ctx.tenant_id)
            tenant_engine = (
                getattr(tenant, "dlp_engine", "inherit") if tenant else "inherit"
            )
        except Exception:  # noqa: BLE001
            return settings.dlp_engine
        if tenant_engine and tenant_engine != "inherit":
            return tenant_engine
        return settings.dlp_engine

    async def _run_dlp_scan(
        self,
        registry,
        messages,
        *,
        direction: str,
        scan_system_messages: bool,
        ctx: RequestContext,
        session: Session | None,
    ):
        """Route the DLP scan through the selected engine.

        - ``python`` (default) → in-process registry call.
        - ``rust`` → gRPC call to ``dlp-sidecar``. Any failure falls
          back to Python so a sidecar incident never downgrades
          coverage.
        - ``shadow`` → run BOTH in parallel, use Python on the hot
          path, log ``dlp_shadow_diff`` events.
        """
        engine = self._select_dlp_engine(ctx, session)

        def _python_scan():
            return registry.scan_messages(
                messages,
                direction=direction,
                scan_system_messages=scan_system_messages,
            )

        if engine == "rust":
            from app.services.dlp.rust_dlp_client import RustDlpClient
            try:
                client = RustDlpClient.instance()
                rust_result = await client.scan_messages(
                    messages,
                    direction=direction,
                    scan_system_messages=scan_system_messages,
                    ner_enabled=getattr(ctx, "ner_enabled", True),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dlp_rust_engine_exception", error=str(exc)[:200]
                )
                rust_result = None
            if rust_result is not None:
                return rust_result
            logger.warning("dlp_rust_fallback_to_python")
            return _python_scan()

        if engine == "shadow":
            import asyncio as _asyncio
            from app.services.dlp.rust_dlp_client import RustDlpClient
            rust_task = None
            try:
                client = RustDlpClient.instance()
                rust_task = _asyncio.create_task(
                    client.scan_messages(
                        messages,
                        direction=direction,
                        scan_system_messages=scan_system_messages,
                        ner_enabled=getattr(ctx, "ner_enabled", True),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dlp_shadow_rust_dispatch_failed", error=str(exc)[:200]
                )

            python_result = _python_scan()

            if rust_task is not None:
                try:
                    rust_result = await _asyncio.wait_for(rust_task, timeout=2.0)
                    if rust_result is not None:
                        self._log_shadow_diff(python_result, rust_result, ctx)
                except Exception:  # noqa: BLE001
                    logger.info("dlp_shadow_rust_timed_out_or_errored")
            return python_result

        # Default: python
        return _python_scan()

    def _log_shadow_diff(
        self,
        python_result,
        rust_result,
        ctx: RequestContext,
    ) -> None:
        """Structured diff between Python and Rust DLP results.

        Cutover gate: 7 consecutive days of ``diff_count = 0`` on
        production traffic before flipping ``DLP_ENGINE`` from
        ``shadow`` to ``rust``.
        """
        python_set = {
            (m.detector_name, m.start, m.end) for m in python_result.matches
        }
        rust_set = {
            (m.detector_name, m.start, m.end) for m in rust_result.matches
        }
        only_python = python_set - rust_set
        only_rust = rust_set - python_set
        logger.info(
            "dlp_shadow_diff",
            python_count=len(python_result.matches),
            rust_count=len(rust_result.matches),
            diff_count=len(only_python) + len(only_rust),
            only_python=[list(t) for t in list(only_python)[:5]],
            only_rust=[list(t) for t in list(only_rust)[:5]],
            request_id=getattr(ctx, "request_id", None),
            tenant_id=getattr(ctx, "tenant_id", None),
        )

    @staticmethod
    def _compute_restore_allowed_tokens(
        profile: PolicyProfile,
        span_map: dict[str, str],
        input_enforcement: EnforcementDecision,
    ) -> set[str]:
        # Token allow-list for the outbound restore step. A mask token
        # may only be restored to its original value if (a) it was minted
        # from this request's inbound span_map and (b) the active output
        # policy maps its detector category to RESTORE. The same logic
        # runs both at section-8b (fresh provider response) and on cache
        # hit (replayed masked response).
        if not span_map:
            return set()
        output_actions = (
            profile.category_actions_output
            if profile.category_actions_output is not None
            else profile.category_actions
        )
        value_to_tokens: dict[str, set[str]] = {}
        for token, original in span_map.items():
            value_to_tokens.setdefault(original, set()).add(token)
        allowed: set[str] = set()
        for ma in input_enforcement.match_actions:
            if output_actions.get(ma.match.category) != EnforcementAction.RESTORE:
                continue
            allowed.update(value_to_tokens.get(ma.match.matched_text, set()))
        return allowed

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

        Outbound calls also drop findings that target our own mask
        tokens, in two shapes:

          1. ``matched_text`` is the full ``[CATEGORY_N]`` literal
             (caught by ``_MASK_TOKEN_RE.fullmatch``).
          2. ``matched_text`` is the INNER text of a mask token
             (e.g. NER returning ``PHONE_NUMBER_0`` for the body of
             ``[PHONE_NUMBER_0]``). Without this branch the outbound
             pass re-masks our own token as a PERSON and emits
             ``[[NAME_1]]`` — a self-feedback loop that survives all
             the way to the user.

        The filter is gated to ``direction == "output"`` so an inbound
        caller (none today, but the signature allows it) is unaffected.
        """
        from app.services.dlp_prompt import _MASK_TOKEN_RE

        registry = self._select_registry(ner_enabled)
        matches = registry.scan_text(text)
        if direction == "output":
            # Pre-compute the (start, end) spans of every mask-token
            # literal already in the text so we can drop any finding
            # whose span is fully contained inside one.
            mask_token_spans = [
                (m.start(), m.end()) for m in _MASK_TOKEN_RE.finditer(text)
            ]

            def _inside_mask_token(start: int, end: int) -> bool:
                for tstart, tend in mask_token_spans:
                    if tstart <= start and end <= tend:
                        return True
                return False

            # Outbound-only suppression list. NER + Thai-name detectors
            # are inbound-oriented: NER mints arbitrary spans on Thai AI
            # replies (no Thai tokeniser), and ``thai_name`` /
            # ``thai_name_no_prefix`` regexes are greedy on the
            # second-Thai-char group, capturing the next-noun-phrase
            # alongside the actual name (e.g. ``คุณค่ะ และจะดำเนินการ``
            # matched as one span). All three mint ``[THAI_NAME_N]``
            # tokens that don't exist in the inbound ``span_map``, so
            # they survive restore as visible orphan splices in the
            # user's reply. Suppressing them on output direction has no
            # privacy cost — the inbound pass already masked anything
            # the AI is supposed to be quoting, and the dedicated
            # outbound restore is regex-driven for emails / phones /
            # account numbers, which DO still run here.
            _OUTBOUND_SUPPRESSED = {
                "presidio_ner",
                "thai_name",
                "thai_name_no_prefix",
            }

            matches = [
                m for m in matches
                if m.detector_name not in _OUTBOUND_SUPPRESSED
                and not _MASK_TOKEN_RE.fullmatch(m.matched_text.strip())
                and not _inside_mask_token(m.start, m.end)
            ]
        return DetectionResult(
            matches=matches,
            scanned_direction=direction,
            scan_target="response_content",
        )
