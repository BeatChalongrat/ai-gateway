package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"

	"github.com/BeatChalongrat/ai-gateway/gateway/internal/httpx"
)

type ctxKey int

const resolvedKey ctxKey = iota

// FromContext returns the resolved identity attached by Middleware, if
// the request carried a valid API key. Absent for JWT / fail-open paths.
func FromContext(ctx context.Context) (*Resolved, bool) {
	r, ok := ctx.Value(resolvedKey).(*Resolved)
	return r, ok
}

// publicPaths are endpoints the Python backend serves WITHOUT auth, keyed by
// "METHOD PATH". The gateway must NOT gate these or it would 401 a route that
// is public upstream — e.g. GET /v1/models (the public model catalog in
// app/api/routes/public_models.py, mounted at /v1 with no auth dependency).
// Add future public /v1/* endpoints here.
var publicPaths = map[string]bool{
	"GET /v1/models": true,
}

// isPublic reports whether (method, path) is an unauthenticated upstream route.
func isPublic(method, path string) bool {
	return publicPaths[method+" "+path]
}

// Middleware performs coarse API-key auth, but ONLY for requests whose
// path starts with guardPrefix (e.g. "/v1/"). All other traffic
// (/portal, /admin, /auth, health) passes straight through — those use
// cookies/JWT that Python owns.
//
// Behaviour on /v1/*:
//   - public upstream paths (publicPaths, e.g. GET /v1/models) → pass through
//   - no/blank Bearer            → 401 (unambiguous; Python would 401 too)
//   - JWT (eyJ...)               → pass through, Python validates
//   - provably-invalid API key   → 401 early reject
//   - infra error (DB/Redis)     → log + pass through (fail open)
//   - valid API key              → attach identity, pass through
func Middleware(a *Authenticator, guardPrefix string, log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !strings.HasPrefix(r.URL.Path, guardPrefix) {
				next.ServeHTTP(w, r)
				return
			}

			// Public upstream endpoints (e.g. GET /v1/models) must stay
			// reachable without a key — Python serves them unauthenticated,
			// so gating them here would be a regression.
			if isPublic(r.Method, r.URL.Path) {
				next.ServeHTTP(w, r)
				return
			}

			authz := r.Header.Get("Authorization")
			if !strings.HasPrefix(authz, "Bearer ") {
				httpx.WriteError(w, http.StatusUnauthorized,
					"missing_authorization", "Missing or invalid Authorization header.")
				return
			}
			raw := strings.TrimSpace(strings.TrimPrefix(authz, "Bearer "))
			if raw == "" {
				httpx.WriteError(w, http.StatusUnauthorized,
					"missing_authorization", "Missing or invalid Authorization header.")
				return
			}

			// JWTs (admin UI / portal) are base64url JSON starting "eyJ".
			// The gateway doesn't hold the JWT secret in Phase 1 — let
			// Python verify them.
			if strings.HasPrefix(raw, "eyJ") {
				next.ServeHTTP(w, r)
				return
			}

			resolved, err := a.Resolve(r.Context(), raw)
			switch {
			case errors.Is(err, ErrInvalidKey):
				httpx.WriteError(w, http.StatusUnauthorized,
					"invalid_api_key", "Invalid, expired, or revoked API key.")
				return
			case err != nil:
				// Infra failure — never reject on our own outage. Python
				// re-auths downstream, so admitting here is safe.
				log.Warn("auth fail-open", "err", err, "path", r.URL.Path)
				next.ServeHTTP(w, r)
				return
			}

			ctx := context.WithValue(r.Context(), resolvedKey, resolved)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
