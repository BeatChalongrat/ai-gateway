// Package config loads runtime configuration from the environment.
//
// All secrets and connection strings live here; the rest of the
// service receives a fully-populated Config struct and never reads
// os.Getenv directly. This makes the privacy-critical knobs
// (JWT_SECRET, SERVICE_JWT_SECRET, CSRF_SECRET) auditable from a
// single file.
package config

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/caarlos0/env/v11"
)

// Config is the full runtime configuration for chat-edge-api.
//
// Field tags use `env:"NAME"` for the environment variable name and
// `envDefault:"…"` for fallback values. Fields without a default
// are required — missing required fields cause Load() to return an
// error and the service refuses to start.
type Config struct {
	// Deployment environment. Anything outside {development, dev, test,
	// testing} is treated as production by Validate(), which then refuses
	// to boot on dev-default secrets / non-Secure cookies.
	AppEnv string `env:"APP_ENV" envDefault:"development"`

	// HTTP listener.
	Port string `env:"PORT" envDefault:"4000"`
	// FIX119 Part C2 — a caller whose email matches this pattern is a
	// test account: identity.users.is_test is set when the user row is
	// provisioned, every conversation and message it creates or imports
	// copies the flag, and the admin analytics exclude those rows unless
	// asked (include_test=1). The probe accounts all end in "@local".
	TestAccountEmailPattern string `env:"TEST_ACCOUNT_EMAIL_PATTERN" envDefault:"@local$"`

	// PostgreSQL connection (pgx-compatible DSN).
	DatabaseURL string `env:"DATABASE_URL,required"`

	// FIX129 A1 — server-side session timeouts every pooled connection
	// carries (internal/db). On 2026-09-22 an abandoned FOR UPDATE idled
	// in its transaction for 34 minutes with all three at 0 and every
	// later writer queued behind its row lock. 0 = not enforced.
	PGStatementTimeoutMS         int `env:"PG_STATEMENT_TIMEOUT_MS" envDefault:"30000"`
	PGLockTimeoutMS              int `env:"PG_LOCK_TIMEOUT_MS" envDefault:"5000"`
	PGIdleInTransactionTimeoutMS int `env:"PG_IDLE_IN_TRANSACTION_TIMEOUT_MS" envDefault:"30000"`

	// Redis connection. Database number is part of the URL
	// (``redis://host:6379/1`` selects DB 1) so test code can
	// isolate state by pointing at a different DB.
	RedisURL string `env:"REDIS_URL,required"`

	// FIX41 — the run ledger's bounds (docs/STREAM_CONTRACT.md §8).
	//
	// TTL: how long a finished run stays resumable. Ten minutes matches
	// the window OpenAI keeps for a background run: long enough for a
	// refresh, a tunnel, or a laptop lid; short enough that this is a
	// window and not a store.
	//
	// MaxEvents: the cap on one run's frames. Measured at ~177 bytes per
	// frame (FIX41's E1 ledger-backends probe), so
	// 8000 is ~1.4 MB for a single runaway generation. Past the cap the
	// OLDEST frames go — a reattaching client needs the live tail more
	// than the beginning, and a cursor pointing into the dropped region
	// is answered 409 RESUME_TOO_OLD rather than quietly under-served.
	StreamLedgerTTLSeconds int `env:"STREAM_LEDGER_TTL_SECONDS" envDefault:"600"`
	StreamLedgerMaxEvents  int `env:"STREAM_LEDGER_MAX_EVENTS" envDefault:"8000"`

	// Upstream Gateway API base URL (no trailing slash).
	GatewayAPIURL string `env:"GATEWAY_API_URL,required"`

	// FIX132 B — the knowledge base's models (services/kb-models): embeddings,
	// rerank and Thai segmentation on this machine. chat-edge calls it
	// directly; the host must be a compose service name, loopback, an
	// RFC 1918 address or host.docker.internal (anything else is refused and
	// the knowledge base reports itself unavailable) — document text never
	// leaves the host. docs/ops/knowledge-base.md.
	KBModelsURL string `env:"KB_MODELS_URL" envDefault:"http://kb-models:8090"`
	// KB_INDEX_WORKERS — background indexing goroutines (1..4). kb-models
	// runs one request at a time, so two keep it busy while one document is
	// being extracted (OCR can take minutes).
	KBIndexWorkers int `env:"KB_INDEX_WORKERS" envDefault:"2"`
	// KB_CHUNK_SIZE / KB_CHUNK_OVERLAP — the chunker's defaults in characters
	// (runes) for new knowledge bases and for rows still on the old 512/50.
	KBChunkSize    int `env:"KB_CHUNK_SIZE" envDefault:"1000"`
	KBChunkOverlap int `env:"KB_CHUNK_OVERLAP" envDefault:"150"`
	// Retrieval (rag_service.go; defaults measured on the FIX132 eval set).
	// KB_SPARSE_WEIGHT — score = cosine + weight × bge-m3 lexical score over
	// the union of both arms' candidates; 0 = dense only.
	KBSparseWeight float64 `env:"KB_SPARSE_WEIGHT" envDefault:"0.3"`
	// KB_CANDIDATES — candidates per arm (dense, sparse) before fusion.
	KBCandidates int `env:"KB_CANDIDATES" envDefault:"50"`
	// KB_RERANK_ENABLED — rerank the fused top KB_RERANK_CANDIDATES with
	// bge-reranker-v2-m3. Off: on this CPU 30–40 passages take 20–60 s. When
	// on, KB_RERANK_BUDGET_MS bounds it; past it the fused order is used.
	// kb-models reads the same variable to decide whether to load the model.
	KBRerankEnabled    bool    `env:"KB_RERANK_ENABLED" envDefault:"false"`
	KBRerankCandidates int     `env:"KB_RERANK_CANDIDATES" envDefault:"40"`
	KBRerankBudgetMS   int     `env:"KB_RERANK_BUDGET_MS" envDefault:"4000"`
	KBRerankFloor      float64 `env:"KB_RERANK_FLOOR" envDefault:"0.01"`
	// KB_MIN_SCORE — without the reranker, a passage is given to the model
	// only if cosine + weight × lexical reaches this (measured: real
	// questions' top score ≥ 0.51, off-topic 0.32–0.50). None passes → no
	// passage at all; the model answers normally.
	KBMinScore float64 `env:"KB_MIN_SCORE" envDefault:"0.40"`
	// KB_TOP_K — passages given to the model; KB_CONTEXT_MAX_CHARS caps the
	// block (it is also held to a quarter of the model's context window).
	KBTopK            int `env:"KB_TOP_K" envDefault:"20"`
	KBContextMaxChars int `env:"KB_CONTEXT_MAX_CHARS" envDefault:"24000"`

	// Optional whole-request deadline for the gateway client (Batch A).
	// Default 0 = unbounded: Client.Timeout covers the entire streamed
	// body, so any non-zero value severs SSE generations that outlive
	// it (the old hardcoded 5m did exactly that). Dial/TLS/header waits
	// are bounded by the client's transport regardless. Set e.g. "90s"
	// only for deployments that never stream.
	GatewayClientTimeout time.Duration `env:"GATEWAY_CLIENT_TIMEOUT" envDefault:"0"`

	// JWT signing secrets. Required — the service refuses to start
	// with the dev defaults in production.
	//
	// Phase 1.5 — Portal integration:
	//   PortalJWTSecret is the load-bearing user-auth secret. It MUST
	//   match the FastAPI backend's ``portal_jwt_secret`` so tokens
	//   minted by the Portal (services/portal/auth_service.py) verify
	//   here. UserJWTSecret + CSRFSecret are retained as required so
	//   the legacy /auth/* stubs still parse env on boot but their
	//   handlers now return 410 Gone (see handler/auth.go).
	UserJWTSecret    string `env:"USER_JWT_SECRET,required"`
	ServiceJWTSecret string `env:"SERVICE_JWT_SECRET,required"`
	CSRFSecret       string `env:"CSRF_SECRET,required"`
	PortalJWTSecret  string `env:"PORTAL_JWT_SECRET,required"`

	// Token lifetimes. The access token is short-lived; the
	// refresh token is the long-lived family rotation anchor.
	AccessTokenExpiry  time.Duration `env:"ACCESS_TOKEN_EXPIRY"  envDefault:"1h"`
	RefreshTokenExpiry time.Duration `env:"REFRESH_TOKEN_EXPIRY" envDefault:"168h"`

	// Internal service JWT TTL — kept tight (60s) so a compromised
	// token has a minimal replay window.
	ServiceJWTTTL time.Duration `env:"SERVICE_JWT_TTL" envDefault:"60s"`

	// Set ``Secure`` on auth cookies. Disable in local HTTP dev
	// where the browser will refuse Secure cookies over plain HTTP.
	// Production deployments MUST keep this true.
	CookieSecure bool `env:"COOKIE_SECURE" envDefault:"false"`

	// Bearer credential the chat-edge-api uses when calling the
	// existing gateway-api. The production design carries a service
	// JWT (auth/service_jwt.go), but the current gateway-api accepts
	// only its own API-key format. Setting this env var lets dev
	// builds use a seeded gateway key; production sets it to the
	// per-environment dedicated key. Empty value → fall back to the
	// service JWT (which the gateway will reject until it learns
	// the new auth scheme — surfaces as 401 from gateway →
	// 502 PROVIDER_UNAVAILABLE at the chat-edge boundary).
	GatewayAPIKey string `env:"GATEWAY_API_KEY" envDefault:""`

	// Shared secret proving this is a TRUSTED internal caller, so the
	// gateway honours the per-request ``X-User-ID`` header for per-end-
	// user rate limiting (instead of treating all web-chat traffic as one
	// shared ``api_key.key_id`` bucket). Must match the gateway's
	// ``INTERNAL_SERVICE_SECRET``. Empty (default) → header is not sent,
	// the gateway ignores X-User-ID, and limiting stays collective —
	// safe, backward-compatible rollout: set it on BOTH sides to enable.
	InternalServiceSecret string `env:"INTERNAL_SERVICE_SECRET" envDefault:""`

	// FIX16 — cloud scheduled tasks. The unattended firing loop is OFF by
	// default (backward-compatible with every deployment that predates it);
	// turning it on is a deliberate config act.
	//   CloudSchedulerEnabled   — master switch for the firing goroutine.
	//   CloudTaskAuthorizeURL   — base URL of the backend that answers
	//                             /internal/scheduled-run/authorize (the
	//                             revocation + balance gate). REQUIRED when
	//                             enabled; empty + enabled = refuse to start.
	//   CloudSchedulerInterval  — how often Tick scans for due tasks. The
	//                             1-hour per-task floor is enforced separately;
	//                             this only bounds scan latency.
	CloudSchedulerEnabled  bool          `env:"CLOUD_SCHEDULER_ENABLED" envDefault:"false"`
	CloudTaskAuthorizeURL  string        `env:"CLOUD_TASK_AUTHORIZE_URL" envDefault:""`
	CloudSchedulerInterval time.Duration `env:"CLOUD_SCHEDULER_INTERVAL" envDefault:"60s"`

	// Max conversation messages threaded into a Gateway call. The
	// model has its own context window. The S4 ContextCompiler trims
	// history by TOKEN budget (per-model, see context_budget.go), so
	// this message count is now just a generous safety ceiling on how
	// many rows we load — not the real context limit. Kept high so the
	// token budget is the sole decider (matches Claude.ai behaviour);
	// the repo layer still hard-caps the DB load (see message_repo.go).
	MaxContextMessages int `env:"MAX_CONTEXT_MESSAGES" envDefault:"1000"`

	// FIX124 — how many tool rounds a web turn may run before the answer
	// round (tool_choice none). Each round re-sends the whole context, so
	// this is a cost knob; 5 is what it has always been.
	MaxToolIterations int `env:"MAX_TOOL_ITERATIONS" envDefault:"5"`

	// FIX33 A-W2 — auto-compact fires at a THRESHOLD, not on overflow
	// (Claude: compaction at 150K of a bigger window). When the loaded
	// history reaches TriggerRatio × the messages budget, the compiler
	// refreshes <conversation_state> and trims the middle of the
	// history down to TargetRatio × budget (the gap between the two
	// is hysteresis: without it every turn past 85 % would pay an
	// extraction call and drop one message). 1.0 restores the old
	// "only when it overflows" behaviour. Both default ON in prod.
	ContextCompactTriggerRatio float64 `env:"CONTEXT_COMPACT_TRIGGER_RATIO" envDefault:"0.85"`
	ContextCompactTargetRatio  float64 `env:"CONTEXT_COMPACT_TARGET_RATIO" envDefault:"0.60"`

	// ContextSummaryModel (FIX102 Part D) — the model that writes the
	// <conversation_state> summary when compaction folds older messages.
	// Empty (default) = the model the conversation is answered by, as
	// before. When set, it is tried first and the chat model second, so a
	// summariser that is not served, not usable by the tenant, or replies
	// with something other than the JSON never costs the summary. The call
	// goes through the gateway as the conversation's own tenant and user.
	ContextSummaryModel string `env:"CONTEXT_SUMMARY_MODEL"`

	// BackgroundTaskModel (FIX126 O1) — the model background work calls:
	// the conversation-state summariser (unless CONTEXT_SUMMARY_MODEL is
	// set) and the preference extractor. Each falls back to the
	// conversation's own model when this one produces nothing. "off"
	// restores the conversation's model for both. Title generation is not
	// affected (FIX124: the model that answered names the chat).
	// Keep the default equal to service.DefaultBackgroundTaskModel.
	BackgroundTaskModel string `env:"BACKGROUND_TASK_MODEL" envDefault:"deepseek-v4-flash"`

	// TemporaryChatReadsMemory — RETIRED in FIX130 (A2). FIX56.1 let a
	// memory-off / temporary chat read the saved memories; NIGHT2 found
	// answers quoting them in chats the user had switched memory off for.
	// Memory off now means off, in both directions, with no setting to
	// undo it. The variable is still PARSED — as a string, so no value an
	// old .env carries can stop the server booting — only so main.go can
	// warn that it is ignored.
	TemporaryChatReadsMemory string `env:"TEMPORARY_CHAT_READS_MEMORY" envDefault:""`

	// JSON-encoded list of models exposed to the chat UI. Phase 1
	// keeps this list flat (no per-tenant restriction; admin features
	// land later). Empty string → fall back to the hard-coded
	// production-safe defaults in ``DefaultModels``.
	AvailableModelsJSON string `env:"AVAILABLE_MODELS" envDefault:""`

	// Per-user message-send rate limit. INCR + EXPIRE on Redis under
	// ``ratelimit:{user_id}:{window_bucket}``. The window is the
	// floor of now/RateLimitWindow so the bucket rotates naturally.
	RateLimitMessages int           `env:"RATE_LIMIT_MESSAGES" envDefault:"30"`
	RateLimitWindow   time.Duration `env:"RATE_LIMIT_WINDOW"   envDefault:"1m"`

	// Admin-only endpoints (Session 6: observability dashboards) live
	// under ``/api/v1/admin/`` and require this bearer secret on the
	// Authorization header. Empty value disables the routes entirely
	// so a misconfigured deploy fails closed rather than exposing
	// cross-tenant data.
	AdminAPIKey string `env:"CHAT_EDGE_ADMIN_API_KEY" envDefault:""`

	// File-upload knobs. Storage root is an absolute or repo-relative
	// directory the service owns end-to-end; uploads land under
	// ``{root}/{tenant_id}/{conversation_id}/{file_id}_{name}``.
	FileStorageRoot     string `env:"FILE_STORAGE_ROOT" envDefault:"./data/uploads"`
	MaxFileSizeBytes    int64  `env:"MAX_FILE_SIZE_BYTES" envDefault:"209715200"`        // 200 MiB (raised for media gen — Sprint 1)
	MaxConvFileTotal    int64  `env:"MAX_CONV_FILE_TOTAL_BYTES" envDefault:"1073741824"` // 1 GiB (raised for media gen — Sprint 1)
	MaxFilesPerMessage  int    `env:"MAX_FILES_PER_MESSAGE" envDefault:"5"`
	ExtractPythonBinary string `env:"EXTRACT_PYTHON_BINARY" envDefault:"python3"`

	// FIX127 A2 — uploads never attached to a sent message expire.
	// UNATTACHED_FILE_TTL is the age after which such a row (and its blob)
	// is removed; UNATTACHED_FILE_SWEEP_MODE is dry_run (count + audit,
	// delete nothing — the default until the owner has read the first
	// report), delete, or off. The pass runs at boot and every
	// UNATTACHED_FILE_SWEEP_INTERVAL.
	UnattachedFileTTL           time.Duration `env:"UNATTACHED_FILE_TTL" envDefault:"48h"`
	UnattachedFileSweepMode     string        `env:"UNATTACHED_FILE_SWEEP_MODE" envDefault:"dry_run"`
	UnattachedFileSweepInterval time.Duration `env:"UNATTACHED_FILE_SWEEP_INTERVAL" envDefault:"1h"`

	// FIX132 C — the user owns their data.
	//
	// DELETED_DATA_PURGE_MODE is the ceiling for the nightly purge of what
	// users deleted (chats; and whole accounts, which the backend asks for):
	// on (default — the owner's decision of 2026-09-24), dry_run (count and
	// audit, delete nothing) or off. A tenant's own flags
	// (chat.tenant_retention_settings, migration 050) can only lower it.
	//
	// BACKUP_RETENTION_DAYS is how long the nightly backups keep both
	// databases and the uploads; it deletes nothing here, it is what the
	// Settings page tells the user ("backups roll off N days later").
	//
	// EXPORT_MAX_FILE_BYTES caps the uploaded + generated file bytes one
	// "export all my data" ZIP carries; files past the cap are listed in the
	// ZIP's README instead. EXPORT_MAX_CONVERSATIONS caps the chats.
	DeletedDataPurgeMode   string `env:"DELETED_DATA_PURGE_MODE" envDefault:"on"`
	BackupRetentionDays    int    `env:"BACKUP_RETENTION_DAYS" envDefault:"14"`
	ExportMaxFileBytes     int64  `env:"EXPORT_MAX_FILE_BYTES" envDefault:"524288000"` // 500 MiB
	ExportMaxConversations int    `env:"EXPORT_MAX_CONVERSATIONS" envDefault:"5000"`

	// FIX131 E1 — turns nobody will finish. Every streaming row holds a run
	// lease in Redis while a process runs it (RUN_LEASE_TTL, renewed every
	// TTL/3). The sweep runs at boot and every INTERRUPTED_TURN_SWEEP_INTERVAL
	// and settles, as interrupted, a row older than INTERRUPTED_TURN_MIN_AGE
	// that no process holds a lease for (seen lease-less twice).
	RunLeaseTTL                  time.Duration `env:"RUN_LEASE_TTL" envDefault:"45s"`
	InterruptedTurnSweepInterval time.Duration `env:"INTERRUPTED_TURN_SWEEP_INTERVAL" envDefault:"60s"`
	InterruptedTurnMinAge        time.Duration `env:"INTERRUPTED_TURN_MIN_AGE" envDefault:"2m"`

	// FIX131 E2 — graceful shutdown. On SIGTERM new turns get 503
	// SERVER_DRAINING; the turns in flight get CHAT_EDGE_DRAIN_TIMEOUT to
	// finish; the rest are ended as interrupted, with CHAT_EDGE_DRAIN_GRACE
	// for their endings to be written. The container's stop_grace_period
	// must be longer than both together (docker-compose.webchat.yml: 120s).
	// FIX133 A5 — 90 s (was 60): a long deepseek answer is not cut on deploy.
	// FIX133 E6a — a turn that carries knowledge-base passages is answered
	// by self-hosted models only (KB_ALLOWED_PROVIDERS, the gateway's
	// allowed_providers boundary): internal documents never leave the
	// organisation until the DLP round scans them. "allow" lifts it.
	KBExternalModels   string `env:"KB_EXTERNAL_MODELS" envDefault:"deny"`
	KBAllowedProviders string `env:"KB_ALLOWED_PROVIDERS" envDefault:"sranai"`

	DrainTimeout time.Duration `env:"CHAT_EDGE_DRAIN_TIMEOUT" envDefault:"90s"`
	DrainGrace   time.Duration `env:"CHAT_EDGE_DRAIN_GRACE" envDefault:"10s"`

	// Phase 13 Part A — hardened code-interpreter sandbox. When
	// SANDBOX_ENABLED=1 the plan-executor (excel) + doc generators run
	// inside a locked-down `docker run` of SANDBOX_IMAGE instead of
	// in-process-with-secrets. Requires the chat-edge container to reach
	// a Docker daemon (socket bind-mount) and a WORKDIR shared with the
	// daemon's host namespace (see docker-compose). Unset → legacy
	// in-process exec (dev without docker-in-docker).
	SandboxEnabled     bool   `env:"SANDBOX_ENABLED" envDefault:"false"`
	SandboxImage       string `env:"SANDBOX_IMAGE" envDefault:"ai-gateway-sandbox:latest"`
	SandboxSeccompPath string `env:"SANDBOX_SECCOMP_PATH" envDefault:"/sandbox/seccomp.json"`
	// SandboxWorkDir is the per-run staging root as chat-edge writes it.
	SandboxWorkDir string `env:"SANDBOX_WORKDIR" envDefault:"/sandbox-runs"`
	// SandboxHostWorkDir is the SAME root as the Docker daemon sees it for
	// bind mounts (host path). Equal to SandboxWorkDir only when chat-edge
	// shares the daemon's mount namespace; otherwise set to the host path
	// the shared volume is bound from.
	SandboxHostWorkDir string `env:"SANDBOX_HOST_WORKDIR" envDefault:""`
	// SandboxRuntime (FIX14 §2) is the REQUIRED OCI runtime for the sandbox —
	// e.g. "runsc" (gVisor) for a VM-grade boundary. Empty = default runc
	// (standard container). When set, jobs run under it AND refuse rather than
	// fall back if it is not actually in effect (proven from inside the run).
	SandboxRuntime string `env:"SANDBOX_RUNTIME" envDefault:""`

	// Phase B — the gateway image model the generate_image tool calls.
	// gpt-image-1 is OpenAI's current image model (DALL-E is retiring).
	ImageGenModel string `env:"IMAGE_GEN_MODEL" envDefault:"gpt-image-1"`
	// Sprint 2A — the video model the generate_video tool submits to. The
	// dev default is the mock provider (mock-video-1); Sprint 2B swaps in
	// a real Replicate model id.
	VideoGenModel string `env:"VIDEO_GEN_MODEL" envDefault:"mock-video-1"`
	// Dedicated image-generation timeout. Image models often take much
	// longer than chat completions; keep this separate from streaming chat.
	ChatEdgeImageTimeoutSeconds int `env:"CHAT_EDGE_IMAGE_TIMEOUT_SECONDS" envDefault:"180"`

	// Blob-storage backend selection.
	//
	//   STORAGE_BACKEND=local  → write to the filesystem under
	//                            FileStorageRoot (zero-config default).
	//   STORAGE_BACKEND=s3     → use S3-compatible object storage.
	//                            Honours S3_ENDPOINT for MinIO etc.
	//
	// Defaults to local so a fresh checkout boots without any S3
	// credentials configured.
	StorageBackend   string `env:"STORAGE_BACKEND" envDefault:"local"`
	S3Bucket         string `env:"S3_BUCKET" envDefault:""`
	S3Region         string `env:"S3_REGION" envDefault:"us-east-1"`
	S3Endpoint       string `env:"S3_ENDPOINT" envDefault:""`
	S3AccessKey      string `env:"S3_ACCESS_KEY" envDefault:""`
	S3SecretKey      string `env:"S3_SECRET_KEY" envDefault:""`
	S3ForcePathStyle bool   `env:"S3_FORCE_PATH_STYLE" envDefault:"false"`
	// How long presigned GET URLs stay valid. Short enough to limit
	// link-sharing damage, long enough that a slow client can finish
	// the download.
	PresignedURLExpiry time.Duration `env:"PRESIGNED_URL_EXPIRY" envDefault:"15m"`

	// DLP canonical history (Phase 4A.2.2). When ``Enabled`` is true the
	// service consumes the gateway's canonical artifact, persists it, and
	// builds provider history from ``content_canonical`` ONLY (legacy /
	// non-complete rows are excluded — never a display fallback). When
	// ``Shadow`` is true (and Enabled false) the artifact is parsed +
	// validated and divergence counters logged, but NOTHING is persisted
	// and provider history is unchanged. Defaults keep production behavior.
	DLPCanonicalHistoryEnabled bool `env:"DLP_CANONICAL_HISTORY_ENABLED" envDefault:"false"`
	DLPCanonicalHistoryShadow  bool `env:"DLP_CANONICAL_HISTORY_SHADOW" envDefault:"true"`

	// DLP display lifecycle (Phase 4A.2.4). When ``Enabled`` is true, NEW
	// assistant writes stop persisting restored RAW display into
	// ``content_masked`` — they store the tokenized canonical (provider-safe)
	// or a redacted placeholder, classified by the ``display_state`` column
	// (migration 025). The live stream is unchanged; reload uses restore-on-
	// read (Vault V2) or a tokenized/redacted fallback. ``Shadow`` reserves
	// the same observe-only semantics as the other DLP rollouts (no persisted
	// change). ``StrictLegacy`` additionally redacts pre-feature legacy rows
	// on owner reload instead of showing their stored raw display. Display
	// lifecycle requires canonical history to be enabled (it needs the
	// canonical artifact to store a provider-safe representation). Defaults
	// keep production behavior (restored display persisted, as before).
	DLPDisplayLifecycleEnabled      bool `env:"DLP_DISPLAY_LIFECYCLE_ENABLED" envDefault:"false"`
	DLPDisplayLifecycleShadow       bool `env:"DLP_DISPLAY_LIFECYCLE_SHADOW" envDefault:"true"`
	DLPDisplayLifecycleStrictLegacy bool `env:"DLP_DISPLAY_LIFECYCLE_STRICT_LEGACY" envDefault:"false"`

	// DLP Vault V2 (Phase 4A.2.5). Mirrors the Python gateway flag — chat-edge
	// only needs it to gate the restore-on-read bridge: the Go→Vault V2
	// DisplayRestorer is wired ONLY when Vault V2 is enabled (and display
	// lifecycle is enabled). Default off → restore-on-read stays a
	// tokenized/redacted fallback. chat-edge never reads Vault V2 directly.
	DLPVaultV2Enabled bool `env:"DLP_VAULT_V2_ENABLED" envDefault:"false"`

	// DLP secure calculator (Phase 4A.2.5 placeholder — NOT implemented). The
	// pre-audit gated calculator behind this flag; it stays default off/shadow
	// and inert. No calculator / <CALC_n> exists yet; this only reserves the
	// flag so the next phase doesn't churn config defaults.
	DLPSecureCalcEnabled bool `env:"DLP_SECURE_CALC_ENABLED" envDefault:"false"`
	DLPSecureCalcShadow  bool `env:"DLP_SECURE_CALC_SHADOW" envDefault:"true"`

	// Phase 4A.2.7 — tenant-scoped feature gating (mirror of the Python
	// gateway). A canary-sensitive feature is effective for a request only when
	// its flag is on AND the tenant is in scope. Modes: off (default) | allowlist
	// | global. Allowlist entries are ``tenant/project`` (chat-edge matches on
	// the tenant part, since project lives gateway-side). The Python gateway is
	// the authoritative tenant gate (it withholds canonical events for
	// non-allowlisted tenants, so chat-edge naturally persists legacy); these
	// fields add defense-in-depth + parity.
	DLPFeatureScopeMode string `env:"DLP_FEATURE_SCOPE_MODE" envDefault:"off"`
	DLPFeatureAllowlist string `env:"DLP_FEATURE_ALLOWLIST" envDefault:""`
}

