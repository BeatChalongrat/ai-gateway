package service

// FIX130 C2 — the knowledge availability verdict follows what the models
// actually do, and asks at most once per window. FIX132 B — the models are
// kb-models on this machine: ready / models_loading / models_unavailable.

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
)

func TestFIX130_KBAvailabilityFollowsTheNewestModelOutcome(t *testing.T) {
	var probes int32
	a := NewKnowledgeAvailability(func(context.Context) error {
		atomic.AddInt32(&probes, 1)
		return nil
	})
	// An upload whose embeddings kb-models could not serve (down).
	a.Observe(&KBModelsError{Op: "embeddings", Err: context.DeadlineExceeded})
	v := a.Status(context.Background(), uuid.New(), uuid.New())
	if v.Available || v.Reason != KBUnavailableModels {
		t.Fatalf("a failed model call makes KBs unavailable, got %+v", v)
	}
	if probes != 0 {
		t.Fatal("a fresh observation answers without a probe")
	}
	// Still loading its weights.
	a.Observe(&KBModelsError{Op: "embeddings", Status: 503, Body: `{"detail":"models loading"}`})
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Reason != KBUnavailableLoading {
		t.Fatalf("want models_loading, got %+v", v)
	}
	// A 400 about THIS text says nothing about kb-models; a cancelled
	// request neither.
	a.Observe(&KBModelsError{Op: "embeddings", Status: 400, Body: `{"detail":"empty input item"}`})
	a.Observe(context.Canceled)
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Reason != KBUnavailableLoading {
		t.Fatalf("input-specific refusals must not change the verdict, got %+v", v)
	}
	// The models are up: the next real success flips it back at once.
	a.Observe(nil)
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); !v.Available {
		t.Fatalf("a success makes KBs available, got %+v", v)
	}
}

func TestFIX130_KBAvailabilityProbesOnceAndCaches(t *testing.T) {
	var probes int32
	release := make(chan struct{})
	a := NewKnowledgeAvailability(func(context.Context) error {
		atomic.AddInt32(&probes, 1)
		<-release
		return &KBModelsError{Op: "health", Err: context.DeadlineExceeded}
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
		if r.Available || r.Reason != KBUnavailableModels {
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

func TestFIX132_KBAvailabilityFromKBModelsHealthAndCalls(t *testing.T) {
	// The real wiring: KBModelsClient → kb-models /health + /v1/embeddings
	// → observer.
	fake := kbfake.New()
	defer fake.Close()
	fake.SetStatus("loading")
	c, err := NewKBModelsClient(fake.URL)
	if err != nil {
		t.Fatal(err)
	}
	a := NewKnowledgeAvailability(c.Probe)
	c.SetObserver(a.Observe)

	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Available || v.Reason != KBUnavailableLoading {
		t.Fatalf("loading weights → models_loading, got %+v", v)
	}
	fake.SetStatus("ready")
	if _, err := c.Embed(context.Background(), []string{"a question"}, false, KBPriorityQuery); err != nil {
		t.Fatal(err)
	}
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); !v.Available {
		t.Fatalf("a working embeddings call makes KBs available, got %+v", v)
	}
	fake.Close() // kb-models goes away
	if _, err := c.Embed(context.Background(), []string{"another"}, false, KBPriorityQuery); err == nil {
		t.Fatal("want an error from a closed kb-models")
	}
	if v := a.Status(context.Background(), uuid.New(), uuid.New()); v.Available || v.Reason != KBUnavailableModels {
		t.Fatalf("an unreachable kb-models → models_unavailable, got %+v", v)
	}

	// Not configured at all (nil client) → models_unavailable, no call made.
	var none *KBModelsClient
	b := NewKnowledgeAvailability(none.Probe)
	if v := b.Status(context.Background(), uuid.New(), uuid.New()); v.Available || v.Reason != KBUnavailableModels {
		t.Fatalf("no kb-models → models_unavailable, got %+v", v)
	}
}
