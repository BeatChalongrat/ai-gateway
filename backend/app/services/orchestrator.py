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

import asyncio
import re
import time
import uuid

import structlog

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

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
    safe_output_final_snapshot,
    safe_output_raw_snapshot,
    safe_output_snapshot,
)
from app.observability.metrics import (
    DLP_SCAN_DURATION,
    LLM_PROVIDER_LATENCY,
    REDIS_COMMAND_DURATION,
    SIDECAR_FALLBACK_TOTAL,
    SIDECAR_REQUEST_TOTAL,
)
from app.observability.phase_timer import record_phase
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry, DetectorRegistry
from app.services.dlp.policy_engine import (
    CHANNEL_WEBCHAT,
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
    build_dlp_request_context,
)
from app.services.dlp.feature_scope import effective_feature_enabled, is_in_scope
from app.services.dlp.tool_arg_policy import enforce_tool_arg_safety
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profile_service import (
    get_runtime_profile,
    get_runtime_profile_async,
)
from app.services.policy.profiles import get_profile as get_runtime_profile_fallback
from app.services.project_service import ProjectService
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.query_classifier import QueryComplexity, classify_query_complexity
from app.services.routing_service import RoutingService
from app.services.audit_service import AuditService
from app.services.request_log_service import RequestLogService
from app.services.usage_service import (
    UsageService,
    _estimate_cost,
    _estimate_reasoning_cost,
)
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

# Phase-1 B2: bound the number of DLP regex scans running concurrently
# in the threadpool so a burst of requests can't starve the worker's
# 16-thread default executor (main.py). Created at module import; asyncio
# binds it to the running loop lazily on first ``async with`` (3.10+),
# so it is safe to define here. Batch input/output scans go through this;
# the tiny per-chunk streaming scan stays inline (sub-ms 120-char window).
_DLP_SCAN_SEMAPHORE = asyncio.Semaphore(8)

# Categories whose tokens MUST NEVER be restored on outbound — even
# if a (mis)configured policy profile maps them to RESTORE. This is
# the defense-in-depth layer paired with the
# DEFAULT_PROFILE.category_actions_output = MASK for the same two
# categories in policy/profiles.py. The two layers protect against
# different failure modes:
#   - Policy default fix → covers the steady-state production setup.
#   - Hard-coded exclusion → covers (a) custom DB-edited profiles, (b)
#     a future regression that re-introduces RESTORE on these
#     categories, and (c) any STRICT-equivalent custom profile that
#     inherits inbound→outbound and accidentally allows RESTORE.
# Without this, a user pasting an API key + the LLM faithfully
# preserving the masked ``<r0>`` would round-trip the secret back to
# the user. Tests in test_secret_restore_block.py pin this.
_NEVER_RESTORE_CATEGORIES = frozenset({"credential", "secret"})


