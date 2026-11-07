// Per-user rate limit using Redis INCR + EXPIRE.
//
// Bucket key is “ratelimit:{user_id}:{window_bucket}“ where the
// bucket is “now/window“. INCR returns the post-increment counter;
// the first INCR of a bucket also gets a PEXPIRE so the key auto-
// reaps. Over-limit requests return 429 with a “Retry-After“
// header pointing at the next bucket boundary.
//
// CRITICAL: applied ONLY to high-cost endpoints (message send,
// regenerate). Auth + read-only routes are intentionally exempt —
// rate-limiting login would lock real users out during a credential
// stuffing attack against someone else.
package middleware

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// RateLimit returns a middleware that enforces “limit“ requests
// per “window“ per authenticated user. Requests without a resolved
// caller (no RequireAuth above) pass through — the caller is
// expected to chain RateLimit AFTER RequireAuth.
//
// Redis outages fail OPEN (we log + let the request through) rather
// than fail-closed; a transient Redis failure shouldn't break the
// chat surface. The trade-off is that an outage briefly disables
// the limit; in Phase 1 that's acceptable because the upstream
// gateway has its own quota controls.
func RateLimit(rdb *redis.Client, limit int, window time.Duration) func(http.Handler) http.Handler {
	if limit <= 0 || window <= 0 {
		// Disabled — return a pass-through.
		return func(next http.Handler) http.Handler { return next }
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			caller, ok := CallerFrom(r.Context())
			if !ok {
				next.ServeHTTP(w, r)
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), 100*time.Millisecond)
			defer cancel()

			bucket := time.Now().UnixNano() / int64(window)
			key := fmt.Sprintf("ratelimit:%s:%d", caller.UserID.String(), bucket)

			count, err := rdb.Incr(ctx, key).Result()
			if err != nil {
				slog.Warn("ratelimit: redis incr failed; fail-open",
					"err", err, "user_id", caller.UserID.String())
				next.ServeHTTP(w, r)
				return
			}
			if count == 1 {
				// First hit in this bucket — set TTL just over the
				// window length so the key reaps slightly after the
				// window rolls over.
				if err := rdb.PExpire(ctx, key, window+time.Second).Err(); err != nil {
					slog.Warn("ratelimit: redis pexpire failed",
						"err", err, "user_id", caller.UserID.String())
				}
			}

			if count > int64(limit) {
				// Compute seconds remaining in this bucket so the
				// client can wait the right amount before retrying.
				bucketStart := bucket * int64(window)
				bucketEnd := bucketStart + int64(window)
				retryAfter := time.Duration(bucketEnd-time.Now().UnixNano()) / time.Second
				if retryAfter < 1 {
					retryAfter = 1
				}
				w.Header().Set("Retry-After", strconv.Itoa(int(retryAfter)))
				w.Header().Set("X-RateLimit-Limit", strconv.Itoa(limit))
				w.Header().Set("X-RateLimit-Remaining", "0")
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusTooManyRequests)
				_, _ = w.Write([]byte(`{"error":{"code":"RATE_LIMIT_EXCEEDED","message":"too many requests"}}`))
				return
			}
			w.Header().Set("X-RateLimit-Limit", strconv.Itoa(limit))
			w.Header().Set("X-RateLimit-Remaining",
				strconv.Itoa(limit-int(count)))
			next.ServeHTTP(w, r)
		})
	}
}
