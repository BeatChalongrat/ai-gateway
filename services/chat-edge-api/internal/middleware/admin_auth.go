// Admin-only authentication middleware.
//
// The chat product (apps/web-chat) authenticates via Portal JWT —
// per-user identity flows from the Portal into the request context
// via “RequireAuth“ in auth.go. The admin observability surface
// (apps/admin-ui) is a different audience: a small number of
// operators reading aggregate / cross-tenant data.
//
// For Phase 2 (internal-tool launch mode) we gate admin endpoints
// with a SHARED BEARER SECRET configured via
// “CHAT_EDGE_ADMIN_API_KEY“. The admin UI never sees the secret
// directly — it goes through a Next.js server-side proxy that
// injects the header from its own server env. The cost of that
// indirection is one extra network hop per admin call; the benefit
// is no JWT key-management churn for an audience of <10 operators.
//
// Empty CHAT_EDGE_ADMIN_API_KEY disables the routes entirely: the
// middleware always returns 503 so a misconfigured deploy fails
// closed.
package middleware

import (
	"crypto/subtle"
	"net/http"
	"strings"
)

// RequireAdmin returns middleware that gates the wrapped handler on
// an exact-match “Authorization: Bearer <ADMIN_API_KEY>“ header.
// The constant-time compare avoids leaking the key via response-
// time side channels (paranoid but cheap).
func RequireAdmin(adminKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if adminKey == "" {
				http.Error(w, "admin endpoints disabled", http.StatusServiceUnavailable)
				return
			}
			raw := r.Header.Get("Authorization")
			if !strings.HasPrefix(raw, "Bearer ") {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			supplied := strings.TrimPrefix(raw, "Bearer ")
			if subtle.ConstantTimeCompare([]byte(supplied), []byte(adminKey)) != 1 {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// AdminRoleScope (AM7) narrows a bearer call that names a role below admin.
// admin-ui's proxy puts the signed-in admin's role in X-Admin-Role; a role
// other than admin / super_admin (agent_publisher) may reach only the paths
// under allowPrefix — Agent Market's admin door, whose handlers decide the
// rest — and every other endpoint behind the bearer answers 403 before it
// runs. A call without the header (a script holding the bearer) is not
// narrowed: the bearer alone was always the whole admin surface.
func AdminRoleScope(allowPrefix string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch role := strings.TrimSpace(r.Header.Get("X-Admin-Role")); role {
			case "", "admin", "super_admin":
			default:
				if !strings.HasPrefix(r.URL.Path, allowPrefix) {
					http.Error(w, "forbidden for this admin role", http.StatusForbidden)
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}
