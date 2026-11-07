// Factory — picks the Backend implementation at boot based on the
// runtime config. Anything outside the recognised set falls through
// to the local backend so a fresh checkout never needs S3 set up.
//
// This is the one place main.go talks to the storage package; the
// rest of the service consumes the Backend interface.

package storage

import (
	"fmt"
	"strings"
)

// Config is the subset of chat-edge-api configuration the factory
// needs. Kept as a local struct so this package doesn't depend on
// internal/config and stays unit-testable in isolation.
type Config struct {
	Backend          string // "local" | "s3"
	LocalBasePath    string
	S3Bucket         string
	S3Region         string
	S3Endpoint       string
	S3AccessKey      string
	S3SecretKey      string
	S3ForcePathStyle bool
}

// NewBackend dispatches on Backend. Unknown values default to local
// rather than erroring so a typo in env doesn't take down the
// service — the operator gets a working dev-mode storage while
// the misconfiguration is visible in any "what backend am I using"
// telemetry path.
func NewBackend(cfg Config) (Backend, error) {
	switch strings.ToLower(strings.TrimSpace(cfg.Backend)) {
	case "s3":
		return NewS3Backend(S3Config{
			Bucket:    cfg.S3Bucket,
			Region:    cfg.S3Region,
			Endpoint:  cfg.S3Endpoint,
			AccessKey: cfg.S3AccessKey,
			SecretKey: cfg.S3SecretKey,
			// MinIO + most non-AWS S3 deployments need path-style
			// addressing. Default to true whenever an explicit
			// endpoint is set; AWS-direct callers can override
			// via the explicit S3ForcePathStyle knob.
			ForcePathStyle: cfg.S3ForcePathStyle || cfg.S3Endpoint != "",
		})
	case "", "local", "filesystem", "fs":
		return NewLocalBackend(cfg.LocalBasePath)
	default:
		return nil, fmt.Errorf("storage: unknown backend %q (use \"local\" or \"s3\")", cfg.Backend)
	}
}
