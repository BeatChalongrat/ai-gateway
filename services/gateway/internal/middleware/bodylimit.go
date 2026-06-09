// Package middleware holds the gateway's own HTTP middleware (distinct
// from go-chi's middleware package, which is imported as chimw).
package middleware

import (
	"net/http"
	"strings"
)

// BodyLimit caps the request body to max bytes for requests under
// prefix. It is intentionally scoped (not global): file/KB uploads on
// other paths can be large, and even /v1 vision requests carry base64
// images, so max defaults high. Python's BodySizeLimit remains the
// authoritative cap. max <= 0 disables the guard.
func BodyLimit(max int64, prefix string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if max > 0 && r.Body != nil && strings.HasPrefix(r.URL.Path, prefix) {
				r.Body = http.MaxBytesReader(w, r.Body, max)
			}
			next.ServeHTTP(w, r)
		})
	}
}