// ModelEntry is one entry in the model list surfaced by GET /models.
//
// Field tags double as the wire format — the handler marshals
// “[]ModelEntry“ straight to JSON so the env-supplied list shape
// has to match the response shape.
type ModelEntry struct {
	ID           string   `json:"id"`
	Name         string   `json:"name"`
	Provider     string   `json:"provider"`
	Capabilities []string `json:"capabilities"`
}

// DefaultModels is the fallback list when “AVAILABLE_MODELS“ is
// unset. Picked to match the providers the Gateway is already
// configured for in this repo.
//
// Order matters for Auto Thinking Mode: ResolveAutoModelWithReasoning
// returns the FIRST reasoning-capable id in this list for a complex
// turn. “o3-mini“ is listed ahead of “claude-sonnet-4“ so Auto
// prefers the OpenAI o-series (valid key in this deployment) over the
// Claude row (placeholder Anthropic key) — picking Claude would route
// to an unreachable provider and error the turn.
var DefaultModels = []ModelEntry{
	{ID: "gpt-4o", Name: "GPT-4o", Provider: "openai", Capabilities: []string{"chat"}},
	{ID: "gpt-4o-mini", Name: "GPT-4o Mini", Provider: "openai", Capabilities: []string{"chat"}},
	{ID: "o3-mini", Name: "OpenAI o3-mini", Provider: "openai", Capabilities: []string{"chat", "reasoning"}},
	{ID: "o4-mini", Name: "OpenAI o4-mini", Provider: "openai", Capabilities: []string{"chat", "reasoning"}},
	{ID: "claude-sonnet-4-20250514", Name: "Claude Sonnet 4", Provider: "anthropic", Capabilities: []string{"chat"}},
}

