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

// CORS returns a middleware enforcing the allow-list.
//
// ``allowOrigins`` is a comma-separated env value (e.g.
// ``http://localhost:3002,https://chat.example.com``). An empty
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
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-CSRF-Token")
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
