package auth

// Revocations — FIX129 B2, the store that lets sign-out end a session here.
//
// A Portal access token is good for 60 minutes and chat-edge used to
// honour it for all of them, whatever the Portal had done since. Two
// marks make a token dead before its exp:
//
//	auth:revoked:jti:<jti>   value "1", TTL = until that token's exp
//	auth:revoked:user:<sub>  value = unix seconds; a token whose iat is
//	                         at or before that instant is revoked
//
// The Portal writes them through POST /internal/auth/revoke on logout;
// middleware.RequireAuth reads them on every protected request.
//
// A Redis error on the READ side is reported to the middleware, which
// serves the request and logs auth_revocation_check_failed: availability
// first, the same choice the Portal makes for its own refresh blacklist.
// The window is bounded by the token's own expiry.

import (
	"context"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// Revocations answers whether a parsed token is still allowed in.
type Revocations interface {
	// IsRevoked reports whether the token (jti, issued to sub at
	// issuedAt) has been revoked, alone or by a RevokeUserBefore cut.
	IsRevoked(ctx context.Context, jti, sub string, issuedAt time.Time) (bool, error)
	// RevokeJTI marks one token dead for ttl (its remaining lifetime).
	RevokeJTI(ctx context.Context, jti string, ttl time.Duration) error
	// RevokeUserBefore marks every token of sub issued at or before at
	// as dead.
	RevokeUserBefore(ctx context.Context, sub string, at time.Time) error
}

const (
	revokedJTIPrefix  = "auth:revoked:jti:"
	revokedUserPrefix = "auth:revoked:user:"

	// MinRevocationTTL is the floor for a jti mark: a token the caller
	// says has already expired is still marked for a minute, so clock
	// skew between the Portal and this service cannot leave it alive.
	MinRevocationTTL = time.Minute

	// UserRevocationTTL is how long an all-tokens-before mark is kept. It
	// only has to outlive the tokens it covers, which live 60 minutes;
	// a day is a comfortable multiple. A stale mark is harmless: a token
	// issued after the cut has iat > mark.
	UserRevocationTTL = 24 * time.Hour
)

// revokedByCut is the shared rule: a token is dead when the user's cut
// exists and the token is not provably newer than it. A token with no
// iat cannot be dated, so it is treated as older than any cut.
func revokedByCut(cutUnix int64, issuedAt time.Time) bool {
	if issuedAt.IsZero() {
		return true
	}
	return issuedAt.Unix() <= cutUnix
}

// ---------------------------------------------------------------------------
// Redis
// ---------------------------------------------------------------------------

// RedisRevocations keeps the marks in Redis (go-redis v9), the same
// instance the run ledger and the rate limiter use.
type RedisRevocations struct {
	rdb     *redis.Client
	userTTL time.Duration
}

// NewRedisRevocations builds the store over rdb.
func NewRedisRevocations(rdb *redis.Client) *RedisRevocations {
	return &RedisRevocations{rdb: rdb, userTTL: UserRevocationTTL}
}

func (r *RedisRevocations) IsRevoked(ctx context.Context, jti, sub string, issuedAt time.Time) (bool, error) {
	keys := []string{revokedUserPrefix + sub}
	if jti != "" {
		keys = append(keys, revokedJTIPrefix+jti)
	}
	vals, err := r.rdb.MGet(ctx, keys...).Result()
	if err != nil {
		return false, err
	}
	if len(vals) > 1 && vals[1] != nil {
		return true, nil
	}
	if s, ok := vals[0].(string); ok {
		if cut, perr := strconv.ParseInt(strings.TrimSpace(s), 10, 64); perr == nil && revokedByCut(cut, issuedAt) {
			return true, nil
		}
	}
	return false, nil
}

func (r *RedisRevocations) RevokeJTI(ctx context.Context, jti string, ttl time.Duration) error {
	if jti == "" {
		return nil
	}
	if ttl < MinRevocationTTL {
		ttl = MinRevocationTTL
	}
	return r.rdb.Set(ctx, revokedJTIPrefix+jti, "1", ttl).Err()
}

func (r *RedisRevocations) RevokeUserBefore(ctx context.Context, sub string, at time.Time) error {
	if sub == "" {
		return nil
	}
	key := revokedUserPrefix + sub
	cut := at.Unix()
	// Never move a cut backwards: a later logout must not resurrect
	// tokens an earlier one killed.
	if existing, err := r.rdb.Get(ctx, key).Result(); err == nil {
		if prev, perr := strconv.ParseInt(strings.TrimSpace(existing), 10, 64); perr == nil && prev > cut {
			cut = prev
		}
	} else if err != redis.Nil {
		return err
	}
	return r.rdb.Set(ctx, key, strconv.FormatInt(cut, 10), r.userTTL).Err()
}

// ---------------------------------------------------------------------------
// In-process (tests, single-process dev without Redis)
// ---------------------------------------------------------------------------

// MemoryRevocations is the same contract held in a map. It does not
// survive a restart and is not shared between replicas, so production
// wires RedisRevocations; this one is for tests and a one-process dev
// stack.
type MemoryRevocations struct {
	mu   sync.Mutex
	jtis map[string]time.Time // jti -> expiry of the mark
	cuts map[string]int64     // sub -> cut (unix seconds)
	now  func() time.Time
}

// NewMemoryRevocations builds an empty in-process store.
func NewMemoryRevocations() *MemoryRevocations {
	return &MemoryRevocations{jtis: map[string]time.Time{}, cuts: map[string]int64{}, now: time.Now}
}

func (m *MemoryRevocations) IsRevoked(_ context.Context, jti, sub string, issuedAt time.Time) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if jti != "" {
		if until, ok := m.jtis[jti]; ok {
			if m.now().Before(until) {
				return true, nil
			}
			delete(m.jtis, jti)
		}
	}
	if cut, ok := m.cuts[sub]; ok && revokedByCut(cut, issuedAt) {
		return true, nil
	}
	return false, nil
}

func (m *MemoryRevocations) RevokeJTI(_ context.Context, jti string, ttl time.Duration) error {
	if jti == "" {
		return nil
	}
	if ttl < MinRevocationTTL {
		ttl = MinRevocationTTL
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.jtis[jti] = m.now().Add(ttl)
	return nil
}

func (m *MemoryRevocations) RevokeUserBefore(_ context.Context, sub string, at time.Time) error {
	if sub == "" {
		return nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if prev, ok := m.cuts[sub]; !ok || at.Unix() > prev {
		m.cuts[sub] = at.Unix()
	}
	return nil
}
