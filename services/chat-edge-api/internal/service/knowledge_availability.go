package service

// FIX130 C2 — "can a knowledge base work right now?", answered by the
// server so the clients can stop offering one that cannot.
//
// A knowledge base is only as good as the embeddings behind it: every
// document is embedded when it is uploaded, and every question is embedded
// to search it. The gateway serves embeddings from ONE provider (OpenAI,
// backend openai_embeddings_client.py) and answers 503 when that provider has
// no key or refuses. NIGHT2 F27 / the FIX130 test stack: create a KB → 201;
// upload → 201 with status "failed" ("embedding failed: gateway unavailable:
// status=503"); and the web offered all of it anyway — a composer button, a
// project dropdown and a sidebar entry leading to a feature that could not
// work, with RAG failing silently at answer time.
//
// The verdict comes from what actually happened: every embedding call
// chat-edge makes (an upload, a reindex, a RAG query) is observed, and the
// newest outcome is the answer. When nothing has been observed recently a
// single tiny probe is made (one short string — a fraction of a cent when the
// provider works, free when it refuses), single-flight, and cached: a
// success for availabilityOKTTL, a failure for availabilityFailTTL so a key
// being fixed is noticed within minutes.

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

// Reasons a knowledge base is unavailable — a closed set the clients map to
// their own sentence.
const (
	// KBUnavailableNotConfigured — no embeddings provider is set up at all.
	KBUnavailableNotConfigured = "embeddings_not_configured"
	// KBUnavailableProviderFailed — one is set up, and it refuses or fails
	// (out of credit, invalid key, unreachable).
	KBUnavailableProviderFailed = "embeddings_failed"
)

// KBAvailability is the answer the endpoint serves.
type KBAvailability struct {
	Available bool      `json:"available"`
	Reason    string    `json:"reason,omitempty"`
	CheckedAt time.Time `json:"checked_at"`
}

var (
	availabilityOKTTL    = 10 * time.Minute
	availabilityFailTTL  = 2 * time.Minute
	availabilityProbeMax = 10 * time.Second
)

// KnowledgeAvailability keeps the newest embedding outcome.
type KnowledgeAvailability struct {
	probe func(ctx context.Context, tenantID, userID uuid.UUID) error

	mu      sync.Mutex
	known   bool
	last    KBAvailability
	probing chan struct{} // non-nil while a probe runs; closed when it ends
}

// NewKnowledgeAvailability wires the probe (one embedding of a short string).
func NewKnowledgeAvailability(probe func(ctx context.Context, tenantID, userID uuid.UUID) error) *KnowledgeAvailability {
	return &KnowledgeAvailability{probe: probe}
}

// Observe records a real embedding outcome (nil = it worked). Errors that
// say nothing about the provider — a cancelled request, an empty input —
// are ignored.
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
// on behalf of the asking person (concurrent callers wait on the same probe).
func (a *KnowledgeAvailability) Status(ctx context.Context, tenantID, userID uuid.UUID) KBAvailability {
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
				return KBAvailability{Available: false, Reason: KBUnavailableProviderFailed, CheckedAt: time.Now().UTC()}
			}
		}
		done := make(chan struct{})
		a.probing = done
		a.mu.Unlock()

		pctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), availabilityProbeMax)
		err := a.probe(pctx, tenantID, userID)
		cancel()
		v, ok := availabilityFrom(err)
		if !ok { // the probe itself could not say: count it as a failure, briefly
			v = KBAvailability{Available: false, Reason: KBUnavailableProviderFailed, CheckedAt: time.Now().UTC()}
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
	if v.Available {
		ttl = availabilityOKTTL
	}
	return time.Since(v.CheckedAt) < ttl
}

// availabilityFrom turns an embedding outcome into a verdict; false when
// the outcome says nothing about the provider.
func availabilityFrom(err error) (KBAvailability, bool) {
	now := time.Now().UTC()
	if err == nil {
		return KBAvailability{Available: true, CheckedAt: now}, true
	}
	if errors.Is(err, context.Canceled) {
		return KBAvailability{}, false
	}
	if errors.Is(err, ErrEmbeddingDisabled) {
		return KBAvailability{Available: false, Reason: KBUnavailableNotConfigured, CheckedAt: now}, true
	}
	var ue *GatewayUnavailableError
	if errors.As(err, &ue) {
		// The gateway's 503 names the missing key in its detail
		// (openai_embeddings_client.py: "embeddings provider not configured").
		if strings.Contains(strings.ToLower(ue.Body), "not configured") {
			return KBAvailability{Available: false, Reason: KBUnavailableNotConfigured, CheckedAt: now}, true
		}
		if ue.Status == 0 || ue.Status >= 500 || ue.Status == 429 || ue.Status == 401 || ue.Status == 402 {
			return KBAvailability{Available: false, Reason: KBUnavailableProviderFailed, CheckedAt: now}, true
		}
		// A 4xx about THIS input (the DLP credential block, a too-long
		// text) is not a verdict on the provider.
		return KBAvailability{}, false
	}
	if isTimeout(err) || isDisconnect(err) {
		return KBAvailability{Available: false, Reason: KBUnavailableProviderFailed, CheckedAt: now}, true
	}
	return KBAvailability{}, false
}
