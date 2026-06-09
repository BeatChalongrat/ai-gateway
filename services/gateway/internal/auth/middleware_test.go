package auth

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func discardLog() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

// okNext is a downstream handler that 200s — i.e. "the request was let through".
func okNext() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
}

// mw builds the auth middleware with throwaway deps. The Authenticator is
// never consulted on the paths these tests exercise (public bypass, or
// missing-Bearer which 401s before Resolve), so nil pool/redis is safe.
func mw() func(http.Handler) http.Handler {
	return Middleware(New(nil, nil, time.Second), "/v1/", discardLog())
}

// GET /v1/models is public in the Python backend (no auth dependency). The
// gateway must let it through WITHOUT an Authorization header, otherwise it
// regresses a previously-public endpoint to 401.
func TestPublicModelsBypassesAuth(t *testing.T) {
	h := mw()(okNext())
	req := httptest.NewRequest(http.MethodGet, "/v1/models", nil) // no Authorization
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET /v1/models without auth = %d, want 200 (must stay public)", rec.Code)
	}
}

// Only the allowlisted method counts: POST /v1/models is not public.
func TestPublicAllowlistIsMethodScoped(t *testing.T) {
	h := mw()(okNext())
	req := httptest.NewRequest(http.MethodPost, "/v1/models", nil) // no Authorization
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("POST /v1/models without auth = %d, want 401 (only GET is public)", rec.Code)
	}
}

// A normal guarded /v1 path with no key still 401s — auth isn't disabled.
func TestGuardedPathStillRequiresAuth(t *testing.T) {
	h := mw()(okNext())
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil) // no Authorization
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("POST /v1/chat/completions without auth = %d, want 401", rec.Code)
	}
}

// Non-/v1 traffic is never gated (cookies/JWT owned by Python).
func TestNonV1PassesThrough(t *testing.T) {
	h := mw()(okNext())
	req := httptest.NewRequest(http.MethodGet, "/portal/me", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET /portal/me = %d, want 200 (not gated)", rec.Code)
	}
}
