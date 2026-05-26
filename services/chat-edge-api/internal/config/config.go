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

	// Max conversation messages threaded into a Gateway call. The
	// model has its own context window; we cap at the service layer
	// to keep latency + cost bounded.
	MaxContextMessages int `env:"MAX_CONTEXT_MESSAGES" envDefault:"50"`

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
	MaxFileSizeBytes    int64  `env:"MAX_FILE_SIZE_BYTES" envDefault:"26214400"`     // 25 MiB
	MaxConvFileTotal    int64  `env:"MAX_CONV_FILE_TOTAL_BYTES" envDefault:"104857600"` // 100 MiB
	MaxFilesPerMessage  int    `env:"MAX_FILES_PER_MESSAGE" envDefault:"5"`
	ExtractPythonBinary string `env:"EXTRACT_PYTHON_BINARY" envDefault:"python3"`
}

// ModelEntry is one entry in the model list surfaced by GET /models.
//
// Field tags double as the wire format — the handler marshals
// ``[]ModelEntry`` straight to JSON so the env-supplied list shape
// has to match the response shape.
type ModelEntry struct {
	ID           string   `json:"id"`
	Name         string   `json:"name"`
	Provider     string   `json:"provider"`
	Capabilities []string `json:"capabilities"`
}

// DefaultModels is the fallback list when ``AVAILABLE_MODELS`` is
// unset. Picked to match the providers the Gateway is already
// configured for in this repo.
var DefaultModels = []ModelEntry{
	{ID: "gpt-4o", Name: "GPT-4o", Provider: "openai", Capabilities: []string{"chat"}},
	{ID: "gpt-4o-mini", Name: "GPT-4o Mini", Provider: "openai", Capabilities: []string{"chat"}},
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

// Models returns the configured model list, falling back to
// ``DefaultModels`` when ``AVAILABLE_MODELS`` is unset or
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

// ModelAllowed returns true iff ``id`` matches an entry in the
// configured model list. Used by handlers to gate
// conversation/message creation.
func (c *Config) ModelAllowed(id string) bool {
	for _, m := range c.Models() {
		if m.ID == id {
			return true
		}
	}
	return false
}
