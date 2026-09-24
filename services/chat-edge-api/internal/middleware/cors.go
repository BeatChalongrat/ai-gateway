// CORS middleware for the chat-edge-api.
//
// The web-chat Next.js app (Session 6, port 3002) is the only
// browser client; we tighten the allow-list to known origins so
// random third-party pages can't drive the API from a victim's
// cookies. Production deployments must override the origin via
// the CORS_ALLOW_ORIGINS env var.
package middleware

import (
	"net/http"
	"strings"
)

// CORSAllowHeaders is the request-header allow-list of a cross-origin call.
const CORSAllowHeaders = "Authorization, Content-Type, X-CSRF-Token, X-Request-Id"

// CORS returns a middleware enforcing the allow-list.
//
// “allowOrigins“ is a comma-separated env value (e.g.
// “http://localhost:3002,https://chat.example.com“). An empty
// list yields a "deny all cross-origin" policy that still permits
// same-origin requests.
func CORS(allowOrigins string) func(http.Handler) http.Handler {
	allowed := map[string]struct{}{}
	for _, o := range strings.Split(allowOrigins, ",") {
		o = strings.TrimSpace(o)
		if o != "" {
			allowed[o] = struct{}{}
		}
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if _, ok := allowed[origin]; ok {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Credentials", "true")
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
				// FIX121 — X-Request-Id: the page now names its own send (the
				// idempotency key, STREAM_CONTRACT §12.1). A header outside this
				// list fails the browser's preflight, i.e. EVERY cross-origin send.
				w.Header().Set("Access-Control-Allow-Headers", CORSAllowHeaders)
				// Readable by the page: the run id the server settled on, and
				// whether this response is the replay of an earlier request.
				// FIX131 E2 — and Retry-After, which is not CORS-safelisted: the
				// 503 SERVER_DRAINING of a restart says when to send again.
				w.Header().Set("Access-Control-Expose-Headers", "X-Request-Id, Idempotent-Replayed, Retry-After")
				w.Header().Set("Access-Control-Max-Age", "600")
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
