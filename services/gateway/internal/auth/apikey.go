// Package auth implements the gateway's COARSE API-key authentication.
//
// Phase 1 contract: this is a strict subset of the Python backend's
// app/services/api_key_service.py:verify_key. Go positively rejects a
// key only when it is provably invalid (no row, status != active, or
// expired) — the exact same three rules Python applies. Anything Go is
// unsure about (DB error, Redis error, JWT, an unusual token) is
// proxied through and left for Python to decide. Python re-authenticates
// every request regardless, so Go can never *admit* something Python
// would reject; it can only reject early what Python would also reject.
package auth

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// ErrInvalidKey means the key was provably invalid (matches Python's
// "return None" outcomes). The middleware turns this into an early 401.
var ErrInvalidKey = errors.New("invalid api key")

// Resolved mirrors the fields the Python ResolvedApiKey carries.
type Resolved struct {
	KeyID     string `json:"key_id"`
	TenantID  string `json:"tenant_id"`
	ProjectID string `json:"project_id"`
	KeyPrefix string `json:"key_prefix"`
	Role      string `json:"role"`
}

const (
	cachePrefix = "gwauth:"
	// Sentinel cached for provably-invalid keys. Short TTL so a key
	// revoked-then-reissued isn't pinned invalid for long.
	negSentinel = "\x00invalid"
	negTTL      = 5 * time.Second
)

// HashKey reproduces Python's app.core.security.hash_api_key:
// lowercase hex of SHA-256 over the raw key's UTF-8 bytes.
func HashKey(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}

// Authenticator resolves raw API keys against Postgres, fronted by a
// short-lived Redis cache.
type Authenticator struct {
	pool   *pgxpool.Pool
	rdb    *redis.Client
	posTTL time.Duration
}

// New builds an Authenticator. posTTL is the cache window for a valid
// resolution (mirror Python's 30s key cache).
func New(pool *pgxpool.Pool, rdb *redis.Client, posTTL time.Duration) *Authenticator {
	return &Authenticator{pool: pool, rdb: rdb, posTTL: posTTL}
}

// Resolve returns the identity for a raw key, ErrInvalidKey if it is
// provably invalid, or a non-nil non-ErrInvalidKey error on an
// infrastructure failure (which the caller MUST treat as fail-open).
func (a *Authenticator) Resolve(ctx context.Context, raw string) (*Resolved, error) {
	h := HashKey(raw)

	if a.rdb != nil {
		if v, err := a.rdb.Get(ctx, cachePrefix+h).Result(); err == nil {
			if v == negSentinel {
				return nil, ErrInvalidKey
			}
			var r Resolved
			if json.Unmarshal([]byte(v), &r) == nil {
				return &r, nil
			}
		}
		// redis.Nil (miss) or any Redis error → fall through to DB.
	}

	r, err := a.lookup(ctx, h)
	if err != nil {
		if errors.Is(err, ErrInvalidKey) {
			a.cache(ctx, h, negSentinel, negTTL)
			return nil, ErrInvalidKey
		}
		return nil, err // infra error → caller fails open
	}

	if blob, mErr := json.Marshal(r); mErr == nil {
		a.cache(ctx, h, string(blob), a.posTTL)
	}
	return r, nil
}

func (a *Authenticator) lookup(ctx context.Context, keyHash string) (*Resolved, error) {
	const q = `SELECT key_id, key_prefix, tenant_id, project_id, role, status, expires_at
	             FROM api_keys WHERE key_hash = $1`

	var (
		r       Resolved
		status  string
		expires *time.Time
	)
	err := a.pool.QueryRow(ctx, q, keyHash).Scan(
		&r.KeyID, &r.KeyPrefix, &r.TenantID, &r.ProjectID, &r.Role, &status, &expires,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrInvalidKey
	}
	if err != nil {
		return nil, err
	}
	if status != "active" {
		return nil, ErrInvalidKey
	}
	// Python: invalid when now >= expires_at.
	if expires != nil && !expires.After(time.Now()) {
		return nil, ErrInvalidKey
	}
	return &r, nil
}

func (a *Authenticator) cache(ctx context.Context, keyHash, val string, ttl time.Duration) {
	if a.rdb == nil {
		return
	}
	// Best-effort; a cache write failure is non-fatal.
	_ = a.rdb.Set(ctx, cachePrefix+keyHash, val, ttl).Err()
}
