package service

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// The proxy must forward to the gateway's PUBLIC media path carrying the
// internal-trust header set (X-Internal-Service-Secret + the caller's
// X-User-ID / X-Tenant-ID), and relay the upstream status + body verbatim.
func TestProxyPublicMedia_ForwardsInternalTrust(t *testing.T) {
	var gotSecret, gotUser, gotTenant, gotPath, gotMethod string
	var gotBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotSecret = r.Header.Get("X-Internal-Service-Secret")
		gotUser = r.Header.Get("X-User-ID")
		gotTenant = r.Header.Get("X-Tenant-ID")
		gotPath = r.URL.Path
		gotMethod = r.Method
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"job_id":"j1","status":"queued"}`))
	}))
	defer srv.Close()

	// APIKey set → bearer() returns it; no JWT signing needed.
	gw := NewGatewayClient(srv.URL+"/v1", "test-api-key", "unused-jwt-secret", time.Minute, "the-internal-secret")
	uid, tid := uuid.New(), uuid.New()

	status, body, err := gw.ProxyPublicMedia(
		context.Background(),
		GatewayCallContext{TenantID: tid, UserID: uid},
		http.MethodPost, "/videos/generations",
		[]byte(`{"prompt":"a cat","model":"mock-video-1","duration_sec":5}`),
	)
	if err != nil {
		t.Fatalf("ProxyPublicMedia err: %v", err)
	}
	if status != http.StatusCreated {
		t.Fatalf("status=%d want 201", status)
	}
	if string(body) != `{"job_id":"j1","status":"queued"}` {
		t.Fatalf("relayed body=%s", body)
	}
	if gotSecret != "the-internal-secret" {
		t.Fatalf("X-Internal-Service-Secret=%q", gotSecret)
	}
	if gotUser != uid.String() {
		t.Fatalf("X-User-ID=%q want %q", gotUser, uid.String())
	}
	if gotTenant != tid.String() {
		t.Fatalf("X-Tenant-ID=%q want %q", gotTenant, tid.String())
	}
	if gotMethod != http.MethodPost {
		t.Fatalf("forwarded method=%q", gotMethod)
	}
	if gotPath != "/v1/videos/generations" {
		t.Fatalf("forwarded path=%q want /v1/videos/generations", gotPath)
	}
	if !strings.Contains(string(gotBody), "mock-video-1") {
		t.Fatalf("forwarded body did not include the request payload: %s", gotBody)
	}
}

// The gateway's error envelope (e.g. 402 insufficient credits / 403 DLP) must
// be relayed to the client unchanged, not swallowed.
func TestProxyPublicMedia_RelaysGatewayError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusPaymentRequired)
		_, _ = w.Write([]byte(`{"detail":"insufficient credits"}`))
	}))
	defer srv.Close()

	gw := NewGatewayClient(srv.URL+"/v1", "k", "j", time.Minute, "s")
	status, body, err := gw.ProxyPublicMedia(
		context.Background(),
		GatewayCallContext{TenantID: uuid.New(), UserID: uuid.New()},
		http.MethodPost, "/videos/generations", []byte(`{}`),
	)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if status != http.StatusPaymentRequired {
		t.Fatalf("status=%d want 402", status)
	}
	if !strings.Contains(string(body), "insufficient credits") {
		t.Fatalf("relayed body=%s", body)
	}
}