# Counter-form ``<rN>`` token matcher. Used to recover the highest
# numeric index from a vault snapshot so a fresh mask call starts at
# ``max + 1`` instead of colliding with vault-owned slots. The token
# emitter only uses digits in counter mode; legacy HMAC suffixes are
# ignored here on purpose (they can't influence the next counter).
#
# Under ``DLP_PSEUDONYM_MODE=true`` per-category counters also need
# recovery — that's handled by ``transformer.recover_counters_from_vault``
# which understands both shapes. The legacy regex stays here for
# backwards-compat callers that only need ``__global__`` recovery.
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
        # Phase 1 — Central DLP Policy Engine. Shared decision boundary
        # that wraps the classifier + evaluator above. Both the
        # non-streaming seam (``_scan_and_evaluate``) and the streaming
        # path in ``public_chat.py`` route findings through this single
        # instance, so the policy decision is identical across channels
        # regardless of which detector engine (Rust sidecar / Python)
        # produced the findings.
        self.policy_engine = DLPPolicyEngine(
            classifier=self.classifier, evaluator=self.policy_evaluator
        )
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

    async def _run_preflight_checks(
        self,
        auth: ResolvedApiKey,
        ctx: RequestContext,
        session: "AsyncSession | None",
    ) -> None:
        """Shared pre-provider admission checks for BOTH the streaming
        and non-streaming chat paths.

        Runs rate limit (Redis sliding window) + daily quota + budget +
        portal credit. Any breach raises the same exception the
        non-streaming path has always raised
        (RateLimitExceededError / QuotaExceededError / credit error), so
        clients get an identical error response regardless of the
        ``stream`` flag. Keeping this in one place is what prevents the
        two paths from silently diverging (a stream request used to skip
        rate/quota/budget entirely).

        Rate limiting always runs (Redis only, no session). Quota,
        budget and portal credit run only when a DB session is present,
        mirroring the historical non-streaming behaviour.
        """
        # 1b. Rate limit check (Redis sliding window — per key, project, tenant)
        with record_phase("rate_limit"), REDIS_COMMAND_DURATION.labels(
            operation="rate_limit_pipeline"
        ).time():
            # Per-end-user bucket for trusted shared-key traffic (web-chat);
            # falls back to ``auth.key_id`` for everything else. Cost /
            # audit attribution below still keys on ``auth.key_id``.
            rl = await self.rate_limit_service.check_all_async(
                key_id=str(
                    getattr(auth, "rate_limit_key_id", None) or auth.key_id
                ),
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

        # 1c. Quota + budget checks (DB-backed). Both raise on breach.
        if session is not None:
            await self.quota_service.check_request_quota_async(
                session, ctx.project_id
            )
            await self.budget_service.check_budget_async(session, ctx.project_id)

        # 1d. Portal credit check — no-op for admin/tenant keys.
        # Runs BEFORE DLP so a suspended/zero-balance portal user doesn't
        # waste CPU on entity detection + provider calls. See
        # app.services.gateway.portal_credit_check for the policy.
        if session is not None:
            from app.services.gateway.portal_credit_check import (
                check_portal_credit_async,
            )
            await check_portal_credit_async(
                session,
                str(auth.key_id),
                portal_user_id=getattr(auth, "portal_user_id", None),
            )

    async def handle_chat_completion(
        self,
        auth: ResolvedApiKey,
        payload: ChatCompletionsRequest,
        session: "AsyncSession | None" = None,
        out_meta: dict | None = None,
        _preflight_done: bool = False,
    ) -> ChatCompletionsResponse:
        request_id = f"req_{uuid.uuid4().hex}"
        t_start = time.perf_counter()

        # 1. Resolve project context from authenticated identity.
        # ``policy_profile_override`` is the optional per-request field
        # on ChatCompletionsRequest; project_service applies it (or
        # raises 403 on escalation attempts) before any DLP runs.
        with record_phase("auth"):
            # Track A: native AsyncSession on the chat hot path. The DB
            # read awaits directly (no thread hop); the connection is
            # checked out lazily and RELEASED at the commit before the
            # provider dispatch (see below) so it is not held during the
            # ~1-3s provider call (re-audit H1).
            ctx = await self.project_service.resolve_project_context_async(
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
            await self._inject_project_routing_config_async(
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

        # 1b-1d. Shared admission checks: rate limit + quota + budget +
        # portal credit. Delegated to the same helper the streaming path
        # (resolve_streaming_context) calls so the two can't diverge — a
        # stream request used to skip all of these. See
        # _run_preflight_checks. ``_preflight_done`` is set only when the
        # streaming path already ran preflight and is falling back here
        # for a non-streaming adapter, so we don't double-count the
        # rate-limit window for a single request.
        if not _preflight_done:
            await self._run_preflight_checks(auth, ctx, session)

        # Resolve policy profile: project-level → settings default → "default"
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

        # 2. DLP scan input (skip entirely if DLP disabled).
        # Per-tenant NER gate: pick the right pre-built registry from
        # the cached pair on the orchestrator. The NER-off variant has
        # the regex ``thai_name_no_prefix`` fallback in place of
        # Presidio so bare Thai names are still covered.
        active_registry = self._select_registry(ctx.ner_enabled)

        t_dlp = time.perf_counter()
        input_enforcement = EnforcementDecision(policy_profile_id=profile.profile_id)
        inbound_ctx = None  # set when DLP scans; reused for Vault V2 scope
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
                    # P1 Item 1: don't NER-scan prior assistant turns in the
                    # history — model-generated content already covered by
                    # outbound scanning; English-only NER on it only yields
                    # false positives. Regex PII detectors still run on them.
                    ner_skip_roles=frozenset({"assistant"}),
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
            inbound_ctx = build_dlp_request_context(
                auth=auth,
                direction=ScanDirection.INPUT,
                source_type=SourceType.DIRECT_API,
                streaming=False,
                client_conversation_id=payload.metadata.get("conversation_id"),
            )
            input_enforcement = self._scan_and_evaluate(
                input_detection,
                profile,
                context=inbound_ctx,
                segments=(
                    self._numeric_segments_from_messages(
                        payload.messages,
                        scan_system_messages=profile.scan_system_messages,
                    )
                    # Phase 4A.2.7 — tenant-scoped Policy V2: a non-allowlisted
                    # tenant gets NO V2 numeric capture (empty segments →
                    # baseline behavior), even when the global flag is on.
                    if effective_feature_enabled(
                        settings.dlp_policy_engine_v2_enabled,
                        getattr(inbound_ctx, "tenant_id", None),
                        getattr(inbound_ctx, "project_id", None),
                    )
                    else []
                ),
            )
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
                    await self.request_log_service.persist_async(
                        session,
                        RequestRecord(
                            request_id=request_id,
                            user_id=ctx.user_id,
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
            await self.audit_service.record_blocked_request_async(
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
        # H1 — legacy cross-turn reverse-map / counter recovery runs ONLY
        # when Vault V2 is disabled. Under V2 the legacy key is never read;
        # token uniqueness comes from the scoped Redis reservation.
        if (
            conversation_id
            and settings.dlp_enabled
            and settings.dlp_conversation_vault_enabled
            and not settings.dlp_vault_v2_enabled
        ):
            from app.services.dlp.conversation_vault import conversation_vault
            from app.services.dlp.transformer import (
                recover_counters_from_vault,
            )
            existing_vault = await conversation_vault.get_all_tokens_async(
                conversation_id
            )
            if existing_vault:
                vault_reverse_map = {
                    value: token for token, value in existing_vault.items()
                }
                # Per-shape counter recovery — handles legacy ``<rN>``
                # (``__global__`` bucket) and pseudonym ``<<CAT_N>>``
                # (per-category buckets) in one pass. A pre-pseudonym
                # vault yields exactly ``{__global__: N}`` so legacy
                # callers see identical behaviour.
                recovered = recover_counters_from_vault(existing_vault)
                if recovered:
                    inbound_start_counters = recovered

        messages_to_send = list(payload.messages)
        span_map: dict[str, str] = {}
        inbound_counters: dict[str, int] = {}
        # Vault V2: reserve scope-unique tokens atomically BEFORE the
        # synchronous transformer runs, then have the transformer consume
        # the reservation and emit per-token allocation records.
        v2_prepared = None
        if (
            effective_feature_enabled(
                settings.dlp_vault_v2_enabled,
                getattr(inbound_ctx, "tenant_id", None),
                getattr(inbound_ctx, "project_id", None),
            )
            and settings.dlp_conversation_vault_enabled
            and input_enforcement.requires_transformation
        ):
            v2_prepared = await self._vault_v2_prepare(
                inbound_ctx, input_enforcement.match_actions
            )
        if input_enforcement.requires_transformation:
            if v2_prepared is not None:
                _scope, _reserved, _records = v2_prepared
                messages_to_send, span_map, inbound_counters = self.transformer.mask_messages(
                    payload.messages,
                    input_enforcement.match_actions,
                    token_allocator=_reserved.allocator(),
                    record_sink=_records,
                )
            else:
                messages_to_send, span_map, inbound_counters = self.transformer.mask_messages(
                    payload.messages,
                    input_enforcement.match_actions,
                    conversation_key=conversation_key,
                    start_counters=inbound_start_counters,
                    vault_reverse_map=vault_reverse_map or None,
                )

        # Cross-turn DLP vault persistence. Pure best-effort — Redis outage
        # means no carry-over, never a failed request and never raw passthrough.
        if span_map and settings.dlp_conversation_vault_enabled:
            if settings.dlp_vault_v2_enabled:
                # V2 — store entries derived from allocation records ONLY
                # (no raw-value reverse lookup); reservation-checked store.
                # Gated on the prepared reservation, whose scope already binds
                # the TRUSTED conversation (X-Conversation-ID via inbound_ctx),
                # NOT the request-body ``conversation_id`` — managed WebChat
                # supplies the conversation via the trusted header, so a
                # body-only gate would silently skip the store (reserving a
                # counter but never persisting the entry → restore can't find
                # the token). Phase 4A.2.5 (B1).
                if v2_prepared is not None:
                    await self._vault_v2_store_prepared(v2_prepared)
                # else: V2 on but no valid scope / reservation failed → the
                # request was masked request-locally and nothing is persisted.
            elif conversation_id:
                from app.services.dlp.conversation_vault import conversation_vault
                await conversation_vault.extend_async(conversation_id, span_map)
                # Shadow readiness: validate scope + count, NO Redis I/O.
                self._vault_v2_shadow(
                    inbound_ctx, span_map, input_enforcement.match_actions
                )

        # 4a. Inject a system prompt explaining DLP placeholders so the
        # model doesn't refuse masked messages. Gated on the inbound
        # DLP findings count — a clean request (no findings) reaches
        # the model with its messages untouched, so a "What is 2+2?"
        # prompt doesn't pay the DLP-context tokens.
        if input_enforcement.match_actions:
            from app.services.dlp_prompt import inject_dlp_system_prompt
            # Pass span_map so the prompt can enumerate the exact
            # ``<<CATEGORY_N>>`` placeholders the LLM is allowed to
            # reference. Closes the hallucinated-placeholder failure
            # mode caught in the real-LLM E2E Case 2 trace.
            messages_to_send = inject_dlp_system_prompt(
                messages_to_send, span_map=span_map,
            )
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
                    await self.request_log_service.persist_async(
                        session,
                        RequestRecord(
                            request_id=request_id,
                            user_id=ctx.user_id,
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
                    await session.commit()
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
                openai_key = await self.provider_key_service.get_api_key_async(
                    session, "openai"
                )
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
                            await self.request_log_service.persist_async(
                                session,
                                RequestRecord(
                                    request_id=request_id,
                                    user_id=ctx.user_id,
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
                            await session.commit()
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
            router_used, _matched_rule = await self._apply_router_chain(
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
            # Auto Thinking Mode: enable adaptive reasoning when the
            # resolved model can think and the caller didn't opt out.
            self._maybe_enable_adaptive_reasoning(payload, route.model_name)
        log.info(
            "route_selected",
            provider=route.provider_name,
            model=route.model_name,
            constrained=route.constrained_by_policy,
        )

        # 5b. Phase 4A.2.6.1 — In-chat SECURE CALCULATOR. When enabled for a
        # MANAGED (internal-trusted WebChat) request with a valid owner scope,
        # detect deterministic calc intent + collect ONLY Vault-V2-eligible
        # confidential-numeric operands from THIS turn's allocation records, and
        # answer server-side with a ``<CALC_n>`` template. The provider is
        # BYPASSED (never sees raw operands/result). Public Direct API can't
        # reach here: it lacks the WEBCHAT channel + a trusted-header scope.
        # canonical_history + display_lifecycle are enforced chat-edge-side.
        _calc_outcome = None
        _calc_error = False
        _calc_safe_error = ""
        _calc_active = False
        if (
            settings.dlp_secure_calc_enabled
            and settings.dlp_vault_v2_enabled
            and settings.dlp_policy_engine_v2_enabled
            and settings.dlp_canonical_artifact_enabled
            # Phase 4A.2.7 — tenant-scoped: ALL lower layers share this scope, so
            # one check gates the calc (v2_prepared already required effective
            # vault). Non-allowlisted tenant → no <CALC_n>.
            and is_in_scope(
                getattr(inbound_ctx, "tenant_id", None),
                getattr(inbound_ctx, "project_id", None),
            )
            and v2_prepared is not None
            and getattr(inbound_ctx, "channel", None) == CHANNEL_WEBCHAT
        ):
            from app.services.dlp.secure_calc import (
                orchestrate_secure_calc as _orch_calc,
                CalcError as _CalcErr,
            )
            from app.services.dlp.secure_calc_intent import CALC_SAFE_ERROR as _CALC_ERR_TXT

            _calc_user_text = ""
            for _m in reversed(payload.messages):
                if getattr(_m, "role", None) == "user":
                    _c = getattr(_m, "content", "")
                    _calc_user_text = _c if isinstance(_c, str) else ""
                    break
            _scope_c, _reserved_c, _records_c = v2_prepared
            try:
                _calc_outcome = await _orch_calc(
                    scope=_scope_c, records=_records_c,
                    user_text=_calc_user_text, created_at=int(time.time()),
                )
            except _CalcErr as _ce:
                _calc_error = True
                _calc_safe_error = _CALC_ERR_TXT
                log.info("secure_calc_denied", code=_ce.code)
            _calc_active = _calc_outcome is not None or _calc_error
            if _calc_outcome is not None:
                log.info("secure_calc_applied", result_token=_calc_outcome.token)

        # 6. Resolve provider API key (DB → env var fallback)
        provider_api_key = None
        if session is not None:
            provider_api_key = await self.provider_key_service.get_api_key_async(
                session, route.provider_name
            )
        if not provider_api_key and not _calc_active:
            # Calculator answers server-side without a provider key (it bypasses
            # the provider entirely), so a missing key must not block it.
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
        # P1 reasoning cache isolation — a reasoning request (mode +
        # show_summary) must not share a cache entry with a non-reasoning
        # one for the same prompt/model. None for non-reasoning callers so
        # the key is unchanged and existing entries stay reachable.
        _cache_rc = getattr(payload, "reasoning", None)
        cache_reasoning_key = (
            f"{_cache_rc.mode}:{int(bool(_cache_rc.show_summary))}"
            if (_cache_rc is not None and _cache_rc.mode != "off")
            else None
        )
        cache_hit = None
        if not payload.stream and not _calc_active:
            with record_phase("cache"), REDIS_COMMAND_DURATION.labels(
                operation="cache_get"
            ).time():
                cache_hit = await self.cache_service.get_async(
                    model=route.model_name,
                    messages=cache_messages,
                    project_id=ctx.project_id,
                    api_key_id=cache_api_key_id,
                    reasoning_key=cache_reasoning_key,
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
                # Same secret-token strip on the cache-hit path so a
                # replay can never leak a credential the original
                # request bound to ``<r0>``. See
                # ``_strip_never_restore_tokens`` for the contract.
                _cache_redact_span_map = self._strip_never_restore_tokens(
                    span_map, input_enforcement,
                )
                _sanitised = _redact_orphan_tokens(
                    cached_text, _cache_redact_span_map,
                )
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
                    await self.request_log_service.persist_async(
                        session,
                        RequestRecord(
                            request_id=request_id,
                            user_id=ctx.user_id,
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
                            # Bug 4 (logging): the cache-hit path logged
                            # findings but dropped the CATEGORY (the fresh
                            # path sets it at the success persist). That made
                            # dlp_categories intermittently empty on a masked
                            # secret — populated on the cache MISS, blank on the
                            # HIT. Populate it here too (input-only: the cached
                            # response is already masked, no response scan).
                            dlp_categories=sorted(
                                {ma.match.category for ma in input_enforcement.match_actions}
                            ),
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
                            # 4A.2.3 — provider-safe (masked) only; never
                            # the restored ``cached_text``.
                            output_final=safe_output_final_snapshot(
                                cached_text_masked, input_enforcement
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
        # H1 (connection release): commit the read-only pre-provider work so
        # the async session's pooled connection returns to the pool and is
        # NOT held during the ~1-3s provider dispatch below. Post-dispatch
        # reads/writes (outbound DLP block persist, etc.) re-checkout lazily;
        # the request dependency commits again at the end.
        if session is not None:
            await session.commit()
        t_provider = time.perf_counter()
        # Layer-1 fallback key resolver. ``dispatch_with_fallback``
        # calls this (synchronously) for each fallback hop's provider.
        # Kept SYNC to preserve the dispatcher's key_resolver contract: it
        # opens a short-lived SYNC session (the sync engine is retained for
        # admin/portal/background) so the async request session is not
        # reused across the provider await. Fallback is rare and provider
        # keys are cached, so this seldom touches the DB.
        def _resolve_fallback_key(provider_name: str) -> str | None:
            from app.core.db import SessionLocal as _SyncSession
            with _SyncSession() as _s:
                return self.provider_key_service.get_api_key(_s, provider_name)

        # A2: when a provider rejects a key with 401/403 mid-dispatch, flip
        # that key's row to invalid so the round-robin pool stops handing it
        # out; the dispatcher then re-resolves the next key via
        # ``_resolve_fallback_key`` and retries within this same request.
        def _invalidate_key(provider_name: str, bad_key: str) -> None:
            from app.core.db import SessionLocal as _SyncSession
            try:
                with _SyncSession() as _s:
                    self.provider_key_service.invalidate_key_by_value(
                        _s, provider_name, bad_key
                    )
                    _s.commit()
            except Exception as _exc:  # never let bookkeeping break dispatch
                log.warning(
                    "auto_invalidate_key_failed",
                    provider=provider_name,
                    error=str(_exc),
                )

        try:
            if _calc_active:
                # Phase 4A.2.6.1 — secure calculator answers server-side; the
                # provider is BYPASSED entirely (never sees raw operands/result).
                from app.domain.providers import ProviderResponse as _ProvResp

                provider_response = _ProvResp(
                    content=(_calc_outcome.canonical if _calc_outcome is not None else _calc_safe_error),
                    model=route.model_name,
                    finish_reason="stop",
                    usage=None,
                    raw={},
                    reasoning_content=None,
                )
                used_provider, used_model = route.provider_name, route.model_name
                if _calc_outcome is not None:
                    # Seed the request-local restore map so the OWNER display
                    # restores <CALC_n> → raw result. ``ai_to_service_masked``
                    # is captured BEFORE restore, so canonical / output_final
                    # keep the token.
                    span_map[_calc_outcome.token] = _calc_outcome.raw_value
            else:
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
                        invalidate_key=_invalidate_key,
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
                        original_error=exc,
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
            # OpenAI prompt-cache hit (0 for every other provider / cache
            # miss). Observability only — no request_log column yet, so
            # this stays in the structured log line.
            cached_prompt_tokens=(
                provider_response.usage.cached_prompt_tokens
                if provider_response.usage else 0
            ),
        )

        # 7. Estimate usage (falls back to heuristic when provider doesn't report usage)
        if provider_response.usage:
            from app.services.currency_service import currency_service
            _usd = _estimate_cost(
                route.model_name,
                provider_response.usage.prompt_tokens,
                provider_response.usage.completion_tokens,
                provider_response.usage.cached_prompt_tokens,
                # P1 Item 4 — Anthropic cache-write tokens (1.25× input).
                getattr(
                    provider_response.usage,
                    "cache_creation_prompt_tokens",
                    0,
                ),
            )
            _cost_info = currency_service.format_cost(_usd)
            # T3: surface ``reasoning_tokens`` on UsageInfo when the
            # provider broke it out (or we estimate from
            # ``reasoning_content`` length when Anthropic folded it
            # into ``output_tokens``). ``None`` keeps the wire shape
            # backward-compatible for non-reasoning calls.
            _provider_rtok = provider_response.usage.reasoning_tokens or 0
            if _provider_rtok == 0 and provider_response.reasoning_content:
                _provider_rtok = max(1, len(provider_response.reasoning_content) // 4)
            # Cap at completion_tokens — reasoning is a SUBSET of the
            # completion meter on every provider we route to, so the
            # attribution (reasoning_cost) must never exceed the
            # completion cost it is part of. Guards against a length
            # over-estimate inflating the breakdown past the total.
            _provider_rtok = min(
                _provider_rtok, provider_response.usage.completion_tokens
            )
            _reasoning_tok_for_usage = _provider_rtok or None
            usage = UsageInfo(
                prompt_tokens=provider_response.usage.prompt_tokens,
                completion_tokens=provider_response.usage.completion_tokens,
                total_tokens=provider_response.usage.total_tokens,
                estimated_cost_usd=_usd,
                cost_thb=_cost_info["thb"],
                cost_display=_cost_info["display"],
                reasoning_tokens=_reasoning_tok_for_usage,
            )
        else:
            usage = self.usage_service.estimate(
                payload=payload,
                response_text=provider_response.content,
                model_name=route.model_name,
            )
            # Heuristic estimator doesn't know reasoning tokens, but if
            # the adapter parsed ``reasoning_content`` we can still
            # length-estimate. Leave ``None`` when neither is available.
            if provider_response.reasoning_content:
                usage = usage.model_copy(update={
                    "reasoning_tokens": max(
                        1, len(provider_response.reasoning_content) // 4
                    )
                })

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
                response_detection = await self._scan_text_as_result_async(
                    response_content,
                    direction="output",
                    ner_enabled=False,
                )
            response_enforcement = self._scan_and_evaluate(
                response_detection,
                pre_restore_profile,
                context=build_dlp_request_context(
                    auth=auth,
                    direction=ScanDirection.OUTPUT,
                    source_type=SourceType.DIRECT_API,
                    streaming=False,
                    client_conversation_id=payload.metadata.get("conversation_id"),
                ),
                text=response_content,
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
                        await self.request_log_service.persist_async(
                            session,
                            RequestRecord(
                                request_id=request_id,
                                user_id=ctx.user_id,
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
                        await session.commit()
                    except Exception as exc:
                        log.warning("dlp_block_log_persist_failed", error=str(exc))
                await self.audit_service.record_blocked_request_async(
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
                # V2: reserve outbound tokens from the SAME scoped counter
                # so they never collide with this turn's inbound tokens.
                _out_v2 = None
                if (
                    settings.dlp_vault_v2_enabled
                    and settings.dlp_conversation_vault_enabled
                ):
                    _out_v2 = await self._vault_v2_prepare(
                        inbound_ctx, response_enforcement.match_actions
                    )
                if _out_v2 is not None:
                    _osc, _ores, _orec = _out_v2
                    response_content, outbound_span_map, _ = self.transformer.mask_text(
                        response_content,
                        response_enforcement.match_actions,
                        token_allocator=_ores.allocator(),
                        record_sink=_orec,
                    )
                else:
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
                # query can still resolve them.
                if outbound_span_map and settings.dlp_conversation_vault_enabled:
                    if settings.dlp_vault_v2_enabled:
                        # B1 — gate on the prepared reservation (trusted-header
                        # scope), not the request-body conversation_id.
                        if _out_v2 is not None:
                            await self._vault_v2_store_prepared(_out_v2)
                    elif conversation_id:
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
        # Phase 4A.2.6.1 — restore the calculator result token for the OWNER
        # display ONLY. ``ai_to_service_masked`` (canonical) + output_final keep
        # ``<CALC_n>``; this request-local substitution affects only the
        # user-facing ``response_content``. Never persisted, never logged raw.
        if _calc_outcome is not None and _calc_outcome.token in response_content:
            response_content = response_content.replace(
                _calc_outcome.token, _calc_outcome.raw_value
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
                final_response_detection = await self._scan_text_as_result_async(
                    response_content,
                    direction="output",
                    ner_enabled=False,
                )
            response_enforcement = self._scan_and_evaluate(
                final_response_detection,
                profile,
                context=build_dlp_request_context(
                    auth=auth,
                    direction=ScanDirection.OUTPUT,
                    source_type=SourceType.DIRECT_API,
                    streaming=False,
                    client_conversation_id=payload.metadata.get("conversation_id"),
                ),
                text=response_content,
            )
            if response_enforcement.requires_transformation:
                response_content, _final_outbound_span_map, _ = self.transformer.mask_text(
                    response_content,
                    response_enforcement.match_actions,
                    start_counters=inbound_counters,
                    conversation_key=conversation_key,
                    vault_reverse_map=vault_reverse_map or None,
                )

        # 8d. Tool-call argument DLP. Function-calling responses
        # (commits cd7e8d8, 6cf7752) carry their payload in
        # ``provider_response.tool_calls[*].function.arguments`` as a
        # JSON string rather than in ``content``. The outbound text
        # scan above (sections 8 / 8c) never sees those bytes, so
        # without this pass an AI that puts a customer's email or
        # credit card into a ``send_email`` / ``charge`` call would
        # leak to the client.
        #
        # Each argument string is scanned with the same outbound
        # detectors used for ``response_content`` (NER off — the same
        # Thai-NER false-positive rationale applies). When sensitive
        # data is found:
        #   - block: raise DLPViolationError, same as section 8;
        #     persist a BLOCKED row first so the dashboard shows the
        #     tool-call attempt and what category fired.
        #   - mask: rewrite the arguments string in place. Mask
        #     tokens (``<rN>``) are valid inside a JSON string value,
        #     so the resulting JSON stays parseable on the client.
        #
        # ``final_tool_calls`` is the post-mask payload threaded into
        # the response builder + cache write below. None when the
        # provider didn't return any tool_calls — backward-compatible
        # with plain text responses.
        final_tool_calls: list[dict] | None = None
        if (
            provider_response.tool_calls
            and settings.dlp_enabled
            and settings.dlp_scan_responses
            and profile.scan_responses
        ):
            final_tool_calls = []
            tool_call_block_reasons: list = []
            for tc in provider_response.tool_calls:
                # Shallow copy so we can rewrite ``function.arguments``
                # without mutating the frozen provider_response dataclass.
                new_tc = dict(tc) if isinstance(tc, dict) else {}
                fn = dict(new_tc.get("function") or {})
                args_str = fn.get("arguments", "") or ""
                if not args_str:
                    new_tc["function"] = fn
                    final_tool_calls.append(new_tc)
                    continue
                arg_detection = await self._scan_text_as_result_async(
                    args_str, direction="output", ner_enabled=False,
                )
                arg_enforcement = self._scan_and_evaluate(
                    arg_detection,
                    pre_restore_profile,
                    context=build_dlp_request_context(
                        auth=auth,
                        direction=ScanDirection.OUTPUT,
                        source_type=SourceType.TOOL_ARGUMENT,
                        streaming=False,
                        model=getattr(provider_response, "model", None),
                        client_conversation_id=payload.metadata.get("conversation_id"),
                    ),
                    text=args_str,
                )
                if arg_enforcement.is_blocked:
                    # Collect all blocked tool_call enforcements so we
                    # raise once with the full set of categories that
                    # tripped, rather than failing on the first one
                    # and hiding the rest from the audit row.
                    tool_call_block_reasons.append(arg_enforcement)
                    continue
                if arg_enforcement.requires_transformation:
                    _arg_v2 = None
                    if (
                        settings.dlp_vault_v2_enabled
                        and settings.dlp_conversation_vault_enabled
                    ):
                        _arg_v2 = await self._vault_v2_prepare(
                            inbound_ctx, arg_enforcement.match_actions
                        )
                    if _arg_v2 is not None:
                        _asc, _ares, _arec = _arg_v2
                        masked_args, outbound_arg_span_map, _ = self.transformer.mask_text(
                            args_str,
                            arg_enforcement.match_actions,
                            token_allocator=_ares.allocator(),
                            record_sink=_arec,
                        )
                    else:
                        masked_args, outbound_arg_span_map, _ = self.transformer.mask_text(
                            args_str,
                            arg_enforcement.match_actions,
                            start_counters=inbound_counters,
                            conversation_key=conversation_key,
                            vault_reverse_map=vault_reverse_map or None,
                        )
                    fn["arguments"] = masked_args
                    log.info(
                        "dlp_tool_call_arguments_masked",
                        tool_call_id=new_tc.get("id"),
                        tool_name=fn.get("name"),
                        findings=len(arg_enforcement.match_actions),
                        categories=sorted(
                            {ma.match.category for ma in arg_enforcement.match_actions}
                        ),
                    )
                    # Outbound vault extend — mirrors section 8 so an
                    # audit query can still resolve the masked values.
                    if outbound_arg_span_map and settings.dlp_conversation_vault_enabled:
                        if settings.dlp_vault_v2_enabled:
                            # B1 — gate on the prepared reservation (trusted-
                            # header scope), not the request-body conversation_id.
                            if _arg_v2 is not None:
                                await self._vault_v2_store_prepared(_arg_v2)
                        elif conversation_id:
                            from app.services.dlp.conversation_vault import (
                                conversation_vault,
                            )
                            await conversation_vault.extend_async(
                                conversation_id, outbound_arg_span_map
                            )
                # FIX 8 — first-party LOCAL tools (SRAN Desktop) get the
                # same emit-side restore the response text got at 8b:
                # their arguments are client-bound and execute on the
                # user's own machine, so a token the output policy says
                # RESTORE (never credential/secret — Layer 2 strips
                # those from ``restore_allowed_tokens``) goes back as
                # the user's own value instead of a dead ``<rN>`` that
                # breaks the command. Inbound tokens only; outbound
                # tool-arg masks minted above use fresh counters and are
                # not in ``restore_allowed_tokens``.
                from app.services.dlp.tool_arg_policy import (
                    is_local_execution_tool,
                )
                from app.services.dlp.transformer import contains_mask_token
                _args_now = fn.get("arguments", "") or ""
                if (
                    restore_allowed_tokens
                    and is_local_execution_tool(fn.get("name"))
                    and contains_mask_token(_args_now)
                ):
                    _restored_args = self.transformer.restore(
                        _args_now, span_map,
                        allowed_tokens=restore_allowed_tokens,
                    )
                    if _restored_args != _args_now:
                        fn["arguments"] = _restored_args
                        log.info(
                            "dlp_tool_arg_local_restore",
                            tool_call_id=new_tc.get("id"),
                            tool_name=fn.get("name"),
                        )
                # B2 — tool-argument placeholder safety. If the (now
                # masked) arguments still carry a ``<rN>`` placeholder,
                # only tools explicitly classified ``placeholder_safe``
                # may execute on them; external-egress / plaintext-
                # requiring / unknown tools are blocked with a PII-free,
                # valid-JSON marker so the executor never silently runs on
                # a placeholder. Raw values are never emitted either way.
                _safe_args, _arg_blocked, _arg_reason = enforce_tool_arg_safety(
                    fn.get("name"), fn.get("arguments", "") or ""
                )
                if _arg_reason is not None:
                    fn["arguments"] = _safe_args
                    log.info(
                        "dlp_tool_arg_placeholder_safety",
                        tool_call_id=new_tc.get("id"),
                        tool_name=fn.get("name"),
                        blocked=_arg_blocked,
                        reason=_arg_reason,
                    )
                new_tc["function"] = fn
                final_tool_calls.append(new_tc)

            if tool_call_block_reasons:
                # Aggregate categories + findings across every blocked
                # tool call so the single DLPViolationError carries the
                # full picture (same shape as the section 8 block).
                all_match_actions: list = []
                for e in tool_call_block_reasons:
                    all_match_actions.extend(e.match_actions)
                aggregated = EnforcementDecision(
                    match_actions=all_match_actions,
                    policy_profile_id=profile.profile_id,
                )
                if session is not None:
                    from app.domain.observability import RequestRecord, RequestStatus
                    try:
                        await self.request_log_service.persist_async(
                            session,
                            RequestRecord(
                                request_id=request_id,
                                user_id=ctx.user_id,
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
                                output_raw="[BLOCKED_BY_DLP_TOOL_ARGS]",
                                dlp_findings_json=build_dlp_findings_json(
                                    input_enforcement.match_actions,
                                    all_match_actions,
                                    span_map=span_map,
                                ),
                            ),
                        )
                        await session.commit()
                    except Exception as exc:
                        log.warning("dlp_block_log_persist_failed", error=str(exc))
                await self.audit_service.record_blocked_request_async(
                    ctx=ctx, enforcement=aggregated, direction="output",
                )
                raise DLPViolationError(
                    "Response blocked by DLP policy — tool_call arguments contained sensitive data.",
                    details={
                        "action": "block",
                        "policy_profile": aggregated.policy_profile_id,
                        "categories": sorted(
                            {ma.match.category.upper() for ma in all_match_actions}
                        ),
                        "findings_count": len(all_match_actions),
                        "direction": "output",
                        "location": "tool_calls.arguments",
                    },
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
        from app.services.dlp.transformer import (
            ANY_MASK_TOKEN_RE as _ANY_MASK_TOKEN_RE,
            redact_orphan_tokens,
        )
        _before_redact = response_content
        # Filter span_map so the orphan pass treats never-restorable
        # secret tokens as orphans (and strips them) — see
        # ``_strip_never_restore_tokens`` for the rationale.
        _redact_span_map = self._strip_never_restore_tokens(
            span_map, input_enforcement,
        )
        response_content = redact_orphan_tokens(response_content, _redact_span_map)
        if _before_redact != response_content:
            # Shared regex captures both ``<rN>`` and ``<<CAT_N>>`` so
            # pseudonym-mode orphans show up in the log under the same
            # event name. Stays in lockstep with the redact pass that
            # also keys on ``ANY_MASK_TOKEN_RE``.
            redacted_tokens = sorted(
                set(_ANY_MASK_TOKEN_RE.findall(_before_redact))
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
        await self.audit_service.record_success_async(
            ctx=ctx,
            route=route,
            enforcement=input_enforcement,
            usage=usage,
            input_enforcement=input_enforcement,
            output_enforcement=response_enforcement,
        )

        # 10b. Portal credit deduction — cost-based (the request's real
        # USD cost × tenant markup × USAGE_CREDITS_PER_USD, min 1).
        # Debits the key's owning portal user, or the TRUSTED end-user
        # behind a shared key (web-chat). Writes a
        # ``credit_transactions`` row (type=usage) and clamps at 0.
        if session is not None and usage is not None:
            from app.services.gateway.portal_credit_check import (
                deduct_portal_credit_async,
            )
            await deduct_portal_credit_async(
                session,
                str(auth.key_id),
                request_id,
                cost_usd=float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0),
                tokens_used=int(getattr(usage, "total_tokens", 0) or 0),
                tenant_id=str(ctx.tenant_id),
                portal_user_id=getattr(auth, "portal_user_id", None),
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

            from app.domain.observability import resolved_log_model

            record = RequestRecord(
                request_id=request_id,
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                provider=route.provider_name,
                # Resolve the OpenRouter fallback "auto" sentinel to the
                # response-reported model so usage aggregation never
                # shows "auto" rows.
                model=resolved_log_model(
                    route.model_name, provider_response.model
                ),
                status=RequestStatus.SUCCESS,
                policy_action=overall_action.name.lower(),
                policy_profile_id=profile.profile_id,
                latency_ms=total_ms,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
                # T3: persist reasoning attribution. ``usage.reasoning_tokens``
                # is None on non-reasoning calls so we coerce to 0 for
                # the request_log column (NOT NULL DEFAULT 0). The
                # cost helper short-circuits to 0.0 when the token
                # count is zero so non-reasoning rows stay byte-identical.
                reasoning_tokens=(usage.reasoning_tokens or 0),
                reasoning_cost=_estimate_reasoning_cost(
                    route.model_name, usage.reasoning_tokens or 0
                ),
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
                # 4A.2.3 — provider-safe (masked) only; never the restored
                # ``response_content`` display.
                output_final=safe_output_final_snapshot(
                    ai_to_service_masked, response_enforcement
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
            # queue falls back to a synchronous persist on its OWN fresh
            # sync SessionLocal. Track A: we no longer pass the request
            # session as fallback_session — it is now an AsyncSession and
            # the queue's fallback does synchronous add/flush, which an
            # AsyncSession can't serve (the write would fail silently and
            # the audit row would be lost).
            from app.services import request_log_queue
            try:
                request_log_queue.enqueue(record)
            except Exception as exc:
                log.warning("request_log_enqueue_failed", error=str(exc))

            # Alert evaluation moved OFF the request path (Phase-1 B6).
            # A periodic background sweep (lifespan _alert_evaluation_sweep)
            # now runs AlertEvaluator.evaluate_all every few seconds, so the
            # per-request enabled-rules SELECT + window aggregates no longer
            # block the event loop on every completed request.

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
            # ``final_tool_calls`` comes from section 8d (the
            # tool-arg DLP pass). It is None when DLP was disabled
            # or the response carried no tool_calls — fall back to
            # the raw provider value so a non-DLP path is unaffected.
            response_tool_calls = (
                final_tool_calls
                if final_tool_calls is not None
                else provider_response.tool_calls
            )
            # Reasoning (T2) — gating mirrors the streaming path:
            #   * ``reasoning_content`` is surfaced ONLY when the
            #     caller opted in via ``payload.reasoning.show_summary=True``
            #     AND the provider returned thinking text. Otherwise
            #     the field stays None and ``response_model_exclude_none``
            #     drops it from the wire — the model can still have
            #     thought (better answers); the user just doesn't see it.
            #   * ``reasoning_tokens`` is surfaced unconditionally
            #     when known so the caller can audit billing even
            #     when summary is hidden. Source priority:
            #       1. provider-reported (OpenAI o-series usage details)
            #       2. length estimate from reasoning_content (~4 chars/token)
            #       3. None when neither is available
            _reasoning_cfg_resp = getattr(payload, "reasoning", None)
            _show_summary_resp = bool(
                _reasoning_cfg_resp
                and _reasoning_cfg_resp.show_summary
                and _reasoning_cfg_resp.mode != "off"
            )
            response_reasoning_content = (
                provider_response.reasoning_content
                if _show_summary_resp and provider_response.reasoning_content
                else None
            )
            # T3: ``usage.reasoning_tokens`` was already resolved at
            # step 7 (provider-reported OR length-estimated from
            # ``reasoning_content``); reuse it here so the top-level
            # response field and ``usage.reasoning_tokens`` agree.
            response_reasoning_tokens = usage.reasoning_tokens

            # P0 downgrade signal — reasoning requested but resolved model
            # can't reason. None on the normal path (dropped from wire).
            _downgrade_reason = self._reasoning_downgrade_reason(
                payload, route.model_name
            )

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
                            tool_calls=response_tool_calls,
                        ),
                    )
                ],
                usage=usage,
                policy_action=overall_action.name.lower(),
                policy_reasons=all_reasons,
                reasoning_content=response_reasoning_content,
                reasoning_tokens=response_reasoning_tokens,
                # T5 — surface the thinking signature for client-driven
                # tool-loop round-trips. Independent of show_summary: a
                # client needs the signature to replay even when it doesn't
                # display the thinking text.
                reasoning_signature=getattr(
                    provider_response, "reasoning_signature", None
                ),
                reasoning_downgraded=True if _downgrade_reason else None,
                reasoning_downgrade_reason=_downgrade_reason,
            )
            if _downgrade_reason:
                log.info(
                    "reasoning_downgraded",
                    requested_mode=payload.reasoning.mode,
                    resolved_model=route.model_name,
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
            # Phase 4A.2.6.1 — NEVER cache a secure-calculator response. The
            # answer is server-computed per owner-scope and carries a
            # scope-bound ``<CALC_n>``; caching it would let a different caller
            # (incl. Direct API) replay the calc canonical for the same prompt.
            and not _calc_active
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
            # P1 reasoning isolation — never persist reasoning text into the
            # shared cache. The reasoning channel bypasses the per-user DLP
            # restore pass run on cache hit (that pass only re-restores
            # ``choices[].message.content``), so a stored ``reasoning_content``
            # would replay user A's thinking verbatim to user B in the same
            # project. Drop it before write; the reasoning_tokens count is a
            # non-sensitive integer and may stay for cost display.
            cache_response.pop("reasoning_content", None)
            await self.cache_service.set_async(
                model=route.model_name,
                messages=cache_messages,
                project_id=ctx.project_id,
                response=cache_response,
                api_key_id=cache_api_key_id,
                reasoning_key=cache_reasoning_key,
            )

        # 13. Realtime AI review hook (fire-and-forget). Phase 1 egress
        # fix: the reviewer receives the POST-DLP, PRE-RESTORE sanitized
        # output (``ai_to_service_masked``) — NOT the raw provider output
        # and NOT the restored ``output_final``. PII the outbound pipeline
        # MASKED is already a ``<rN>`` token (so it never egresses to the
        # external reviewer); PII the pipeline MISSED is still present in
        # the sanitized text, so the reviewer can still find coverage
        # gaps. This is the same value persisted to the ``output_raw``
        # trace column via ``safe_output_raw_snapshot``.
        maybe_ai_review_background(
            request_id,
            ai_to_service_masked,
            api_key_id=str(auth.key_id) if auth else None,
        )

        # 14. Phase 4A.2.1/4A.2.2 — dual canonical/display artifact (shadow by
        # default; gated envelope via ``out_meta`` when enabled). Display
        # response is unchanged; the route injects the envelope off-schema.
        self._produce_canonical_artifact_nonstream(
            request_id=request_id,
            canonical_content=ai_to_service_masked,
            context=inbound_ctx,
            auth=auth,
            span_map=span_map,
            restore_allowed_tokens=restore_allowed_tokens,
            out_meta=out_meta,
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
        self._apply_project_routing_config(project, payload, log)

    async def _inject_project_routing_config_async(
        self,
        *,
        session: "AsyncSession",
        project_id: str,
        payload: ChatCompletionsRequest,
        log,
    ) -> None:
        """Async sibling of ``_inject_project_routing_config`` — see its
        docstring. Only the project fetch differs (awaited); the mutation
        logic is shared via ``_apply_project_routing_config``."""
        if not project_id:
            return
        try:
            from app.models.project import ProjectORM

            project = await session.get(ProjectORM, project_id)
        except Exception as exc:
            log.warning("project_routing_config_load_failed", error=str(exc))
            return
        self._apply_project_routing_config(project, payload, log)

    @staticmethod
    def _apply_project_routing_config(project, payload, log) -> None:
        """Merge a fetched project's routing config into payload.metadata
        (pure, no DB). Shared by the sync + async inject variants."""
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

    def _maybe_enable_adaptive_reasoning(
        self, payload: ChatCompletionsRequest, model_name: str
    ) -> None:
        """Auto-enable adaptive reasoning when routing lands on a
        reasoning-capable model and the caller did not specify a mode.

        Auto Thinking Mode: the user no longer flips a Thinking toggle,
        so when the (resolved) model can think we turn on adaptive
        reasoning with a visible summary by default. ``mode='auto'``
        lets the provider's own logic decide the effort.

        No-op when:
          * the model is not reasoning-capable (the adapter would
            ignore the field anyway), or
          * the caller already opted in/out explicitly (any
            ``reasoning`` with a non-``off`` mode, or an explicit
            ``off`` we must respect).

        Call this AFTER ``select_route`` so ``model_name`` is the final
        provider model, never the ``auto`` sentinel.
        """
        from app.services.providers.model_registry import get_candidate

        cand = get_candidate(model_name)
        if not (cand and cand.supports_reasoning):
            return
        existing = getattr(payload, "reasoning", None)
        if existing is not None and existing.mode != "off":
            # Caller already set a real mode — respect it.
            return
        if existing is not None and existing.mode == "off":
            # Explicit opt-out — respect it (don't force thinking on).
            return
        from app.schemas.public import ReasoningConfig

        payload.reasoning = ReasoningConfig(mode="auto", show_summary=True)

    def _reasoning_downgrade_reason(
        self, payload: ChatCompletionsRequest, model_name: str
    ) -> str | None:
        """Return a downgrade message when reasoning was requested but the
        resolved model can't reason, else None.

        The caller asked for reasoning (``reasoning.mode != 'off'``) yet
        routing/policy landed on a model whose ``supports_reasoning`` is
        False (no reasoning-capable model survived the candidate filters).
        Rather than silently dropping the intent, both the blocking and
        streaming paths surface this string so the client knows the answer
        was produced without reasoning.

        Returns None on the normal path (no reasoning requested, or the
        resolved model is reasoning-capable), so the wire is unchanged.
        Call AFTER the final route is resolved so ``model_name`` is the
        concrete provider model, never ``auto``.
        """
        rc = getattr(payload, "reasoning", None)
        if rc is None or rc.mode == "off":
            return None
        from app.services.providers.model_registry import get_candidate

        cand = get_candidate(model_name)
        if cand and cand.supports_reasoning:
            return None
        return (
            f"Reasoning was requested (mode='{rc.mode}') but the resolved "
            f"model '{model_name}' is not reasoning-capable; the answer was "
            "produced without reasoning. No reasoning-capable model was "
            "available for this request after routing and policy filtering."
        )

    async def _stash_configured_providers(
        self, payload: ChatCompletionsRequest, session, log
    ) -> None:
        """Record providers with a callable key into request metadata so the
        reasoning candidate pool can prefer reachable models (P0).

        Best-effort: any failure (no session, DB error) leaves the metadata
        absent and routing falls back to unfiltered reasoning selection —
        never breaks the request. Only ever called on the reasoning path, so
        general / mode=off routing never pays this query.
        """
        if session is None:
            return
        try:
            providers = await self.provider_key_service.providers_with_valid_keys_async(
                session
            )
            if providers:
                payload.metadata["configured_providers"] = list(providers)
        except Exception as exc:  # never let key lookup break routing
            log.warning("configured_providers_lookup_failed", error=str(exc))

    async def _smart_select(self, payload, session, log):
        """Provider-agnostic model selection for ``auto`` + routing_hints.

        Builds the candidate pool from the DB-backed model registry
        (all providers), keeps only models whose provider resolves a
        valid key right now (proactive filter — provider_keys
        valid/untested, legacy, or a non-placeholder env var), then
        delegates the capability/cost ranking to ``smart_router``.

        Returns a ``SmartSelection`` or ``None`` when no provider holds a
        usable key (caller raises a clear error). Never raises on a
        per-provider key lookup failure — that provider is just skipped.
        """
        from app.services import smart_router
        from app.services.providers.model_registry import (
            get_all_candidates,
            get_all_providers,
        )

        registry_providers = get_all_providers()
        usable: set[str] = set()
        if session is not None:
            for provider in registry_providers:
                try:
                    key = await self.provider_key_service.get_api_key_async(
                        session, provider
                    )
                except Exception as exc:  # never let one provider break routing
                    log.warning(
                        "smart_routing_key_lookup_failed",
                        provider=provider,
                        error=str(exc),
                    )
                    key = None
                if key:
                    usable.add(provider)
        else:
            # No session (defensive / test path): can't verify keys, so
            # offer the whole registry and lean on reactive dispatch
            # fallback if a key turns out bad.
            usable = set(registry_providers)

        if not usable:
            return None

        # Tested-valid subset → tie-break preference (a proven key beats an
        # untested one at equal cost, avoiding a dispatch-time 401 hop).
        proven: frozenset[str] = frozenset()
        if session is not None:
            try:
                proven = frozenset(
                    await self.provider_key_service.providers_with_valid_keys_async(
                        session
                    )
                ) & usable
            except Exception as exc:  # tie-break only — never fatal
                log.warning("smart_routing_proven_lookup_failed", error=str(exc))

        candidates = [
            c
            for c in get_all_candidates()
            if c.provider in usable and getattr(c, "supports_streaming", True)
        ]
        # Admin-configured Auto preference (system_settings, cached).
        # Falls back to the shipped default when unset/unreadable, so
        # "cheapest capable" never silently owns Auto again.
        from app.services import routing_settings

        preferred = routing_settings.get_auto_preference(session)
        return smart_router.select_model(
            payload.routing_hints,
            candidates,
            proven_providers=proven,
            preferred_models=preferred,
        )

    async def _apply_router_chain(
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
        # Auto Thinking Mode — an explicit model pick is authoritative.
        # Skip the semantic/keyword override chain entirely so the
        # caller's model (including a chat-edge auto-resolved reasoning
        # model) is never silently rewritten to something else. The
        # routers only ever shape the bare ``auto`` sentinel now. This
        # closes the prior P0 where "What is 15*37?" matched the
        # ``simple_tasks`` keyword rule and downgraded an explicit
        # Claude reasoning request to gpt-4o-mini.
        if payload.model != "auto":
            log.info("routing_decision", router="explicit", model=payload.model)
            return "explicit", None

        # Provider-agnostic smart routing (T-smart-routing). When the
        # caller sends ``routing_hints`` alongside ``model="auto"``, pick a
        # concrete model from providers that hold a valid key NOW — no
        # hardcoded default provider, and a dead / rotated key never yields
        # an empty reply because a keyless provider simply never enters the
        # pool. This sets ``payload.model`` to a concrete id and returns, so
        # ``select_route`` then resolves it on the explicit path. Older
        # clients that send no hints fall through to the legacy
        # semantic → keyword → service chain below, unchanged.
        hints = getattr(payload, "routing_hints", None)
        if hints is not None:
            selection = await self._smart_select(payload, session, log)
            if selection is not None:
                payload.model = selection.candidate.model
                log.info(
                    "smart_routing",
                    selected_model=selection.candidate.model,
                    provider=selection.candidate.provider,
                    reason=selection.reason,
                    hints=hints.model_dump(exclude_defaults=True),
                    candidates_count=selection.candidates_count,
                    filtered_count=selection.filtered_count,
                )
                return "smart", None
            # No candidate ⇒ no provider holds a usable key. Surface a
            # clear error instead of an empty stream (Fix Priority #2).
            from app.core.exceptions import ProviderUnavailableError

            log.warning("smart_routing_no_usable_provider")
            raise ProviderUnavailableError(
                "No provider with a valid API key is available for auto "
                "routing. Configure at least one provider key.",
                details={"reason": "no_usable_providers"},
            )

        # Explicit reasoning intent on an ``auto`` request forces the
        # reasoning candidate pool (P0 unlock). Without this, ``auto`` +
        # ``reasoning.mode != off`` fell through to the semantic/keyword
        # chain and was silently served by a non-reasoning model
        # (gpt-4o-mini), discarding the reasoning request with no signal —
        # the headline blocker from REASONING_SUPPORT_AUDIT.md. We mark
        # ``use_case='reasoning'`` and return early so ``select_route`` ->
        # ``RoutingService._route_auto`` restricts the pool to
        # ``supports_reasoning=True`` models. If that pool is empty the
        # router still resolves a non-reasoning model, but the downgrade is
        # now surfaced (see ``_reasoning_downgrade_reason``) instead of
        # dropped. ``mode='off'`` is untouched — non-reasoning routing is
        # byte-for-byte unchanged.
        _reasoning_cfg = getattr(payload, "reasoning", None)
        if _reasoning_cfg is not None and _reasoning_cfg.mode != "off":
            if payload.metadata is None:
                payload.metadata = {}
            payload.metadata["use_case"] = "reasoning"
            await self._stash_configured_providers(payload, session, log)
            log.info(
                "routing_decision",
                router="reasoning_explicit",
                use_case="reasoning",
                mode=_reasoning_cfg.mode,
                model=payload.model,
            )
            return "reasoning_explicit", None

        # "auto": let query complexity steer the candidate pool. A
        # COMPLEX query routes to the reasoning pool (use_case=
        # 'reasoning' — handled by RoutingService._route_auto) so the
        # selected model's native adaptive thinking can engage. Simple
        # / moderate queries keep the existing semantic/keyword chain.
        try:
            complexity = classify_query_complexity(dict_messages)
        except Exception as exc:  # never let classification break routing
            log.warning("query_classify_failed", error=str(exc))
            complexity = QueryComplexity.MODERATE
        if complexity == QueryComplexity.COMPLEX:
            if payload.metadata is None:
                payload.metadata = {}
            payload.metadata["use_case"] = "reasoning"
            await self._stash_configured_providers(payload, session, log)
            log.info(
                "routing_decision",
                router="complexity",
                complexity=complexity,
                use_case="reasoning",
                model=payload.model,
            )
            return "complexity", None

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
                    await self.keyword_rule_service.get_rules_for_request_async(
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
            # Step 2: semantic router (existing precedence). B4: the
            # embedding encode runs off the event loop.
            sem_model, sem_intent, sem_confidence = (
                await self.semantic_router.get_model_hint_async(
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

        # Legacy chain has no capability hints, so a keyword/semantic rule
        # can hand a tool-bearing request to a tools-blind model. Enforce
        # the one capability the request itself proves it needs.
        self._enforce_tool_capable_model(payload, session, log)

        log.info(
            "routing_decision",
            router=router_used,
            intent=sem_intent,
            confidence=sem_confidence,
            matched_rule=matched_rule,
            model=payload.model,
        )

        return router_used, matched_rule

    def _enforce_tool_capable_model(self, payload, session, log) -> None:
        """Swap a tools-blind pick for the Auto preference, in place.

        Only ever runs on the ``auto`` path (the caller returns early for
        an explicit model, so a user's own pick is never rewritten) and
        only when the request actually carries tool definitions. A model
        the registry doesn't know is left alone — the registry is a
        cache, not the authority on what exists.
        """
        if not getattr(payload, "tools", None):
            return
        from app.services import routing_settings
        from app.services.providers.model_registry import get_candidate

        current = get_candidate(payload.model)
        if current is None or getattr(current, "supports_tools", False):
            return
        for want in routing_settings.get_auto_preference(session):
            cand = get_candidate(want)
            if cand is not None and getattr(cand, "supports_tools", False):
                log.info(
                    "routing_tool_capability_override",
                    was=payload.model,
                    model=want,
                )
                payload.model = want
                return
        log.warning(
            "routing_tool_capability_unmet", model=payload.model
        )

    async def resolve_streaming_context(
        self,
        auth: ResolvedApiKey,
        payload: ChatCompletionsRequest,
        session: "AsyncSession | None" = None,
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
        ctx = await self.project_service.resolve_project_context_async(
            session,
            auth,
            request_id=request_id,
            policy_profile_override=payload.policy_profile_id,
        )
        # Preflight parity with the non-streaming path (1b-1d): rate
        # limit + daily quota + budget + portal credit ALL run here,
        # BEFORE DLP / router / provider work, so a streaming request
        # can no longer bypass the admission controls the non-streaming
        # path enforces. Same helper, same exceptions. Rate limiting
        # runs even when ``session`` is None (Redis only).
        await self._run_preflight_checks(auth, ctx, session)
        if session is not None:
            stream_log = logger.bind(
                request_id=request_id,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
            )
            await self._inject_project_routing_config_async(
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

    async def _select_dlp_engine_async(
        self,
        ctx: RequestContext,
        session: "AsyncSession | None",
    ) -> str:
        """Resolve the effective DLP engine for this request (async).

        Per-tenant ``tenants.dlp_engine`` override beats the global
        ``settings.dlp_engine`` when it isn't ``inherit``. Migration
        043 ensures every row has a value (default ``inherit``).
        """
        if session is None:
            return settings.dlp_engine
        try:
            from app.models.tenant import TenantORM

            tenant = await session.get(TenantORM, ctx.tenant_id)
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
        ner_skip_roles: frozenset[str] = frozenset(),
    ):
        """Route the DLP scan through the selected engine.

        - ``python`` (default) → in-process registry call.
        - ``rust`` → gRPC call to ``dlp-sidecar``. Any failure falls
          back to Python so a sidecar incident never downgrades
          coverage.
        - ``shadow`` → run BOTH in parallel, use Python on the hot
          path, log ``dlp_shadow_diff`` events.
        """
        # Native async tenant lookup for the per-tenant DLP engine override.
        engine = await self._select_dlp_engine_async(ctx, session)

        async def _python_scan():
            # B2: run the synchronous regex scan off the event loop so a
            # full-message scan (tens of ms with large bodies) does not
            # block every other coroutine on the worker.
            return await self._scan_messages_async(
                registry,
                messages,
                direction=direction,
                scan_system_messages=scan_system_messages,
                ner_skip_roles=ner_skip_roles,
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
                    ner_skip_roles=ner_skip_roles,
                )
                _rust_exc = False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dlp_rust_engine_exception", error=str(exc)[:200]
                )
                rust_result = None
                _rust_exc = True
            if rust_result is not None:
                SIDECAR_REQUEST_TOTAL.labels(
                    engine="dlp", status="success"
                ).inc()
                return rust_result
            # Fail-open to Python (audit H4: count it so a flapping DLP
            # sidecar is visible, not just logged).
            SIDECAR_FALLBACK_TOTAL.labels(
                engine="dlp", reason="error" if _rust_exc else "unavailable"
            ).inc()
            SIDECAR_REQUEST_TOTAL.labels(engine="dlp", status="fallback").inc()
            logger.warning("dlp_rust_fallback_to_python")
            return await _python_scan()

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
                        ner_skip_roles=ner_skip_roles,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dlp_shadow_rust_dispatch_failed", error=str(exc)[:200]
                )

            python_result = await _python_scan()

            if rust_task is not None:
                try:
                    rust_result = await _asyncio.wait_for(rust_task, timeout=2.0)
                    if rust_result is not None:
                        self._log_shadow_diff(python_result, rust_result, ctx)
                except Exception:  # noqa: BLE001
                    logger.info("dlp_shadow_rust_timed_out_or_errored")
            return python_result

        # Default: python
        return await _python_scan()

    async def _scan_messages_async(
        self,
        registry,
        messages,
        *,
        direction: str,
        scan_system_messages: bool,
        ner_skip_roles: frozenset[str] = frozenset(),
    ) -> DetectionResult:
        """Off-loop wrapper for ``registry.scan_messages`` (B2).

        Bounded by ``_DLP_SCAN_SEMAPHORE`` so concurrent scans can't
        exhaust the default executor. ``scan_messages`` is effectively
        read-only over shared state (module-level compiled regex;
        Presidio NER runs in the remote ner-service), so running it in
        a thread is safe.
        """
        async with _DLP_SCAN_SEMAPHORE:
            return await asyncio.to_thread(
                registry.scan_messages,
                messages,
                direction=direction,
                scan_system_messages=scan_system_messages,
                ner_skip_roles=ner_skip_roles,
            )

    async def _scan_text_as_result_async(
        self,
        text: str,
        direction: str = "output",
        *,
        ner_enabled: bool = True,
    ) -> DetectionResult:
        """Off-loop wrapper for the synchronous ``_scan_text_as_result``
        (B2) — used by the non-streaming outbound + tool-arg scans."""
        async with _DLP_SCAN_SEMAPHORE:
            return await asyncio.to_thread(
                self._scan_text_as_result,
                text,
                direction,
                ner_enabled=ner_enabled,
            )

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
    def _strip_never_restore_tokens(
        span_map: dict[str, str],
        input_enforcement: EnforcementDecision,
    ) -> dict[str, str]:
        """Filter ``span_map`` for orphan-redact: drop tokens whose
        inbound match category is in ``_NEVER_RESTORE_CATEGORIES``.

        ``redact_orphan_tokens`` keeps tokens that are in the
        ``span_map`` it receives (it assumes restore handled them).
        For secret-class categories restore was BLOCKED by Layer 2,
        so the tokens did NOT get swapped back to their originals —
        they're still on the wire. If we passed the full ``span_map``
        to the orphan pass it would conclude "restore handled it" and
        leave the bare ``<r0>`` visible to the user.
        Stripping these tokens from the filtered map flips them into
        the orphan path, which deletes them silently.

        Identity-class tokens (``pii`` / ``identity`` / …) stay in
        the filtered map so the orphan pass keeps any that survive
        restore (e.g. MASK-policy categories where the user is
        expected to see the placeholder).
        """
        if not span_map:
            return span_map
        value_to_categories: dict[str, set[str]] = {}
        for ma in input_enforcement.match_actions:
            value_to_categories.setdefault(
                ma.match.matched_text, set()
            ).add(ma.match.category)
        return {
            token: original
            for token, original in span_map.items()
            if not (
                value_to_categories.get(original, set())
                & _NEVER_RESTORE_CATEGORIES
            )
        }

    def _produce_canonical_artifact_nonstream(
        self, *, request_id, canonical_content, context, auth,
        span_map, restore_allowed_tokens, out_meta,
    ) -> None:
        """Build the canonical artifact (shadow) + gated envelope (F2).

        Inert unless a canonical-artifact flag is set. Shadow → in-memory
        build + sanitized counters, NO response/transport change. Enabled +
        managed gate → set ``out_meta['canonical_artifact']`` to the
        envelope (status ``complete`` on success, ``unavailable`` on build
        failure) — the ROUTE injects it as a top-level ``x_sran_dlp`` key on
        a custom JSONResponse, so the public response model/OpenAPI schema is
        never touched. Never mutates display content; never raw-passes.
        """
        if not (
            settings.dlp_canonical_artifact_enabled
            or settings.dlp_canonical_artifact_shadow
        ):
            return
        from app.services.dlp.canonical_artifact import (
            CanonicalArtifactFinalizer,
            STATUS_COMPLETE,
            canonical_artifact_shadow_report,
            canonical_artifact_transport_allowed,
            safe_exception_fields,
            unavailable_envelope,
        )
        from app.services.dlp.vault_v2 import build_vault_scope

        gated = (
            settings.dlp_canonical_artifact_enabled
            and canonical_artifact_transport_allowed(
                auth=auth, context=context,
                enabled=settings.dlp_canonical_artifact_enabled,
            )
        )
        try:
            scope = build_vault_scope(context) if context is not None else None
            forbidden = [
                span_map[t] for t in (restore_allowed_tokens or ())
                if t in (span_map or {})
            ]
            artifact = CanonicalArtifactFinalizer().finalize_once(
                generation_id=request_id,
                request_id=request_id,
                canonical_content=canonical_content,
                channel=(context.channel if context is not None else "api"),
                streaming=False,
                status=STATUS_COMPLETE,
                created_at=int(time.time()),
                scope_fingerprint=(scope.fingerprint() if scope else None),
                forbidden_values=forbidden,
            )
            if gated and out_meta is not None:
                out_meta["canonical_artifact"] = artifact.transport_dict()
            else:
                logger.info(
                    "dlp_canonical_artifact_shadow",
                    **canonical_artifact_shadow_report(
                        artifact, display_length=len(canonical_content or ""),
                        diverged=True,
                    ),
                )
        except Exception as exc:  # never break/raw-pass on artifact failure
            # Managed consumer still gets an explicit terminal envelope so it
            # can mark the message non-replayable (B1) — no raw/exception text.
            if gated and out_meta is not None:
                out_meta["canonical_artifact"] = unavailable_envelope(
                    request_id, "canonical_nonstream"
                )
            logger.warning(
                "dlp_canonical_artifact_failed",
                **safe_exception_fields(exc, "canonical_nonstream"),
            )

    async def _vault_v2_prepare(self, context, match_actions):
        """Reserve scope-unique tokens BEFORE the synchronous transformer.

        Returns ``(scope, reserved_range, records_list)`` or ``None`` when
        there is no valid owner scope, nothing to tokenize, or the
        reservation fails (Redis down / cap / corrupt). ``None`` makes the
        caller mask request-locally WITHOUT persistence — never raw.
        """
        from app.domain.dlp import EnforcementAction as _EA
        try:
            from app.services.dlp.vault_v2 import (
                ConversationVaultV2,
                build_vault_scope,
            )
            scope = build_vault_scope(context)
            if scope is None:
                logger.info("dlp_vault_v2_scope_missing", reason="no_persistent_vault")
                return None
            plan = sum(1 for ma in match_actions if ma.action == _EA.MASK)
            if plan <= 0:
                return None  # REDACT-only / nothing to tokenize
            vault = ConversationVaultV2(
                max_entries=settings.dlp_vault_v2_max_entries
            )
            reserved = await vault.reserve_tokens_async(scope, plan)
            return (scope, reserved, [])
        except Exception as exc:  # fail closed → request-local masking
            logger.warning("dlp_vault_v2_reserve_error", error=str(exc)[:200])
            return None

    async def _vault_v2_store_prepared(self, prepared) -> None:
        """Store entries built from allocation records (reservation-checked)."""
        scope, reserved, records = prepared
        try:
            from app.services.dlp.vault_v2 import (
                ConversationVaultV2,
                build_entries_from_records,
            )
            entries = build_entries_from_records(
                records, created_at=int(time.time())
            )
            vault = ConversationVaultV2(
                max_entries=settings.dlp_vault_v2_max_entries
            )
            await vault.internal_store_reserved_entries_async(
                scope, reserved, entries
            )
        except Exception as exc:  # never break a response on a vault bug
            logger.warning("dlp_vault_v2_store_error", error=str(exc)[:200])

    def _vault_v2_shadow(self, context, span_map, match_actions) -> None:
        """Shadow-mode readiness counters — NO Redis I/O, no output change."""
        if not settings.dlp_vault_v2_shadow:
            return
        try:
            from app.services.dlp.vault_v2 import (
                build_vault_scope,
                derive_entries_from_span_map,
                vault_v2_shadow_report,
            )
            scope = build_vault_scope(context)
            entries = derive_entries_from_span_map(
                span_map, match_actions, created_at=int(time.time())
            )
            report = vault_v2_shadow_report(scope, entries)
            logger.info(
                "dlp_vault_v2_shadow",
                scope_fp=(scope.fingerprint() if scope else None),
                **report,
            )
        except Exception as exc:
            logger.warning("dlp_vault_v2_shadow_error", error=str(exc)[:200])

    @staticmethod
    def _compute_restore_allowed_tokens(
        profile: PolicyProfile,
        span_map: dict[str, str],
        input_enforcement: EnforcementDecision,
    ) -> set[str]:
        # Token allow-list for the outbound restore step. A mask token
        # may only be restored to its original value if (a) it was minted
        # from this request's inbound span_map, (b) the active output
        # policy maps its detector category to RESTORE, AND (c) the
        # category is not in ``_NEVER_RESTORE_CATEGORIES`` (credential
        # / secret are hard-blocked here regardless of profile config —
        # see the module-level constant for the rationale). The same
        # logic runs both at section-8b (fresh provider response) and
        # on cache hit (replayed masked response).
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
            # Layer 2 defense-in-depth — secrets / credentials can
            # never round-trip, even if a custom DB profile sets
            # RESTORE for them by mistake.
            if ma.match.category in _NEVER_RESTORE_CATEGORIES:
                continue
            if output_actions.get(ma.match.category) != EnforcementAction.RESTORE:
                continue
            allowed.update(value_to_tokens.get(ma.match.matched_text, set()))
        return allowed

    @staticmethod
    def _numeric_segments_from_messages(messages, *, scan_system_messages: bool):
        """Build per-message :class:`NumericTextSegment`s for the V2 numeric
        analyzer (Phase 2A.1). Each segment carries the ORIGINAL message
        index (so the transformer binds masks to the right message), the
        role, and the message text. System messages are excluded unless
        ``scan_system_messages`` — matching registry.scan_messages — so the
        analyzer never derives a finding from a system prompt. Vision
        list-block content is flattened to its text parts. Offsets in
        findings are LOCAL to each message; context never crosses a
        message boundary. Read-only.
        """
        from app.services.dlp.numeric_context import NumericTextSegment

        segments = []
        for idx, m in enumerate(messages or []):
            role = getattr(m, "role", None)
            if role is None and isinstance(m, dict):
                role = m.get("role")
            if role == "system" and not scan_system_messages:
                continue
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if not isinstance(content, str):
                continue
            segments.append(
                NumericTextSegment(
                    message_index=idx,
                    role=role,
                    text=content,
                    source_type="direct_api",
                )
            )
        return segments

    def _scan_and_evaluate(
        self,
        detection_result: DetectionResult,
        profile,
        *,
        context: DLPRequestContext | None = None,
        text: str = "",
        segments=None,
    ) -> EnforcementDecision:
        """Run classifier + evaluator on a detection result.

        Phase 1: delegates to the shared :class:`DLPPolicyEngine` so every
        chat path (API/WebChat, stream/non-stream, tool args) makes the
        decision through one boundary. The engine preserves the legacy
        side effect of writing classified matches back onto
        ``detection_result.matches`` and, under the default flags, returns
        the legacy decision unchanged (shadow mode). ``context`` is
        optional; callers that have it pass channel / source_type /
        direction so the engine can record (and Phase 2 can specialise).

        Phase 2A: ``text`` is the plain text the findings index into; it
        is forwarded to the V2 numeric analyzer. Default "" → no numeric
        analysis (legacy behavior). Only the V2/shadow decision is shaped;
        the authoritative legacy decision is untouched.
        """
        return self.policy_engine.evaluate_decision(
            detection_result=detection_result,
            profile=profile,
            context=context,
            text=text,
            segments=segments,
        )

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

        # A tool-call assistant response carries content=None (OpenAI sets
        # content null when tool_calls are present), and the non-streaming
        # outbound scan passes that straight here. Coerce None -> "" so it
        # scans as empty (no findings) instead of crashing
        # registry.scan_text / _MASK_TOKEN_RE.finditer on NoneType.
        if text is None:
            text = ""

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
                # ``health_condition`` is context-gated for INBOUND
                # text where the user is asking about medical issues
                # ("ผู้ป่วยมีประวัติรักษาโรคเบาหวาน"). On OUTBOUND the
                # LLM uses the same Thai verbs in completely unrelated
                # contexts — "เพื่อให้การรักษาความปลอดภัย" ("to
                # MAINTAIN security"), "รักษาระดับ" ("MAINTAIN level"),
                # "รักษามาตรฐาน" ("UPHOLD standards"). The detector
                # mis-tags these as ``health_condition`` findings, the
                # outbound pass mints ``<<HEALTH_1>>``, and orphan-
                # redact strips it — leaving holes in the user's reply
                # ("เพื่อให้การ ความปลอดภัย"). Suppressing here keeps
                # the inbound coverage intact while removing the
                # outbound FP source. Surfaced by the May-2026 E2E
                # Case 4 trace.
                "health_condition",
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
