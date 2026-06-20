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

	// PostgreSQL connection (pgx-compatible DSN).
	DatabaseURL string `env:"DATABASE_URL,required"`

	// Redis connection. Database number is part of the URL
	// (``redis://host:6379/1`` selects DB 1) so test code can
	// isolate state by pointing at a different DB.
	RedisURL string `env:"REDIS_URL,required"`

	// Upstream Gateway API base URL (no trailing slash).
	GatewayAPIURL string `env:"GATEWAY_API_URL,required"`

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

	// Max conversation messages threaded into a Gateway call. The
	// model has its own context window. The S4 ContextCompiler trims
	// history by TOKEN budget (per-model, see context_budget.go), so
	// this message count is now just a generous safety ceiling on how
	// many rows we load — not the real context limit. Kept high so the
	// token budget is the sole decider (matches Claude.ai behaviour);
	// the repo layer still hard-caps the DB load (see message_repo.go).
	MaxContextMessages int `env:"MAX_CONTEXT_MESSAGES" envDefault:"1000"`

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
	MaxFileSizeBytes    int64  `env:"MAX_FILE_SIZE_BYTES" envDefault:"26214400"`        // 25 MiB
	MaxConvFileTotal    int64  `env:"MAX_CONV_FILE_TOTAL_BYTES" envDefault:"104857600"` // 100 MiB
	MaxFilesPerMessage  int    `env:"MAX_FILES_PER_MESSAGE" envDefault:"5"`
	ExtractPythonBinary string `env:"EXTRACT_PYTHON_BINARY" envDefault:"python3"`

	// Phase B — the gateway image model the generate_image tool calls.
	// gpt-image-1 is OpenAI's current image model (DALL-E is retiring).
	ImageGenModel string `env:"IMAGE_GEN_MODEL" envDefault:"gpt-image-1"`
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
