package middleware

// FIX121 — the page sends X-Request-Id on a cross-origin send. A request
// header the preflight does not allow is not a degraded send, it is NO send:
// the browser refuses to issue the POST at all.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCORS_PreflightAllowsTheRequestIDHeaderAndExposesTheReplayMarker(t *testing.T) {
	h := CORS("http://localhost:3002")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("a preflight must not reach the handler")
	}))
	req := httptest.NewRequest(http.MethodOptions, "/api/v1/conversations/x/messages", nil)
	req.Header.Set("Origin", "http://localhost:3002")
	req.Header.Set("Access-Control-Request-Method", "POST")
	req.Header.Set("Access-Control-Request-Headers", "content-type, x-request-id")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent {
		t.Fatalf("preflight status = %d", rec.Code)
	}
	allow := strings.ToLower(rec.Header().Get("Access-Control-Allow-Headers"))
	for _, want := range []string{"authorization", "content-type", "x-csrf-token", "x-request-id"} {
		if !strings.Contains(allow, want) {
			t.Fatalf("Access-Control-Allow-Headers = %q, missing %q", allow, want)
		}
	}
	expose := strings.ToLower(rec.Header().Get("Access-Control-Expose-Headers"))
	if !strings.Contains(expose, "idempotent-replayed") || !strings.Contains(expose, "x-request-id") {
		t.Fatalf("Access-Control-Expose-Headers = %q", expose)
	}
}

func TestCORS_AnOriginOutsideTheListGetsNothing(t *testing.T) {
	h := CORS("http://localhost:3002")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	req := httptest.NewRequest(http.MethodOptions, "/", nil)
	req.Header.Set("Origin", "http://evil.example")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Header().Get("Access-Control-Allow-Origin") != "" || rec.Header().Get("Access-Control-Allow-Headers") != "" {
		t.Fatalf("an unlisted origin must get no CORS headers: %v", rec.Header())
	}
}
