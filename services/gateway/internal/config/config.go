// Package config loads the gateway's runtime configuration from the
// environment. The rest of the service receives a fully-populated
// Config and never reads os.Getenv directly.
//
// Phase 1 (strangler fig): the gateway is a transparent reverse proxy
// in front of the Python backend. It owns the public :8080 listener,
// does coarse API-key auth + metrics, and forwards every request
// (streaming and non-streaming alike) to Python unchanged. Python
// remains authoritative for routing, DLP, full auth, and logging.
package config

import "github.com/caarlos0/env/v11"

// Config is the full runtime configuration for the gateway.
//
// Fields tagged `env:"NAME,required"` cause Load() to error (and the
// process to refuse to start) when absent.
type Config struct {
	// Deployment environment. "production" (anything outside the dev
	// set) is reserved for future fail-closed checks; Phase 1 only
	// uses it for log formatting.
	AppEnv string `env:"APP_ENV" envDefault:"development"`

	// Public HTTP listener — the gateway takes over the port Python
	// used to own (8080 in compose).
	Port string `env:"PORT" envDefault:"8080"`

	// Upstream Python backend base URL (no trailing slash), e.g.
	// http://backend:8080. Every proxied request is forwarded here.
	PythonServiceURL string `env:"PYTHON_SERVICE_URL,required"`

	// PostgreSQL DSN (pgx-compatible) — used ONLY for the coarse
	// api_keys lookup on /v1/* requests. A small pool is plenty.
	DatabaseURL string `env:"DATABASE_URL,required"`
	PgMaxConns  int32  `env:"GATEWAY_PG_MAX_CONNS" envDefault:"10"`

	// Redis URL — caches the resolved api_keys lookup (mirrors the
	// Python 30s key cache) so repeat requests skip Postgres.
	RedisURL string `env:"REDIS_URL,required"`

	// TTL for a cached *valid* key resolution. Matches the Python
	// provider/key caches (30s). Invalid keys are cached for a much
	// shorter window (see auth.Authenticator) to blunt bad-key floods
	// without pinning a revocation for long.
	AuthCacheTTL string `env:"GATEWAY_AUTH_CACHE_TTL" envDefault:"30s"`

	// Max request body for /v1/* only. Default 25 MiB — chat vision
	// requests carry base64 images inline, so a tight (e.g. 1 MiB)
	// cap would regress multimodal. Python's own BodySizeLimit stays
	// authoritative; this is just an early guard. 0 disables it.
	MaxBodyBytes int64 `env:"GATEWAY_MAX_BODY_BYTES" envDefault:"26214400"`
}

// Load parses the environment into a Config.
func Load() (Config, error) {
	return env.ParseAs[Config]()
}