// Load reads the environment into a Config. Returns a descriptive
// error if any required variable is missing.
func Load() (*Config, error) {
	cfg := &Config{}
	if err := env.Parse(cfg); err != nil {
		return nil, fmt.Errorf("config: %w", err)
	}
	return cfg, nil
}

// _devEnvs are the environments where insecure defaults are tolerated.
var _devEnvs = map[string]struct{}{
	"": {}, "development": {}, "dev": {}, "test": {}, "testing": {},
}

// _insecureSecretMarkers flags the shipped dev-default secret values.
var _insecureSecretMarkers = []string{"change-in-prod", "change-me"}

// _minSecretLen — 32 bytes = 256 bits, the floor for HMAC-SHA256.
const _minSecretLen = 32

// Validate refuses to boot a production deployment that still carries the
// dev-default secrets or non-Secure cookies. Mirrors the Python backend's
// config.validate_secrets() so both ends of the shared PORTAL_JWT_SECRET
// fail loudly rather than silently running on a public dev key. No-op in
// dev/test.
func (c *Config) Validate() error {
	if _, dev := _devEnvs[strings.ToLower(strings.TrimSpace(c.AppEnv))]; dev {
		return nil
	}
	var issues []string
	check := func(name, val string) {
		low := strings.ToLower(val)
		for _, m := range _insecureSecretMarkers {
			if strings.Contains(low, m) {
				issues = append(issues, name+" is set to an insecure dev default")
				return
			}
		}
		if len([]byte(val)) < _minSecretLen {
			issues = append(issues, fmt.Sprintf(
				"%s is too short (need >=%d bytes, got %d)",
				name, _minSecretLen, len([]byte(val)),
			))
		}
	}
	// PortalJWTSecret is the load-bearing user-auth secret (must match
	// FastAPI). ServiceJWTSecret signs outbound gateway calls.
	check("PORTAL_JWT_SECRET", c.PortalJWTSecret)
	check("SERVICE_JWT_SECRET", c.ServiceJWTSecret)
	if !c.CookieSecure {
		issues = append(issues, "COOKIE_SECURE must be true outside dev")
	}
	if len(issues) > 0 {
		return fmt.Errorf("insecure production config: %s", strings.Join(issues, "; "))
	}
	return nil
}

