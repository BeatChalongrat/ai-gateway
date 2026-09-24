package middleware

// FIX131 E2 — a restarting chat-edge answers a new turn 503 SERVER_DRAINING
// with Retry-After. Retry-After is not a CORS-safelisted response header,
// so the cross-origin page can only read it when it is exposed.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCORS_ExposesRetryAfterToThePage(t *testing.T) {
	h := CORS("http://localhost:3002")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "5")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/v1/conversations/x/messages", nil)
	req.Header.Set("Origin", "http://localhost:3002")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	expose := strings.ToLower(rec.Header().Get("Access-Control-Expose-Headers"))
	if !strings.Contains(expose, "retry-after") {
		t.Fatalf("Access-Control-Expose-Headers = %q, want Retry-After readable by the page", expose)
	}
}
