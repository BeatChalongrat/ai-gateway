package service

// FIX130 C2 — "can a knowledge base work right now?", answered by the
// server so the clients can stop offering one that cannot.
//
// FIX132 B — the answer now comes from the knowledge base's own models
// (kb-models, on this machine): ready → available; still loading its
// weights → models_loading; down, failed or not configured →
// models_unavailable. There is no provider key or credit involved any more.
//
// The verdict comes from what actually happened: every embeddings call
// chat-edge makes (indexing, a question) is observed, and the newest outcome
// is the answer. When nothing has been observed recently one GET /health is
// made, single-flight, and cached: a success for availabilityOKTTL, a
// failure for availabilityFailTTL, "loading" for availabilityLoadingTTL (it
// becomes ready by itself within minutes of a start).

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/google/uuid"
)

// Reasons a knowledge base is unavailable — a closed set the clients map to
// their own sentence.
const (
	// KBUnavailableLoading — kb-models is starting and still loading its
	// models; it becomes available by itself.
	KBUnavailableLoading = "models_loading"
	// KBUnavailableModels — kb-models is not configured, not reachable, or
	// failed to load its models.
	KBUnavailableModels = "models_unavailable"
)

// KBAvailability is the answer the endpoint serves.
type KBAvailability struct {
	Available bool      `json:"available"`
	Reason    string    `json:"reason,omitempty"`
	CheckedAt time.Time `json:"checked_at"`
}

var (
	availabilityOKTTL      = 10 * time.Minute
	availabilityFailTTL    = 2 * time.Minute
	availabilityLoadingTTL = 20 * time.Second
	availabilityProbeMax   = 10 * time.Second
)

// KnowledgeAvailability keeps the newest model outcome.
type KnowledgeAvailability struct {
	probe func(ctx context.Context) error

	mu      sync.Mutex
	known   bool
	last    KBAvailability
	probing chan struct{} // non-nil while a probe runs; closed when it ends
}

// NewKnowledgeAvailability wires the probe (kb-models GET /health).
func NewKnowledgeAvailability(probe func(ctx context.Context) error) *KnowledgeAvailability {
	return &KnowledgeAvailability{probe: probe}
}

// Observe records a real model outcome (nil = it worked). Errors that say
// nothing about kb-models — a cancelled request, a refused input — are
// ignored.
func (a *KnowledgeAvailability) Observe(err error) {
	v, ok := availabilityFrom(err)
	if !ok {
		return
	}
	a.mu.Lock()
	a.known, a.last = true, v
	a.mu.Unlock()
}

// Status answers from the newest outcome while it is fresh, else probes once
// (concurrent callers wait on the same probe). The ids are the asker's; the
// probe does not need them (the models are the same for everyone).
func (a *KnowledgeAvailability) Status(ctx context.Context, _, _ uuid.UUID) KBAvailability {
	for {
		a.mu.Lock()
		if a.known && fresh(a.last) {
			v := a.last
			a.mu.Unlock()
			return v
		}
		if wait := a.probing; wait != nil {
			a.mu.Unlock()
			select {
			case <-wait:
				continue
			case <-ctx.Done():
				return KBAvailability{Available: false, Reason: KBUnavailableModels, CheckedAt: time.Now().UTC()}
			}
		}
		done := make(chan struct{})
		a.probing = done
		a.mu.Unlock()

		pctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), availabilityProbeMax)
		err := a.probe(pctx)
		cancel()
		v, ok := availabilityFrom(err)
		if !ok { // the probe itself could not say: count it as a failure, briefly
			v = KBAvailability{Available: false, Reason: KBUnavailableModels, CheckedAt: time.Now().UTC()}
		}
		a.mu.Lock()
		a.known, a.last, a.probing = true, v, nil
		a.mu.Unlock()
		close(done)
		return v
	}
}

func fresh(v KBAvailability) bool {
	ttl := availabilityFailTTL
	switch {
	case v.Available:
		ttl = availabilityOKTTL
	case v.Reason == KBUnavailableLoading:
		ttl = availabilityLoadingTTL
	}
	return time.Since(v.CheckedAt) < ttl
}

// availabilityFrom turns a model outcome into a verdict; false when the
// outcome says nothing about kb-models.
func availabilityFrom(err error) (KBAvailability, bool) {
	now := time.Now().UTC()
	if err == nil {
		return KBAvailability{Available: true, CheckedAt: now}, true
	}
	if errors.Is(err, context.Canceled) {
		return KBAvailability{}, false
	}
	if IsKBModelsLoading(err) {
		return KBAvailability{Available: false, Reason: KBUnavailableLoading, CheckedAt: now}, true
	}
	if IsKBModelsUnavailable(err) {
		return KBAvailability{Available: false, Reason: KBUnavailableModels, CheckedAt: now}, true
	}
	if isTimeout(err) || isDisconnect(err) {
		return KBAvailability{Available: false, Reason: KBUnavailableModels, CheckedAt: now}, true
	}
	// A 4xx about THIS input (an empty text) is not a verdict on kb-models.
	return KBAvailability{}, false
}
