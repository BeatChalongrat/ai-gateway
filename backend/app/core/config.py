import warnings
from decimal import Decimal
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {"change-me", "changeme", "secret", "password", "", "dev-stable-key-change-in-prod"}
_DEV_ENVS = {"development", "test", "testing"}
# Minimum byte length for security-critical secrets. 32 bytes = 256 bits,
# the safe floor for HMAC-SHA256 / AES-256. Shorter values pass a
# string-match against _INSECURE_DEFAULTS but still offer weak entropy,
# so we reject them explicitly in production.
_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_name: str = "secure-ai-gateway"
    app_env: str = "development"
    app_port: int = 8080
    log_level: str = "INFO"
    # 3000 = admin-ui, 3001 = portal, 3002 = web-chat, 5173 = legacy vite.
    # allow_credentials=True (main.py) requires explicit origins — "*" is
    # rejected by browsers when credentials are included.
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,"
        "http://localhost:3002,http://localhost:5173"
    )

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ai_gateway"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    # Empty = unauthenticated (dev default; matches the redis service
    # in docker-compose.yml). docker-compose.prod.yml sets a value and
    # adds --requirepass on the redis service. Folded into redis_url
    # below as the AUTH segment.
    redis_password: str = ""

    # Security — jwt_secret MUST be stable across restarts (not random)
    jwt_secret: str = "change-me"
    master_encryption_key: str = "change-me"
    access_token_expire_minutes: int = 1440  # 24 hours

    # AES-256-GCM master key for provider API keys at rest (64 hex chars
    # = 32 bytes). Empty in dev/test (effective_master_key falls back to a
    # deterministic dev key); REQUIRED in production — validate_secrets()
    # blocks startup when it's missing or not 32 bytes of hex.
    provider_key_master_key: str = ""

    # Rate-limit behaviour when Redis is unreachable. "fail_open" (default)
    # admits unlimited — rate limiting is a safety net, not an auth gate.
    # "conservative" applies a small in-process per-key limit instead, for
    # deployments where cost control outweighs availability during an outage.
    rate_limit_redis_outage_mode: str = "fail_open"

    # --- Rate-limit ceilings (env-overridable; Growth-tier defaults) -----
    # The per-API-key / per-end-user window stays at 60/min. The project +
    # tenant ceilings were raised from the legacy 300 / 1000 because a
    # SHARED web-chat API key fans MANY end-users through ONE key_id: the
    # per-end-user keying (see app.api.deps.resolve_rate_limit_key_id) now
    # gives each trusted web-chat user their own 60/min bucket, so the
    # project/tenant caps act only as the aggregate backstop. Override per
    # deployment via the RATE_LIMIT_*_PER_MINUTE env vars.
    rate_limit_key_per_minute: int = 60
    rate_limit_project_per_minute: int = 3000
    rate_limit_tenant_per_minute: int = 10000

    # Trusted internal-service signal. When chat-edge (or any internal
    # caller) presents ``X-Internal-Service-Secret`` matching this value,
    # the gateway HONOURS its ``X-User-ID`` header for per-end-user rate
    # limiting. Empty (default) => X-User-ID is NEVER trusted, so an
    # external API-key client cannot forge its own per-user bucket.
    internal_service_secret: str = ""

    # Image-generation cost-control ceilings (gpt-image-1 has direct $
    # exposure, so the endpoint must never be unthrottled). Per-end-user
    # hourly + per-tenant daily windows, enforced via the same Redis
    # sliding-window backend the request limiter uses.
    image_rate_limit_user_per_hour: int = 10
    image_rate_limit_tenant_per_day: int = 100

    # Portal JWT — separate secret so leaking the admin secret does not
    # forge portal tokens (and vice versa). Payload carries type="portal".
    portal_jwt_secret: str = "change-me-portal-secret"
    portal_access_token_expire_minutes: int = 60
    # 90 days. Rotation (auth_service.refresh) re-issues the refresh
    # token on every renew, so an actively-used session never hits this
    # ceiling — it only bounds an account that's been idle for 3 months.
    portal_refresh_token_expire_days: int = 90

    # Portal auth cookies (HttpOnly cookie migration). The backend now
    # sets the access + refresh tokens as HttpOnly cookies instead of
    # returning them in the JSON body, so JS (and any XSS) can't read
    # them. See app/services/portal/cookies.py.
    #   * auth_cookie_domain: empty = host-only. On localhost a host-only
    #     cookie is shared across ports (:3001/:3002/:4000/:8080), which
    #     is exactly the cross-app sharing we want in dev. In production
    #     set ".yourdomain.com" to share across sub-domains.
    #   * auth_cookie_secure: None = auto (Secure outside dev/test). Set
    #     explicitly to force on/off.
    #   * auth_cookie_samesite: "lax" forwards cookies on same-site
    #     navigation + fetch (all localhost ports are same-site) while
    #     blocking cross-site programmatic POSTs.
    auth_cookie_domain: str = ""
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: str = "lax"

    # Admin session (Phase 2A): Redis-backed sessions keyed by signed
    # HttpOnly cookie. SESSION_SIGNING_SECRETS is a comma-separated list;
    # the first entry is used to sign new cookies, later entries remain
    # valid for verification (zero-downtime key rotation). Any entry in
    # _INSECURE_DEFAULTS is rejected outside dev by validate_secrets().
    session_signing_secrets: str = "change-me-session-signing"
    session_cookie_name: str = "admin_session"
    session_idle_ttl_seconds: int = 86400  # 24h sliding
    session_absolute_ttl_seconds: int = 604800  # 7d hard cap
    admin_ui_origin: str = "http://localhost:3001"
    portal_origin: str = "http://localhost:3010"

    # Server-side OAuth (Phase 2A Commit 4, P0-8 fix). Client secrets
    # stay on the backend; the portal never touches them. Empty values
    # mean the matching provider's /oauth/start endpoint will fail the
    # fetch_token call — that's the correct behaviour on a dev box that
    # hasn't set up OAuth yet.
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""

    # Provider API keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    minimax_api_key: str = ""
    qwen_api_key: str = ""
    kimi_api_key: str = ""
    zai_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    xai_api_key: str = ""
    mistral_api_key: str = ""
    cohere_api_key: str = ""
    perplexity_api_key: str = ""

    # Usage-display credit conversion (USAGE_CREDITS_PER_USD env).
    # The portal's per-model usage breakdown converts each model's real
    # logged cost (request_logs.estimated_cost_usd) into display
    # credits: credits = cost_usd * this rate. 3,000,000/USD anchors
    # 1 credit ≈ 1 blended gpt-4o-mini token, so budget models burn
    # ~1 credit/token and premium models the ~10–30x their pricing
    # implies. DISPLAY ONLY — the credit_balance debit stays 1
    # credit = 1 token (see credit_service.debit) until that change
    # is explicitly approved.
    usage_credits_per_usd: int = 3_000_000

    # Video generation (Sprint 2A). Feature-flagged OFF by default; the
    # internal video route returns 403 when disabled. A submit response
    # sets ``requires_confirmation`` when the estimated USD cost meets this
    # threshold so the frontend can show a confirm modal.
    video_gen_enabled: bool = False
    video_gen_default_threshold_usd: float = 1.00

    # --- Replicate video provider (Sprint 2B) --------------------------
    # ALL THREE cost guards (L1 session budget, L2 tenant daily cap, L3
    # per-job confirmation) are mandatory — there is NO bypass flag. To
    # raise a limit, change the DB column (tenants.max_daily_spend_usd),
    # which leaves an audit trail. The token + webhook secret use
    # ``repr=False`` so they never appear in ``repr(settings)`` or an
    # accidental settings dump in logs.
    replicate_api_token: str | None = Field(default=None, repr=False)
    # ElevenLabs TTS env fallback (Sprint 4). ``repr=False`` so the key never
    # appears in ``repr(settings)``. Prefer the UI provider-key flow; this is
    # the dev/CI fallback (resolution priority: provider_keys → legacy → env).
    elevenlabs_api_key: str | None = Field(default=None, repr=False)
    # L1: session-wide spend ceiling (USD) enforced atomically in Redis.
    replicate_test_budget_usd: Decimal = Field(default=Decimal("5.00"))
    # Hard server-side duration clamp — a request asking for 60s is capped
    # to this regardless, so a bad client can't 6x the cost.
    replicate_max_video_duration_sec: int = 10
    replicate_request_timeout_sec: int = 600
    replicate_webhook_secret: str | None = Field(default=None, repr=False)
    # Public URL the gateway is reachable at; used to build the Replicate
    # webhook callback. Empty → no webhook registered (poll-only fallback).
    replicate_webhook_base_url: str = ""
    replicate_poll_interval_sec_no_webhook: int = 5
    replicate_poll_interval_sec_with_webhook: int = 15

    # Provider timeouts and retry
    provider_timeout_connect: float = 5.0
    provider_timeout_read: float = 30.0
    provider_max_retries: int = 2
    image_generation_timeout_seconds: float = 180.0
    openai_image_timeout_seconds: float = 180.0

    # Provider base-URL overrides (used by perf-baseline to re-target all
    # outbound calls at the local mock_provider). JSON dict keyed by
    # provider_name; unset keys fall through to each adapter's hardcoded
    # default. Empty string disables the feature. Example:
    #   PROVIDER_BASE_URL_OVERRIDES='{"openai":"http://localhost:9090",
    #                                  "anthropic":"http://localhost:9090"}'
    provider_base_url_overrides: str = ""

    # DLP
    dlp_enabled: bool = True
    dlp_default_profile: str = "default"
    dlp_disabled_detectors: str = ""
    dlp_scan_responses: bool = True
    # Phase 1 — Central DLP Policy Engine rollout flags.
    # ``shadow`` (default True): the V2 engine computes a decision in
    # parallel with the legacy seam and logs any divergence, but the
    # LEGACY decision is the one that takes effect — zero behavior change.
    # ``enabled`` (default False): makes the V2 engine authoritative.
    # Both are read on every evaluation, so toggling them takes effect on
    # the next request (config-only rollback, no redeploy). In Phase 1 the
    # V2 decision reuses the same classifier + PolicyEvaluator, so it is
    # action-identical to legacy for the current fixtures.
    dlp_policy_engine_v2_enabled: bool = False
    dlp_policy_engine_v2_shadow: bool = True
    # Phase 1 blocker B1 — AI Sensitive-Data Reviewer egress trust
    # boundary. The reviewer receives the post-DLP, pre-restore output,
    # which may still contain non-secret PII the detectors MISSED (that
    # is the feature's purpose). Sending that to an arbitrary operator-
    # set external host is unsafe, so egress is gated by host class:
    #   - internal/local host (localhost, private IP, single-label docker
    #     service name, *.internal/.local/.svc) → always allowed.
    #   - host in ``ai_review_allowed_hosts`` → trusted, allowed.
    #   - any other external host → BLOCKED (review skipped, fail-closed)
    #     unless ``ai_review_allow_external`` is explicitly true.
    # Set ``ai_review_require_trusted_endpoint=false`` to disable the gate
    # entirely (legacy behavior; not recommended).
    ai_review_require_trusted_endpoint: bool = True
    ai_review_allow_external: bool = False
    ai_review_allowed_hosts: str = ""  # comma-separated hostnames
    # P1 Item 1 — Thai NER false-positive guard. The shipped image has an
    # English-only spaCy model (no th_core_news_*), so Presidio NER run on
    # Thai prose mis-tags common words ("สวัสดีครับ", "อัตราแลกเปลี่ยน") as
    # PERSON/NRP. When ``dlp_ner_thai_enabled`` is False (default) we skip
    # Presidio NER for text that is predominantly Thai (≥
    # ``dlp_ner_thai_min_ratio`` Thai chars) — the regex Thai detectors
    # (thai_name, thai_national_id, phone, …) still cover real Thai PII.
    # Flip to True once a Thai spaCy model / Thai-capable NER service is
    # deployed.
    dlp_ner_thai_enabled: bool = False
    dlp_ner_thai_min_ratio: float = 0.30

    # P1 Item 4 — Anthropic prompt caching. When True (default) the
    # Anthropic adapter sends the system prompt as a content-block array
    # with ``cache_control: {type: ephemeral}`` so Claude caches the large,
    # stable SRAN persona across turns (cached reads bill at ~10% of the
    # input rate). The OpenRouter adapter applies the same breakpoint for
    # ``anthropic/*`` models. No-op for non-Anthropic providers.
    anthropic_prompt_caching_enabled: bool = True
    # Days to retain raw PII content (input_raw / output_raw / matched_text
    # in dlp_findings_json) on request_logs rows. After this window,
    # scripts/dlp_retention_cleanup.py NULL-ifies the raw columns and
    # strips matched_text from each finding — leaving the masked
    # snapshots, summary counts, and per-finding metadata intact for
    # analytics and post-hoc audits.
    dlp_raw_retention_days: int = 90

    # NER microservice — the gateway delegates Presidio NER to a
    # dedicated service so the heavy spaCy model loads once per pod
    # instead of once per worker. Leave the URL empty to fall back to
    # in-process Presidio (the legacy path) on dev machines without
    # docker-compose. The client treats any error as "no entities"
    # so DLP regex detectors continue to fire even if the service is
    # unreachable.
    ner_service_url: str = ""
    ner_service_timeout_seconds: float = 2.0
    # NER service worker count — the ner-service container uses this
    # to size its uvicorn worker pool (Dockerfile CMD reads
    # ``${NER_WORKERS}``). Documented here so a single .env file
    # controls the whole stack's tuning; the backend process itself
    # does not consume the value.
    ner_workers: int = 4

    # Archive retention — rows older than this are moved from
    # ``request_logs`` to ``request_logs_archive`` by the daily job
    # in ``app.jobs.log_archive_job``. 90 days matches the DLP raw
    # retention window so cold storage never holds raw PII for
    # longer than the live table did.
    log_archive_days: int = 90

    # C2 — model discovery sync behaviour.
    # ``model_discovery_auto_add`` False (default) makes the daily sync
    # LOG newly-listed upstream models for admin review instead of
    # auto-inserting them into the routable pool with un-vetted
    # pricing/capabilities. Flip True to restore eager auto-add.
    # ``model_discovery_health_check`` gates an optional 1-token live
    # probe per model during sync (off by default — a daily probe of
    # every model is a paid request per model). The admin "Test" button
    # runs the same probe on demand regardless of this flag.
    model_discovery_auto_add: bool = False
    model_discovery_health_check: bool = False

    # Phase-1 async request-log queue (``app.services.request_log_queue``).
    # The orchestrator's success path enqueues records instead of
    # synchronously persisting; a background worker started by the
    # lifespan hook drains the queue every ``flush_interval_ms`` (or
    # whenever ``batch_size`` is reached) and bulk-inserts. ``queue_max``
    # caps memory pressure under a Postgres outage — once the queue is
    # full, the oldest record is dropped to make room.
    request_log_batch_size: int = 50
    request_log_flush_interval_ms: int = 100
    request_log_queue_max: int = 10000

    # Phase-3 Go log-worker sidecar.
    #
    # ``log_worker_engine`` selects which sink ``request_log_queue.enqueue``
    # dispatches to:
    #
    #   * "python" (default) — keeps the legacy in-process asyncio
    #     queue + ORM-based bulk insert. Backward compatible; no
    #     external service required.
    #   * "go" — fire-and-forget gRPC to the ``log-worker`` sidecar,
    #     which buffers + COPY-inserts on its own connection pool.
    #     Falls back to the legacy persist on the caller's session if
    #     the RPC times out so a sidecar outage cannot lose audit rows.
    #
    # The address is the in-cluster DNS name; the timeout is small on
    # purpose (audit writes are fire-and-forget and the gateway must
    # never block on a slow sidecar).
    log_worker_engine: str = "python"
    log_worker_grpc_address: str = "log-worker:50052"
    log_worker_grpc_timeout_seconds: float = 0.5

    # Phase-3b Rust rate-limiter sidecar.
    #
    # ``rate_limiter_engine`` selects which implementation handles
    # ``RateLimitService.check_all_async``:
    #
    #   * "python" (default) — keeps the legacy in-process Redis
    #     ZSET pipeline that ships with the gateway. No external
    #     service required.
    #   * "rust" — dispatches one CheckAll gRPC to the
    #     ``rate-limiter`` sidecar. On *any* failure (timeout,
    #     RPC error, sidecar unreachable, grpcio import error)
    #     the service falls back to the Python pipeline so a
    #     sidecar outage cannot block requests.
    #
    # Default ``python`` keeps the behaviour identical to the
    # pre-Phase-3b runtime.
    rate_limiter_engine: str = "python"
    rate_limiter_grpc_address: str = "rate-limiter:50053"
    rate_limiter_grpc_timeout_seconds: float = 0.5

    # Phase-3c Rust DLP sidecar.
    #
    # ``dlp_engine`` selects which engine the orchestrator runs:
    #
    #   * "python" (default) — keeps the legacy in-process registry.
    #     No external service required.
    #   * "rust" — dispatches every scan to ``dlp-sidecar`` over
    #     gRPC. On any failure (timeout, RpcError, sidecar
    #     unreachable, grpcio import error) the orchestrator falls
    #     back to the Python registry so a sidecar incident cannot
    #     downgrade DLP coverage.
    #   * "shadow" — runs BOTH engines in parallel, uses the Python
    #     result on the hot path, and logs a structured ``dlp_shadow_diff``
    #     event whenever the two engines disagree. Cutover from
    #     ``shadow`` → ``rust`` is gated on diff_count = 0 across a
    #     7-day sample of production traffic (see Phase 3c brief).
    #
    # Per-tenant override lives on ``tenants.dlp_engine`` (migration
    # 043). Tenants with ``dlp_engine != "inherit"`` ignore this
    # global setting.
    dlp_engine: str = "python"
    dlp_grpc_address: str = "dlp-sidecar:50051"
    dlp_grpc_timeout_seconds: float = 1.0
    # Re-audit H11: the Rust sidecar ports only the regex/heuristic
    # detectors — Presidio NER (PERSON/LOCATION/ORG/GPE/NRP) is Python-
    # only. With this True (default), the rust DLP client runs the Python
    # NER detector as a post-pass and merges its findings, so flipping
    # DLP_ENGINE=rust does NOT silently drop NER coverage. Set False to
    # accept the NER gap (rust regex only) — logged at startup.
    dlp_ner_on_rust_path: bool = True

    # Semantic router (intent classification via sentence-transformers).
    # Off by default — flipping this to true requires the optional
    # sentence-transformers + torch stack (see requirements-semantic.txt).
    semantic_router_enabled: bool = False
    semantic_router_model: str = "all-MiniLM-L6-v2"
    semantic_router_threshold: float = 0.65
    # Perf-baseline only: when ``SEMANTIC_ROUTER_MOCK=1`` the encoder is
    # replaced by a deterministic hash-seeded 384-dim vector + calibrated
    # lognormal sleep (p50=200ms, p95=600ms, p99=1500ms). Avoids torch /
    # sentence-transformers install on the runner. See
    # PERF_BASELINE_PLAN.md §5.7 (Variant C) for rationale.
    semantic_router_mock: bool = False

    # Optional full DATABASE_URL override (used in Docker / production).
    # When set, takes precedence over individual postgres_* fields.
    database_url_override: str = ""

    # Email (Phase 3.5 — Resend transactional provider)
    # ``RESEND_API_KEY`` is mandatory in production; validate_secrets
    # refuses to boot without it. Dev/test leaves the sender in
    # "log-to-console" mode and never calls the real API.
    resend_api_key: str = ""
    email_from_address: str = "no-reply@sran.ai"
    email_from_name: str = "SRAN AI Gateway"
    # Absolute URL to prefix verification / password-reset links with.
    # Must match the portal origin the user browses from.
    portal_public_url: str = "http://localhost:3010"

    # Cost display — ``estimated_cost_usd`` stays the canonical
    # numeric on every response / DB row, but UIs in the TH market
    # need a Baht surface alongside. ``display_currency`` selects
    # the secondary unit shown in API responses; ``usd_to_thb_rate``
    # is the offline fallback when the live exchangerate-api lookup
    # fails or is disabled. Pydantic-settings reads from
    # ``DISPLAY_CURRENCY`` / ``USD_TO_THB_RATE`` env vars.
    display_currency: str = "THB"
    usd_to_thb_rate: float = 35.0

    # DLP system-prompt variant (A/B-able).
    #
    #   * ``v1`` (default) — verbose "Critical rule: do not invent"
    #     copy that has shipped to date. Contains a ``[CATEGORY_NUMBER]``
    #     example token; regression tests in test_dlp_prompt.py lock
    #     in this shape.
    #   * ``v3`` — drops the literal example token to remove the
    #     pattern seed the May 2026 50-request audit identified as
    #     the leading hallucination driver (~48% of replies invented
    #     tokens). Positive-frame instruction in place of the prior
    #     negative list.
    #
    # Switch per-deployment via ``DLP_PROMPT_VARIANT=v3`` to A/B.
    # Unknown values silently fall back to v1.
    dlp_prompt_variant: str = "v1"

    # DLP deterministic-tokenization secret.
    # When a request supplies ``metadata.conversation_id``, the DLP
    # transformer derives a per-conversation HMAC key from this
    # secret and uses it to mint opaque, content-stable tokens of
    # the form ``[CATEGORY_xxxxxxxx]`` (8-char base64url suffix).
    # Same PII value in the same conversation always produces the
    # same token; different conversations produce different tokens.
    # MUST be replaced in production — generate with
    # ``python3 -c "import secrets; print(secrets.token_hex(32))"``.
    dlp_hmac_key: str = "dev-dlp-hmac-key-change-in-production"
    # Redis-backed cross-turn token vault stores raw token->original
    # mappings. Required for safe multi-turn DLP: when turn 2's history
    # echoes a turn-1 ``<rN>`` token, the vault lets the transformer
    # resolve it back to the original value instead of leaking the
    # placeholder. Clients SHOULD send ``metadata.conversation_id`` for
    # deterministic per-conversation tokenization. Without that header
    # the gateway falls back to per-request counter mode — no PII
    # leaks, but multi-turn history may receive newly minted tokens.
    dlp_conversation_vault_enabled: bool = True

    # Phase 4A.1 — Scoped Vault V2 rollout flags. V2 binds the Redis key
    # to the authenticated owner scope (tenant/project/actor/conversation)
    # and authorization-checks every restore. DEFAULTS MUST NOT CHANGE
    # PRODUCTION BEHAVIOR:
    #   ``enabled`` (default False): when off, the legacy unscoped vault
    #     path is used byte-for-byte; V2 never reads/writes Redis.
    #   ``shadow`` (default True): with V2 disabled, the pipeline may build
    #     + validate a VaultScope and emit sanitized readiness counters,
    #     but performs NO V2 Redis read/write and changes no token/output.
    # When ``enabled`` is True (test/opt-in process), the pipeline uses the
    # scoped V2 store ONLY — no legacy key read/write, no fallback,
    # migration is expiry-only.
    dlp_vault_v2_enabled: bool = False
    dlp_vault_v2_shadow: bool = True
    # Bounded per-scope entry ceiling (safety backstop, not a product cap).
    dlp_vault_v2_max_entries: int = 5000

    # Phase 4A.2.1 — Dual canonical/display response artifact rollout.
    # DEFAULTS MUST NOT CHANGE PRODUCTION BEHAVIOR:
    #   ``enabled`` (default False): when off, no internal canonical
    #     extension/event is produced; public JSON + SSE are byte-identical.
    #   ``shadow`` (default True): the gateway builds + validates the
    #     canonical artifact IN MEMORY and logs sanitized counters only —
    #     NO response/field/event change, NO persistence, NO transport.
    # Transport (extension/event) additionally requires the managed
    # internal-trusted WebChat gate; public Direct API never receives it.
    # Decoupled from the Vault V2 flags on purpose.
    dlp_canonical_artifact_enabled: bool = False
    dlp_canonical_artifact_shadow: bool = True

    # Phase 4A.2.6 — Secure Calculator. When ``enabled`` the secure-calc path
    # may compute over Vault V2 ``calculation_eligible`` confidential-numeric
    # tokens and mint result tokens ``<CALC_n>`` (raw operands/result never
    # egress to the provider; results restore only on the authorized owner
    # display path). REQUIRES policy-v2 + vault-v2 + canonical-artifact +
    # canonical-history + display-lifecycle to also be enabled (composed gate);
    # any missing precondition denies the calculator and chat proceeds normally.
    # ``shadow`` reserves observe-only semantics. Defaults keep the calculator
    # fully inert.
    dlp_secure_calc_enabled: bool = False
    dlp_secure_calc_shadow: bool = True

    # Phase 4A.2.7 — Tenant-scoped feature gating. A canary-sensitive DLP
    # feature is effectively enabled for a request ONLY when its global flag is
    # true AND the request's (tenant_id/project_id) passes the scope check:
    #   off       → no tenant gets the feature (default; safe).
    #   allowlist → only ``tenant_id/project_id`` pairs in DLP_FEATURE_ALLOWLIST.
    #   global    → all managed tenants (FULL rollout only; future).
    # Missing tenant/project or malformed mode/allowlist → fail closed (off).
    # Direct API secure-calc stays denied regardless. Same semantics mirrored in
    # the Go chat-edge config.
    dlp_feature_scope_mode: str = "off"
    dlp_feature_allowlist: str = ""

    # Pseudonymization layer — **default-on for production**.
    # When True (default), identity-class detectors (email, phone,
    # names, IDs, addresses, DOB, etc.) emit readable per-category
    # tokens like ``<<PERSON_A>>`` / ``<<EMAIL_1>>`` instead of opaque
    # ``<rN>``, which lets the LLM reason about entity relationships
    # ("the same person") without seeing raw values. Secret-class
    # detectors keep the ``<rN>`` shape (and are never restored to the
    # user). The ``income`` detector is bypassed entirely so the LLM
    # can compute on the raw value.
    # Set to False to revert to the legacy ``<rN>`` shape on every
    # detector — kept as an escape hatch for downgrade scenarios.
    dlp_pseudonym_mode: bool = True

    # AES-256-GCM key for the admin Safe Trace audit channel
    # (``request_logs.input_raw_encrypted``). The orchestrator
    # encrypts the user's raw prompt with this key before masking; the
    # ``/admin/requests/{id}/trace`` endpoint decrypts on demand for
    # the admin UI. Held only in the backend process — leaking a DB
    # snapshot alone does not reveal the raw prompts.
    #
    # Empty in dev (auto-derived from ``master_encryption_key`` via
    # HKDF in :mod:`app.core.dlp_audit_crypto` so the dev box just
    # works); production deploys MUST set a dedicated 32-byte value.
    # Generate with ``python3 -c "import secrets; print(secrets.token_hex(32))"``.
    dlp_audit_key: str = ""

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            from urllib.parse import quote
            return f"redis://:{quote(self.redis_password, safe='')}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def session_signing_secrets_list(self) -> list[str]:
        """Parsed rotation list. First entry signs, all entries verify."""
        return [s.strip() for s in self.session_signing_secrets.split(",") if s.strip()]

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cookie_secure_effective(self) -> bool:
        """Resolve whether auth cookies get the Secure flag.

        ``auth_cookie_secure`` overrides when set explicitly; otherwise
        Secure is on everywhere except dev/test (so localhost http works
        but any real deployment gets Secure cookies automatically).
        """
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.app_env.lower() not in _DEV_ENVS

    @property
    def provider_base_url_overrides_map(self) -> dict[str, str]:
        """Decode ``PROVIDER_BASE_URL_OVERRIDES`` JSON into a mapping.

        Parse failures are silently treated as "no override" so a typo in
        the env var cannot knock production off the real upstream. We
        emit a one-time warning on malformed input via ``warnings.warn``
        to make the misconfiguration discoverable in logs.
        """
        raw = (self.provider_base_url_overrides or "").strip()
        if not raw:
            return {}
        try:
            import json as _json

            data = _json.loads(raw)
        except Exception:
            warnings.warn(
                "PROVIDER_BASE_URL_OVERRIDES is not valid JSON — ignoring.",
                RuntimeWarning,
                stacklevel=2,
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v).rstrip("/")
            for k, v in data.items()
            if isinstance(v, (str,)) and v
        }

    @property
    def dlp_disabled_detectors_list(self) -> list[str]:
        return [d.strip() for d in self.dlp_disabled_detectors.split(",") if d.strip()]

    @property
    def effective_master_key(self) -> str:
        """The AES-256-GCM master key for provider-key encryption.

        Uses ``provider_key_master_key`` when set. In dev/test ONLY it
        falls back to a deterministic all-zero key so a fresh checkout
        works without ceremony. In production an empty key is rejected by
        ``validate_secrets`` at startup, so this never returns the dev key
        there.
        """
        if self.provider_key_master_key:
            return self.provider_key_master_key
        if self.app_env.lower() in _DEV_ENVS:
            return "0" * 64
        raise RuntimeError("PROVIDER_KEY_MASTER_KEY is not configured")

    def validate_secrets(self) -> None:
        """Reject insecure default / too-short secrets outside dev/test.

        Raises RuntimeError if critical secrets are left at placeholder
        values or fall below ``_MIN_SECRET_LENGTH``. This MUST be called
        at process startup (``app.main`` does so at import time) so a
        misconfigured production deploy never accepts a single request.
        """
        if self.app_env.lower() in _DEV_ENVS:
            return  # Allow insecure defaults in dev/test

        issues: list[str] = []

        def _check(name: str, value: str) -> None:
            if value.lower() in _INSECURE_DEFAULTS:
                issues.append(f"{name} is set to an insecure default")
            elif len(value.encode("utf-8")) < _MIN_SECRET_LENGTH:
                issues.append(
                    f"{name} is too short "
                    f"(need ≥{_MIN_SECRET_LENGTH} bytes, got {len(value.encode('utf-8'))})"
                )

        _check("JWT_SECRET", self.jwt_secret)
        _check("MASTER_ENCRYPTION_KEY", self.master_encryption_key)
        _check("PORTAL_JWT_SECRET", self.portal_jwt_secret)
        # Required in production so the Safe Trace audit channel is
        # not silently encrypted with an HKDF-of-master fallback that
        # would be invalidated if the master key ever rotated.
        if not (self.dlp_audit_key or "").strip():
            issues.append(
                "DLP_AUDIT_KEY is empty — set a 32-byte hex value "
                "(generate with `python3 -c \"import secrets; print(secrets.token_hex(32))\"`)"
            )
        else:
            _check("DLP_AUDIT_KEY", self.dlp_audit_key)

        signing = self.session_signing_secrets_list
        if not signing:
            issues.append("SESSION_SIGNING_SECRETS is empty")
        else:
            for idx, s in enumerate(signing):
                _check(f"SESSION_SIGNING_SECRETS[{idx}]", s)

        # Phase 3.5 — Resend is mandatory in production so the
        # verification + password-reset flows aren't silently dropped.
        # Dev/test runs with the console-log sender and doesn't need
        # a real key.
        if not (self.resend_api_key or "").strip():
            issues.append(
                "RESEND_API_KEY is empty "
                "(required in production — set a Resend API key or "
                "switch APP_ENV=development to use the console sender)"
            )

        # Provider API keys are encrypted at rest with AES-256-GCM
        # (app.core.security.encrypt_provider_key). Without the master key
        # the service can't decrypt stored keys; the dev all-zero fallback
        # is not acceptable in production.
        if not (self.provider_key_master_key or "").strip():
            issues.append(
                "PROVIDER_KEY_MASTER_KEY is empty — set a 32-byte hex value "
                "(generate with `python3 -c \"import secrets; print(secrets.token_hex(32))\"`)"
            )
        else:
            try:
                if len(bytes.fromhex(self.provider_key_master_key)) != 32:
                    issues.append(
                        "PROVIDER_KEY_MASTER_KEY must be 32 bytes (64 hex chars)"
                    )
            except ValueError:
                issues.append("PROVIDER_KEY_MASTER_KEY must be valid hex (64 hex chars)")

        if issues:
            raise RuntimeError(
                "STARTUP BLOCKED — insecure secrets detected:\n"
                + "\n".join(f"  - {i}" for i in issues)
                + "\n\nSet strong secret values or set APP_ENV=development for local use."
            )


settings = Settings()
