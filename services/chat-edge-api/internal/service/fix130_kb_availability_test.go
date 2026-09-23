package service

// FIX130 C2 — the knowledge availability verdict follows what the
// embeddings provider actually does, and asks it at most once per window.

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestFIX130_KBAvailabilityFollowsTheNewestEmbeddingOutcome(t *testing.T) {
	var probes int32
	a := NewKnowledgeAvailability(func(context.Context, uuid.UUID, uuid.UUID) error {
		atomic.AddInt32(&probes, 1)
		return nil
	})
	// An upload whose embedding the gateway refused (the live shape:
	// OpenAI out of credit → the gateway's 503).
	a.Observe(&GatewayUnavailableError{Status: 503, Body: `{"detail":"embeddings provider returned 429"}`})
	v := a.Status(context.Background(), uuid.New(), uuid.New())
	if v.Available || v.Reason != KBUnavailableProviderFailed {
		t.Fatalf("a refused embedding makes KBs unavailable, got %+v", v)
	}
	if probes != 0 {
		t.Fatal("a fresh observation answers without a probe")
	}
	// No key at all.
	a.Observe(&GatewayUnavailableError{Status: 503, Body: `{"detail":"embeddings provider not configured — add an OpenAI key"}`})
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Reason != KBUnavailableNotConfigured {
		t.Fatalf("want not-configured, got %+v", v)
	}
	// A 403 about THIS text (the DLP credential block) says nothing about
	// the provider; a cancelled request neither.
	a.Observe(&GatewayUnavailableError{Status: 403, Body: `{"error":{"code":"raw_egress_blocked"}}`})
	a.Observe(context.Canceled)
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Reason != KBUnavailableNotConfigured {
		t.Fatalf("input-specific refusals must not change the verdict, got %+v", v)
	}
	// The key is fixed: the next real success flips it back at once.
	a.Observe(nil)
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); !v.Available {
		t.Fatalf("a success makes KBs available, got %+v", v)
	}
}

func TestFIX130_KBAvailabilityProbesOnceAndCaches(t *testing.T) {
	var probes int32
	release := make(chan struct{})
	a := NewKnowledgeAvailability(func(context.Context, uuid.UUID, uuid.UUID) error {
		atomic.AddInt32(&probes, 1)
		<-release
		return &GatewayUnavailableError{Status: 503, Body: "embeddings provider returned 429"}
	})
	var wg sync.WaitGroup
	results := make([]KBAvailability, 8)
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i] = a.Status(context.Background(), uuid.New(), uuid.New())
		}(i)
	}
	time.Sleep(30 * time.Millisecond)
	close(release)
	wg.Wait()
	if n := atomic.LoadInt32(&probes); n != 1 {
		t.Fatalf("8 concurrent askers must share one probe, got %d", n)
	}
	for _, r := range results {
		if r.Available || r.Reason != KBUnavailableProviderFailed {
			t.Fatalf("every asker gets the probe's verdict, got %+v", r)
		}
	}
	// Cached inside the failure window...
	a.Status(context.Background(), uuid.New(), uuid.New())
	if n := atomic.LoadInt32(&probes); n != 1 {
		t.Fatalf("a fresh verdict is served from cache, got %d probes", n)
	}
	// ...and asked again once it is stale.
	old := availabilityFailTTL
	availabilityFailTTL = 0
	defer func() { availabilityFailTTL = old }()
	a.Status(context.Background(), uuid.New(), uuid.New())
	if n := atomic.LoadInt32(&probes); n != 2 {
		t.Fatalf("a stale verdict is re-checked, got %d probes", n)
	}
}

func TestFIX130_EmbeddingCallsFeedTheVerdict(t *testing.T) {
	// The real wiring: EmbeddingService → gateway /embeddings → observer.
	status := int32(http.StatusServiceUnavailable)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s := atomic.LoadInt32(&status); s != http.StatusOK {
			w.WriteHeader(int(s))
			_, _ = w.Write([]byte(`{"error":{"code":"provider_unavailable","message":"embeddings provider returned 429"}}`))
			return
		}
		_, _ = w.Write([]byte(`{"data":[{"embedding":[0.1,0.2]}]}`))
	}))
	defer srv.Close()
	emb := NewEmbeddingService(NewGatewayClient(srv.URL, "k", "", time.Minute, ""))
	a := NewKnowledgeAvailability(emb.Probe)
	emb.SetObserver(a.Observe)

	_, err := emb.GenerateEmbedding(context.Background(), embeddingCallCtx(uuid.New(), uuid.New()), "a chunk of an uploaded document")
	var ue *GatewayUnavailableError
	if !errors.As(err, &ue) {
		t.Fatalf("want the gateway refusal, got %v", err)
	}
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Available {
		t.Fatalf("the failed upload's embedding must make KBs unavailable, got %+v", v)
	}
	atomic.StoreInt32(&status, http.StatusOK)
	if _, err := emb.GenerateEmbedding(context.Background(), embeddingCallCtx(uuid.New(), uuid.New()), "a question"); err != nil {
		t.Fatal(err)
	}
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); !v.Available {
		t.Fatalf("a working embedding makes KBs available again, got %+v", v)
	}
}
