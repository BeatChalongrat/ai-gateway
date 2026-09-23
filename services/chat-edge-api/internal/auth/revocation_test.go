package auth

// FIX129 B2 — the revocation store, both backends, one contract.
//
// The Redis half runs against the same Redis the run-ledger tests use
// (FIX41_BENCH_REDIS_URL) and skips (fails on CI) when it is not there;
// the in-memory half always runs.

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func testRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("FIX41_BENCH_REDIS_URL")
	if url == "" {
		url = "redis://localhost:6380/1"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		testdeps.Missing(t, "bad redis url: %v", err)
	}
	c := redis.NewClient(opts)
	if err := c.Ping(context.Background()).Err(); err != nil {
		_ = c.Close()
		testdeps.Missing(t, "redis unreachable: %v", err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

// runRevocationContract exercises one store: unknown tokens pass, a
// revoked jti is dead, a user cut kills tokens issued at or before it
// and spares later ones, and a cut never moves backwards. Returns the
// sub and jti it marked so a shared backend can be cleaned.
func runRevocationContract(t *testing.T, store Revocations) (sub, jti string) {
	t.Helper()
	ctx := context.Background()
	sub = uuid.NewString()
	jti = uuid.NewString()
	issued := time.Now().Add(-10 * time.Minute)

	// Nothing revoked yet.
	if revoked, err := store.IsRevoked(ctx, jti, sub, issued); err != nil || revoked {
		t.Fatalf("fresh token: revoked=%v err=%v, want false/nil", revoked, err)
	}

	// One token.
	if err := store.RevokeJTI(ctx, jti, 50*time.Minute); err != nil {
		t.Fatalf("RevokeJTI: %v", err)
	}
	if revoked, err := store.IsRevoked(ctx, jti, sub, issued); err != nil || !revoked {
		t.Fatalf("after RevokeJTI: revoked=%v err=%v, want true/nil", revoked, err)
	}
	// A sibling token of the same user is untouched.
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, issued); err != nil || revoked {
		t.Fatalf("sibling token after RevokeJTI: revoked=%v err=%v, want false/nil", revoked, err)
	}

	// Everything issued up to now.
	cut := time.Now()
	if err := store.RevokeUserBefore(ctx, sub, cut); err != nil {
		t.Fatalf("RevokeUserBefore: %v", err)
	}
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, issued); err != nil || !revoked {
		t.Fatalf("older token after cut: revoked=%v err=%v, want true/nil", revoked, err)
	}
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, cut); err != nil || !revoked {
		t.Fatalf("token issued AT the cut: revoked=%v err=%v, want true/nil (iat <= cut)", revoked, err)
	}
	newer := cut.Add(2 * time.Second)
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, newer); err != nil || revoked {
		t.Fatalf("token issued after cut: revoked=%v err=%v, want false/nil", revoked, err)
	}
	// A token without iat cannot be dated: not trusted past a cut.
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, time.Time{}); err != nil || !revoked {
		t.Fatalf("undated token after cut: revoked=%v err=%v, want true/nil", revoked, err)
	}
	// Another user is untouched.
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), uuid.NewString(), issued); err != nil || revoked {
		t.Fatalf("other user: revoked=%v err=%v, want false/nil", revoked, err)
	}

	// An earlier cut must not resurrect what the later one killed.
	if err := store.RevokeUserBefore(ctx, sub, cut.Add(-time.Hour)); err != nil {
		t.Fatalf("RevokeUserBefore (earlier): %v", err)
	}
	if revoked, err := store.IsRevoked(ctx, uuid.NewString(), sub, issued); err != nil || !revoked {
		t.Fatalf("older token after an earlier second cut: revoked=%v err=%v, want true/nil", revoked, err)
	}
	return sub, jti
}

func TestMemoryRevocations_Contract(t *testing.T) {
	runRevocationContract(t, NewMemoryRevocations())
}

func TestRedisRevocations_Contract(t *testing.T) {
	rdb := testRedis(t)
	sub, jti := runRevocationContract(t, NewRedisRevocations(rdb))
	_ = rdb.Del(context.Background(), revokedUserPrefix+sub, revokedJTIPrefix+jti)
}

// The jti mark carries a TTL bounded by the token's own life, floored
// at MinRevocationTTL, so Redis does not accumulate dead marks.
func TestRedisRevocations_JTIMarkExpiresWithTheToken(t *testing.T) {
	rdb := testRedis(t)
	store := NewRedisRevocations(rdb)
	ctx := context.Background()

	jti := uuid.NewString()
	if err := store.RevokeJTI(ctx, jti, 5*time.Minute); err != nil {
		t.Fatalf("RevokeJTI: %v", err)
	}
	ttl, err := rdb.TTL(ctx, revokedJTIPrefix+jti).Result()
	if err != nil {
		t.Fatalf("TTL: %v", err)
	}
	if ttl <= 0 || ttl > 5*time.Minute {
		t.Fatalf("jti mark TTL = %v, want (0, 5m]", ttl)
	}

	expired := uuid.NewString()
	if err := store.RevokeJTI(ctx, expired, -time.Minute); err != nil {
		t.Fatalf("RevokeJTI (expired): %v", err)
	}
	ttl, err = rdb.TTL(ctx, revokedJTIPrefix+expired).Result()
	if err != nil {
		t.Fatalf("TTL: %v", err)
	}
	if ttl <= 0 || ttl > MinRevocationTTL {
		t.Fatalf("expired-token mark TTL = %v, want (0, %v] (floored, never dropped)", ttl, MinRevocationTTL)
	}
	_ = rdb.Del(ctx, revokedJTIPrefix+jti, revokedJTIPrefix+expired)
}
