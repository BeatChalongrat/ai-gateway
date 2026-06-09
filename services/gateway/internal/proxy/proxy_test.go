package proxy

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// The proxy must forward request body + headers and return the upstream
// status + body byte-for-byte, with no modification.
func TestTransparentPassthrough(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("X-Echo-Auth", r.Header.Get("Authorization"))
		w.Header().Set("X-Echo-Path", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write(body)
	}))
	defer backend.Close()

	rp, err := New(backend.URL, discardLogger())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	const payload = `{"model":"auto","messages":[{"role":"user","content":"hi"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(payload))
	req.Header.Set("Authorization", "Bearer gw_test")
	rec := httptest.NewRecorder()

	rp.ServeHTTP(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201", rec.Code)
	}
	if got := rec.Body.String(); got != payload {
		t.Fatalf("body = %q, want %q (must be unmodified)", got, payload)
	}
	if got := rec.Header().Get("X-Echo-Auth"); got != "Bearer gw_test" {
		t.Fatalf("upstream saw Authorization = %q, want Bearer gw_test", got)
	}
	if got := rec.Header().Get("X-Echo-Path"); got != "/v1/chat/completions" {
		t.Fatalf("upstream saw path = %q, want /v1/chat/completions", got)
	}
}

// SSE bodies must pass through untouched (Go does not parse events).
func TestSSEPassthrough(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		for _, chunk := range []string{"data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"} {
			_, _ = io.WriteString(w, chunk)
			if fl != nil {
				fl.Flush()
			}
		}
	}))
	defer backend.Close()

	rp, err := New(backend.URL, discardLogger())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"stream":true}`))
	rec := httptest.NewRecorder()
	rp.ServeHTTP(rec, req)

	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content-type = %q, want text/event-stream", ct)
	}
	want := "data: a\n\ndata: b\n\ndata: [DONE]\n\n"
	if got := rec.Body.String(); got != want {
		t.Fatalf("stream body = %q, want %q", got, want)
	}
}

// A dead upstream becomes a clean 502 JSON envelope, not a panic.
func TestUpstreamDown(t *testing.T) {
	// Reserve a port by opening then closing a server.
	dead := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := dead.URL
	dead.Close()

	rp, err := New(url, discardLogger())
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{}`))
	rec := httptest.NewRecorder()
	rp.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "upstream_unavailable") {
		t.Fatalf("body = %q, want upstream_unavailable code", rec.Body.String())
	}
}
