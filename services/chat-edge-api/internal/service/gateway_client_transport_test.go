package service

// Batch A — the gateway client must (1) own a pooled transport instead
// of http.DefaultTransport (whose MaxIdleConnsPerHost=2 caused a fresh
// TCP+TLS handshake for every concurrent request past the 2nd), and
// (2) default to NO whole-request deadline so long SSE generations are
// never severed mid-stream (the old hardcoded 5m did exactly that).

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"testing"
	"time"
)

func TestGatewayClient_PooledTransportNotDefault(t *testing.T) {
	g := NewGatewayClient("http://gw", "k", "", time.Minute, "")

	tr, ok := g.http.Transport.(*http.Transport)
	if !ok {
		t.Fatalf("client transport is %T, want *http.Transport (not nil/default)", g.http.Transport)
	}
	if tr == http.DefaultTransport {
		t.Fatal("client must not share http.DefaultTransport")
	}
	if tr.MaxIdleConnsPerHost != 100 {
		t.Fatalf("MaxIdleConnsPerHost = %d, want 100", tr.MaxIdleConnsPerHost)
	}
	if tr.IdleConnTimeout != 90*time.Second {
		t.Fatalf("IdleConnTimeout = %v, want 90s", tr.IdleConnTimeout)
	}
	if tr.ResponseHeaderTimeout != 120*time.Second {
		t.Fatalf("ResponseHeaderTimeout = %v, want 120s", tr.ResponseHeaderTimeout)
	}
}

func TestGatewayClient_NoTotalTimeoutByDefault_SetterApplies(t *testing.T) {
	g := NewGatewayClient("http://gw", "k", "", time.Minute, "")
	if g.http.Timeout != 0 {
		t.Fatalf("default Client.Timeout = %v, want 0 (unbounded — streaming safe)", g.http.Timeout)
	}
	g.SetTotalTimeout(90 * time.Second)
	if g.http.Timeout != 90*time.Second {
		t.Fatalf("SetTotalTimeout not applied, got %v", g.http.Timeout)
	}
}

func TestGatewayClient_ReusesConnectionAcrossRequests(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()

	g := NewGatewayClient(srv.URL, "k", "", time.Minute, "")

	dials := 0
	trace := &httptrace.ClientTrace{
		ConnectDone: func(network, addr string, err error) { dials++ },
	}
	ctx := httptrace.WithClientTrace(context.Background(), trace)

	for i := 0; i < 3; i++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := g.http.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		// Drain before close — a connection only returns to the
		// keepalive pool once its body is fully consumed.
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}

	// Sequential requests over a keepalive pool dial once. The old
	// default-transport behavior was safe for sequential too, but this
	// pins the pool wiring end-to-end (a broken Transport field would
	// dial 3 times).
	if dials != 1 {
		t.Fatalf("dialed %d times across 3 requests, want 1 (keepalive reuse)", dials)
	}
}