// Models returns the configured model list, falling back to
// “DefaultModels“ when “AVAILABLE_MODELS“ is unset or
// unparseable. Parseable JSON wins; malformed JSON falls back
// silently rather than refusing startup so an operator who fat-
// fingers the env var still gets a working service (the deploy log
// shows the parse error).
func (c *Config) Models() []ModelEntry {
	if c.AvailableModelsJSON == "" {
		return DefaultModels
	}
	var out []ModelEntry
	if err := json.Unmarshal([]byte(c.AvailableModelsJSON), &out); err != nil || len(out) == 0 {
		return DefaultModels
	}
	return out
}

// ModelAllowed returns true for any non-empty model id. The upstream
// Gateway is the authority on which models are actually available;
// duplicating that catalog here would just create a stale-mirror
// problem where every Gateway change forces a Chat Edge config
// rebuild. Instead we accept any id the frontend passes through and
// let the Gateway reject bogus ones at request time (the user sees
// "AI service error" with the upstream message, which is honest).
//
// "auto" is documented explicitly even though the general rule
// already covers it — it's the sentinel ResolveAutoModel reads, so
// keeping its acceptance visible at the gate prevents a future
// reader from "tightening" this back into a strict allowlist
// without understanding the contract.
//
// Note: Models() / ModelIDs() still return the OPERATOR'S curated
// catalog — that's what the dropdown shows and what Auto picks
// from. Only the GATE is permissive; recommendation stays curated.
func (c *Config) ModelAllowed(id string) bool {
	if strings.TrimSpace(id) == "" {
		return false
	}
	return true
}

// ModelIDs returns just the ids from Models(). Powers the Auto-
// model resolver in the service layer (service.ResolveAutoModel),
// which only needs the id strings to classify tier + pick a
// concrete model per-request.
func (c *Config) ModelIDs() []string {
	ms := c.Models()
	out := make([]string, 0, len(ms))
	for _, m := range ms {
		out = append(out, m.ID)
	}
	return out
}
